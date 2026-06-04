# Copyright (C) 2026 Qualcomm Technologies, Inc.
#
# Author: Matt Hart <matthart@qti.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the v0.3 REST API additions."""
from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from lava_scheduler_app.models import (
    Device,
    DeviceType,
    JobFailureTag,
    TestJob,
    TestJobUser,
    Worker,
)
from linaro_django_xmlrpc.models import AuthToken

EXAMPLE_JOB = """
device_type: qemu
job_name: test
visibility: public
timeouts:
  job: {minutes: 10}
  action: {minutes: 5}
actions: []
protocols: {}
"""


def _client_for(user: User) -> APIClient:
    token = AuthToken.objects.create(user=user, secret=f"key-{user.username}")  # nosec
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Token " + token.secret)
    return c


@pytest.mark.django_db
class TestV03:
    """The v0.3 router is mounted at /api/v0.3/. We hit it directly here."""

    version = "v0.3"

    @pytest.fixture(autouse=True)
    def fixtures(self, db):
        self.admin = User.objects.create(username="adm", is_superuser=True)
        self.user = User.objects.create(username="alice")
        self.other = User.objects.create(username="bob")
        for u in (self.admin, self.user, self.other):
            u.set_password("x")
            u.save()

        self.admin_c = _client_for(self.admin)
        self.user_c = _client_for(self.user)
        self.other_c = _client_for(self.other)

        self.worker = Worker.objects.create(
            hostname="w1", state=Worker.STATE_ONLINE, health=Worker.HEALTH_ACTIVE
        )
        self.dtype = DeviceType.objects.create(name="qemu")
        self.device = Device.objects.create(
            hostname="qemu01", device_type=self.dtype, worker_host=self.worker,
            state=Device.STATE_RUNNING, health=Device.HEALTH_GOOD,
        )
        Device.objects.create(
            hostname="qemu02", device_type=self.dtype, worker_host=self.worker,
            state=Device.STATE_RESERVED, health=Device.HEALTH_GOOD,
        )

        self.submitted_job = TestJob.objects.create(
            definition=EXAMPLE_JOB,
            submitter=self.user,
            requested_device_type=self.dtype,
            state=TestJob.STATE_SUBMITTED,
            priority=TestJob.MEDIUM,
            is_public=True,
        )
        self.canceling_job = TestJob.objects.create(
            definition=EXAMPLE_JOB,
            submitter=self.user,
            requested_device_type=self.dtype,
            state=TestJob.STATE_CANCELING,
            priority=TestJob.MEDIUM,
            is_public=True,
        )
        self.finished_job = TestJob.objects.create(
            definition=EXAMPLE_JOB,
            submitter=self.user,
            requested_device_type=self.dtype,
            state=TestJob.STATE_FINISHED,
            health=TestJob.HEALTH_INCOMPLETE,
            priority=TestJob.MEDIUM,
            end_time=timezone.now(),
            is_public=True,
        )

        self.failure_tag = JobFailureTag.objects.create(
            name="flaky", description="known-flaky test"
        )

    # ------------- job action endpoints -------------

    def test_priority_change(self):
        url = reverse("api-root", args=[self.version]) + f"jobs/{self.submitted_job.id}/priority/"
        r = self.user_c.post(url, {"priority": TestJob.HIGH}, format="json")
        assert r.status_code == 200, r.content
        self.submitted_job.refresh_from_db()
        assert self.submitted_job.priority == TestJob.HIGH

    def test_priority_rejects_out_of_range(self):
        url = reverse("api-root", args=[self.version]) + f"jobs/{self.submitted_job.id}/priority/"
        r = self.user_c.post(url, {"priority": 999}, format="json")
        assert r.status_code == 400

    def test_priority_requires_change_perm(self):
        # Stranger cannot change priority on someone else's job.
        url = reverse("api-root", args=[self.version]) + f"jobs/{self.submitted_job.id}/priority/"
        r = self.other_c.post(url, {"priority": TestJob.HIGH}, format="json")
        assert r.status_code == 403

    def test_fail_requires_superuser(self):
        url = reverse("api-root", args=[self.version]) + f"jobs/{self.canceling_job.id}/fail/"
        r = self.user_c.post(url)
        assert r.status_code == 403

    def test_fail_requires_canceling_state(self):
        url = reverse("api-root", args=[self.version]) + f"jobs/{self.submitted_job.id}/fail/"
        r = self.admin_c.post(url)
        assert r.status_code == 400

    def test_fail_succeeds_in_canceling_state(self):
        url = reverse("api-root", args=[self.version]) + f"jobs/{self.canceling_job.id}/fail/"
        r = self.admin_c.post(url)
        assert r.status_code == 200, r.content
        self.canceling_job.refresh_from_db()
        assert self.canceling_job.state == TestJob.STATE_FINISHED
        # go_state_finished() from STATE_CANCELING coerces health to CANCELED
        # regardless of the requested health value — mirrors the HTML view.
        assert self.canceling_job.health == TestJob.HEALTH_CANCELED

    def test_favorite_toggle_is_idempotent_per_call(self):
        url = reverse("api-root", args=[self.version]) + f"jobs/{self.submitted_job.id}/favorite/"
        r = self.user_c.post(url)
        assert r.status_code == 200
        assert r.json()["is_favorite"] is True
        r = self.user_c.post(url)
        assert r.json()["is_favorite"] is False

    def test_favorite_requires_auth(self):
        url = reverse("api-root", args=[self.version]) + f"jobs/{self.submitted_job.id}/favorite/"
        r = APIClient().post(url)
        assert r.status_code in (401, 403)

    def test_annotate_get_and_post(self):
        url = reverse("api-root", args=[self.version]) + f"jobs/{self.finished_job.id}/annotate/"
        r = self.user_c.get(url)
        assert r.status_code == 200
        r = self.user_c.post(
            url,
            {"failure_tags": [self.failure_tag.id], "failure_comment": "looks flaky"},
            format="json",
        )
        assert r.status_code == 200, r.content
        self.finished_job.refresh_from_db()
        assert self.finished_job.failure_comment == "looks flaky"
        assert self.failure_tag in self.finished_job.failure_tags.all()

    def test_favorites_collection(self):
        TestJobUser.objects.create(
            user=self.user, test_job=self.submitted_job, is_favorite=True
        )
        url = reverse("api-root", args=[self.version]) + "jobs/favorites/"
        r = self.user_c.get(url)
        assert r.status_code == 200, r.content
        body = r.json()
        results = body["results"] if isinstance(body, dict) else body
        ids = [j["id"] for j in results]
        assert self.submitted_job.id in ids
        assert self.finished_job.id not in ids

    # ------------- dashboard endpoints -------------

    def test_dashboard_queue(self):
        url = reverse("api-root", args=[self.version]) + "dashboard/queue/"
        r = self.user_c.get(url)
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["count"] >= 1
        ids = [j["id"] for j in body["results"]]
        assert self.submitted_job.id in ids
        assert self.finished_job.id not in ids

    def test_dashboard_running(self):
        url = reverse("api-root", args=[self.version]) + "dashboard/running/"
        r = self.user_c.get(url)
        assert r.status_code == 200, r.content
        body = r.json()
        names = {row["name"] for row in body}
        assert "qemu" in names

    def test_dashboard_lab_health(self):
        url = reverse("api-root", args=[self.version]) + "dashboard/lab-health/"
        r = self.user_c.get(url)
        assert r.status_code == 200, r.content
        hostnames = [row["hostname"] for row in r.json()]
        assert "qemu01" in hostnames
        assert "qemu02" in hostnames

    # ------------- failure tags -------------

    def test_failure_tags_list(self):
        url = reverse("api-root", args=[self.version]) + "failure-tags/"
        r = self.user_c.get(url)
        assert r.status_code == 200
        results = r.json()["results"] if isinstance(r.json(), dict) else r.json()
        assert any(t["name"] == "flaky" for t in results)

    # ------------- SSE smoke -------------

    def test_logs_stream_returns_event_stream(self):
        url = (
            reverse("api-root", args=[self.version])
            + f"jobs/{self.finished_job.id}/logs/stream/?max_seconds=1&poll_interval=1"
        )
        r = self.user_c.get(url)
        # StreamingHttpResponse is OK status; content may be empty if no log file.
        assert r.status_code == 200, r.content
        assert r["Content-Type"].startswith("text/event-stream")
        # Drain the stream so the generator runs (otherwise it never ticks).
        body = b"".join(r.streaming_content) if hasattr(r, "streaming_content") else b""
        # We don't assert on content because the job has no on-disk log here,
        # but the stream MUST terminate (finished job => end event).
        assert b"event:" in body or body == b""
