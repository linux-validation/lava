# Copyright (C) 2026 Qualcomm Technologies, Inc.
#
# Author: Matt Hart <matthart@qti.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
LAVA REST API v0.3.

v0.3 is a strict superset of v0.2: every v0.2 endpoint is re-exposed here, and
v0.3 adds the operations that used to be HTML-only — job priority/annotate/
favorite/fail, queue / running / lab-health dashboards, NotificationCallback
CRUD, per-user settings, and an SSE log-tail endpoint.
"""

from __future__ import annotations

import time

from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.db.models import Count, Exists, IntegerField, OuterRef, Q, Subquery, Value
from django.http import Http404, StreamingHttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from lava_rest_app.v02 import views as v02_views

# Re-export v0.2 ViewSets so v0.3 router can register them unchanged.
AliasViewSet = v02_views.AliasViewSet
DeviceViewSet = v02_views.DeviceViewSet
DeviceTypeViewSet = v02_views.DeviceTypeViewSet
GroupDevicePermissionViewSet = v02_views.GroupDevicePermissionViewSet
GroupDeviceTypePermissionViewSet = v02_views.GroupDeviceTypePermissionViewSet
GroupViewSet = v02_views.GroupViewSet
RemoteArtifactTokenViewSet = v02_views.RemoteArtifactTokenViewSet
SystemViewSet = v02_views.SystemViewSet
TagViewSet = v02_views.TagViewSet
TestCaseViewSet = v02_views.TestCaseViewSet
TestSuiteViewSet = v02_views.TestSuiteViewSet
UserViewSet = v02_views.UserViewSet
WorkerViewSet = v02_views.WorkerViewSet

from lava_scheduler_app.logutils import logs_instance
from lava_scheduler_app.models import (
    Device,
    DeviceType,
    ExtendedUser,
    JobFailureTag,
    NotificationCallback,
    TestJob,
    TestJobUser,
)

from . import serializers


class TestJobViewSet(v02_views.TestJobViewSet):
    """v0.2 TestJobViewSet plus the missing HTML-only actions."""

    @action(methods=("post",), detail=True, suffix="priority")
    def priority(self, request, **kwargs):
        with transaction.atomic():
            job = TestJob.get_restricted_job(kwargs["pk"], request.user, for_update=True)
            if not job.can_change_priority(request.user):
                raise PermissionDenied("Cannot change priority on this job.")

            serializer = serializers.TestJobPrioritySerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            new_priority = serializer.validated_data["priority"]

            if job.priority != new_priority:
                job.priority = new_priority
                job.save(update_fields=["priority"])

        return Response({"id": job.id, "priority": job.priority})

    @action(methods=("post",), detail=True, suffix="fail")
    def fail(self, request, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Only superusers can fail a job.")

        with transaction.atomic():
            job = TestJob.get_restricted_job(kwargs["pk"], request.user, for_update=True)
            if job.state != TestJob.STATE_CANCELING:
                raise ValidationError(
                    {"state": "Job must be in Canceling state before being failed."}
                )
            fields = job.go_state_finished(TestJob.HEALTH_INCOMPLETE)
            job.save(update_fields=fields)

        return Response(
            {
                "id": job.id,
                "state": job.get_state_display(),
                "health": job.get_health_display(),
            }
        )

    @action(methods=("post",), detail=True, suffix="favorite")
    def favorite(self, request, **kwargs):
        if isinstance(request.user, AnonymousUser):
            raise PermissionDenied("Authentication required.")

        # No permission check on view here — matches the HTML behaviour
        # (any authenticated user can favorite any job they can fetch).
        try:
            job = TestJob.objects.get(pk=kwargs["pk"])
        except TestJob.DoesNotExist:
            raise NotFound()

        tju, _ = TestJobUser.objects.get_or_create(user=request.user, test_job=job)
        tju.is_favorite = not tju.is_favorite
        tju.save(update_fields=["is_favorite"])
        return Response({"id": job.id, "is_favorite": tju.is_favorite})

    @action(methods=("get", "post"), detail=True, suffix="annotate")
    def annotate(self, request, **kwargs):
        job = TestJob.get_restricted_job(kwargs["pk"], request.user)
        if not job.can_annotate(request.user):
            raise PermissionDenied("Cannot annotate this job.")

        if request.method == "GET":
            serializer = serializers.TestJobAnnotationSerializer(instance=job)
            return Response(serializer.data)

        serializer = serializers.TestJobAnnotationSerializer(
            instance=job, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=False, suffix="favorites")
    def favorites(self, request, **kwargs):
        """List the current user's favorite jobs."""
        if isinstance(request.user, AnonymousUser):
            raise PermissionDenied("Authentication required.")

        qs = (
            self.get_queryset()
            .filter(testjobuser__user=request.user, testjobuser__is_favorite=True)
            .order_by("-id")
        )
        page = self.paginate_queryset(qs)
        serializer = self.get_serializer(page if page is not None else qs, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, url_path="logs/stream", suffix="logs-stream")
    def logs_stream(self, request, **kwargs):
        """
        Server-Sent Events log tail.

        Query params:
            start: line number to start streaming from (default 0). The
                cursor exposed via the end and tick events is the same
                line-number semantic — pass it back verbatim when
                reconnecting.
            max_seconds: hard cap on connection duration (default 55s,
                ceiling 300s) — clients reconnect from the new cursor.
            poll_interval: seconds between disk re-reads while job is
                still running (default 1s, floor 0.1s, ceiling 10s).

        Events:
            event: log     data: <raw yaml chunk>
            event: end     data: <final line cursor>   (sent when job finishes)
            event: tick    data: <current line cursor> (heartbeat)
        """
        job = TestJob.get_restricted_job(kwargs["pk"], request.user)

        try:
            start = max(0, int(request.query_params.get("start", 0)))
        except (TypeError, ValueError):
            start = 0
        try:
            max_seconds = min(300, max(1, int(request.query_params.get("max_seconds", 55))))
        except (TypeError, ValueError):
            max_seconds = 55
        try:
            poll_interval = min(
                10.0, max(0.1, float(request.query_params.get("poll_interval", 1)))
            )
        except (TypeError, ValueError):
            poll_interval = 1.0

        def event_stream():
            # cursor is a *line index* into output.yaml — `logs_instance.read()`
            # interprets its start/end args as positions in output.idx, not
            # byte offsets. We track lines to match.
            cursor = start
            deadline = time.monotonic() + max_seconds
            while True:
                try:
                    total_lines = logs_instance.line_count(job)
                except FileNotFoundError:
                    total_lines = 0

                if total_lines > cursor:
                    try:
                        chunk = logs_instance.read(job, cursor, total_lines)
                    except FileNotFoundError:
                        chunk = ""
                    if chunk:
                        # SSE: every newline in data must be re-prefixed "data:".
                        yield "event: log\n"
                        for line in chunk.splitlines():
                            yield f"data: {line}\n"
                        yield "\n"
                        cursor = total_lines

                job.refresh_from_db(fields=["state"])
                if job.state == TestJob.STATE_FINISHED:
                    yield f"event: end\ndata: {cursor}\n\n"
                    return

                if time.monotonic() >= deadline:
                    yield f"event: tick\ndata: {cursor}\n\n"
                    return

                yield f"event: tick\ndata: {cursor}\n\n"
                time.sleep(poll_interval)

        response = StreamingHttpResponse(
            event_stream(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class DashboardViewSet(viewsets.ViewSet):
    """Aggregations the old HTML dashboards used to render server-side.

    Read-only and anonymous-friendly: every underlying queryset is
    `visible_by_user(request.user)` filtered, so anonymous visitors only
    ever see public devices, public device-types, and public jobs.
    """

    permission_classes = (AllowAny,)

    @action(detail=False)
    def queue(self, request, **kwargs):
        """Submitted (not yet scheduled) jobs visible to this user."""
        qs = (
            TestJob.objects.visible_by_user(request.user)
            .filter(state=TestJob.STATE_SUBMITTED)
            .select_related("submitter", "requested_device_type")
            .order_by("-priority", "submit_time")
        )
        serializer = serializers.QueueItemSerializer(qs[:500], many=True)
        return Response({"count": qs.count(), "results": serializer.data})

    @action(detail=False)
    def running(self, request, **kwargs):
        """Per-device-type aggregates of running/reserved devices and jobs."""
        reserved = Subquery(
            Device.objects.filter(
                device_type=OuterRef("pk"), state=Device.STATE_RESERVED
            )
            .annotate(_g=Value(1))
            .values("_g")
            .annotate(c=Count("*"))
            .values("c"),
            output_field=IntegerField(),
        )
        running = Subquery(
            Device.objects.filter(
                device_type=OuterRef("pk"), state=Device.STATE_RUNNING
            )
            .annotate(_g=Value(1))
            .values("_g")
            .annotate(c=Count("*"))
            .values("c"),
            output_field=IntegerField(),
        )
        running_jobs = Subquery(
            TestJob.objects.filter(
                state=TestJob.STATE_RUNNING,
                requested_device_type=OuterRef("pk"),
            )
            .annotate(_g=Value(1))
            .values("_g")
            .annotate(c=Count("*"))
            .values("c"),
            output_field=IntegerField(),
        )

        qs = (
            DeviceType.objects.filter(display=True)
            .visible_by_user(request.user)
            .annotate(
                reserved_devices=reserved,
                running_devices=running,
                running_jobs=running_jobs,
            )
            .order_by("name")
        )
        serializer = serializers.RunningDeviceTypeSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=False, url_path="lab-health")
    def lab_health(self, request, **kwargs):
        """Per-device health summary across the lab."""
        qs = (
            Device.objects.visible_by_user(request.user)
            .exclude(health=Device.HEALTH_RETIRED)
            .select_related("device_type", "worker_host", "last_health_report_job")
            .order_by("hostname")
        )
        serializer = serializers.LabHealthDeviceSerializer(qs, many=True)
        return Response(serializer.data)


class NotificationCallbackViewSet(viewsets.ModelViewSet):
    """CRUD over the NotificationCallback model."""

    queryset = NotificationCallback.objects.select_related(
        "notification", "notification__test_job"
    )
    serializer_class = serializers.NotificationCallbackSerializer
    permission_classes = (IsAuthenticated,)
    filterset_fields = ("notification", "method", "dataset", "content_type")

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        # Restrict to callbacks whose job is visible to the user.
        return self.queryset.filter(
            notification__test_job__in=TestJob.objects.visible_by_user(user)
        )


class JobFailureTagViewSet(viewsets.ModelViewSet):
    queryset = JobFailureTag.objects.all()
    serializer_class = serializers.JobFailureTagSerializer
    permission_classes = (IsAuthenticated,)
    filterset_fields = ("name",)


class MeSettingsView(APIView):
    """GET / PATCH the authenticated user's ExtendedUser preferences."""

    permission_classes = (IsAuthenticated,)

    def _profile(self, user):
        profile, _ = ExtendedUser.objects.get_or_create(user=user)
        return profile

    def get(self, request, version=None, **kwargs):
        profile = self._profile(request.user)
        serializer = serializers.UserSettingsSerializer(profile)
        return Response(serializer.data)

    def patch(self, request, version=None, **kwargs):
        profile = self._profile(request.user)
        serializer = serializers.UserSettingsSerializer(
            profile, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
