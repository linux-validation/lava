# Copyright (C) 2026-present Linaro Limited
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Queue length and job wait time statistics per device type.

Queue length only means anything as an instantaneous measurement, so the
scheduler samples it (record_snapshots) rather than have the web frontend
reconstruct it from job history. The read helpers here are what the
device type page uses.
"""

from __future__ import annotations

import datetime

from django.conf import settings
from django.db.models import (
    Avg,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    OuterRef,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Cast, NullIf
from django.utils import timezone

from lava_scheduler_app.models import (
    Device,
    DeviceType,
    DeviceTypeQueueSnapshot,
    TestJob,
    Worker,
)

RUNNING_STATES = (
    TestJob.STATE_SCHEDULING,
    TestJob.STATE_SCHEDULED,
    TestJob.STATE_RUNNING,
)


def _counts_by_device_type(queryset) -> dict[str, int]:
    return {
        row["requested_device_type"]: row["count"]
        for row in queryset.values("requested_device_type").annotate(count=Count("*"))
        if row["requested_device_type"] is not None
    }


def record_snapshots(now=None, since=None) -> int:
    """
    Append one snapshot per device type. Returns the number of rows created.

    ``since`` bounds the jobs that average_wait_time is computed over. It
    defaults to the timestamp of the previous snapshot, so that consecutive
    windows abut exactly: no job is counted twice and none is missed, even
    if the scheduler restarted or stalled in between.
    """
    if now is None:
        now = timezone.now()
    if since is None:
        since = DeviceTypeQueueSnapshot.objects.aggregate(Max("timestamp"))[
            "timestamp__max"
        ]
        default_since = now - datetime.timedelta(
            seconds=settings.QUEUE_SNAPSHOT_INTERVAL
        )
        # Bound the window so a long outage cannot turn the first snapshot
        # back into a scan of the whole job table.
        floor = now - datetime.timedelta(days=1)
        if since is None or since < floor:
            since = floor if since is not None else default_since

    queued = _counts_by_device_type(
        TestJob.objects.filter(state=TestJob.STATE_SUBMITTED)
    )
    running = _counts_by_device_type(TestJob.objects.filter(state__in=RUNNING_STATES))

    available = {
        row["device_type"]: row["count"]
        for row in Device.objects.filter(
            state=Device.STATE_IDLE,
            health__in=(Device.HEALTH_UNKNOWN, Device.HEALTH_GOOD),
            worker_host_id__in=Worker.objects.filter(state=Worker.STATE_ONLINE),
        )
        .values("device_type")
        .annotate(count=Count("*"))
    }

    # Jobs that left the queue since the previous snapshot.
    waits = {
        row["requested_device_type"]: (row["count"], row["average"])
        for row in TestJob.objects.filter(
            start_time__gte=since,
            start_time__lt=now,
            submit_time__isnull=False,
        )
        .values("requested_device_type")
        .annotate(
            count=Count("*"),
            average=Avg(
                ExpressionWrapper(
                    F("start_time") - F("submit_time"), output_field=DurationField()
                )
            ),
        )
        if row["requested_device_type"] is not None
    }

    # Jobs that released a device since the previous snapshot.
    durations = {
        row["requested_device_type"]: (row["count"], row["average"])
        for row in TestJob.objects.filter(
            end_time__gte=since,
            end_time__lt=now,
            start_time__isnull=False,
        )
        .values("requested_device_type")
        .annotate(
            count=Count("*"),
            average=Avg(
                ExpressionWrapper(
                    F("end_time") - F("start_time"), output_field=DurationField()
                )
            ),
        )
        if row["requested_device_type"] is not None
    }

    snapshots = []
    for name in DeviceType.objects.values_list("name", flat=True):
        started, average = waits.get(name, (0, None))
        finished, duration = durations.get(name, (0, None))
        snapshots.append(
            DeviceTypeQueueSnapshot(
                device_type_id=name,
                timestamp=now,
                queued_jobs=queued.get(name, 0),
                running_jobs=running.get(name, 0),
                available_devices=available.get(name, 0),
                started_jobs=started,
                # Avg() over a DurationField can come back negative if a
                # clock moved backwards between submit and start.
                average_wait_time=average
                if average and average.total_seconds() >= 0
                else None,
                finished_jobs=finished,
                average_duration=duration
                if duration and duration.total_seconds() >= 0
                else None,
            )
        )
    DeviceTypeQueueSnapshot.objects.bulk_create(snapshots, batch_size=500)
    return len(snapshots)


def prune_snapshots(now=None) -> int:
    """Drop snapshots older than settings.QUEUE_SNAPSHOT_RETENTION_DAYS."""
    if now is None:
        now = timezone.now()
    cutoff = now - datetime.timedelta(days=settings.QUEUE_SNAPSHOT_RETENTION_DAYS)
    deleted, _ = DeviceTypeQueueSnapshot.objects.filter(timestamp__lt=cutoff).delete()
    return deleted


def queue_history(device_type, days: int = 7):
    """Snapshots for one device type, oldest first."""
    since = timezone.now() - datetime.timedelta(days=days)
    return list(
        DeviceTypeQueueSnapshot.objects.filter(
            device_type=device_type, timestamp__gte=since
        )
        .order_by("timestamp")
        .values(
            "timestamp",
            "queued_jobs",
            "running_jobs",
            "available_devices",
        )
    )


def _weighted_average(device_type, days, count_field, average_field):
    """
    Combine per-snapshot averages into one, weighted by their populations.

    Averaging the averages directly would be wrong: a quiet snapshot
    covering a single slow job would count as much as a busy one covering
    fifty fast ones. Returns (average, population).
    """
    since = timezone.now() - datetime.timedelta(days=days)
    rows = DeviceTypeQueueSnapshot.objects.filter(
        device_type=device_type,
        timestamp__gte=since,
        **{f"{count_field}__gt": 0, f"{average_field}__isnull": False},
    ).values_list(count_field, average_field)

    total_jobs = 0
    total = datetime.timedelta()
    for count, average in rows:
        total_jobs += count
        total += average * count
    if not total_jobs:
        return None, 0
    return total / total_jobs, total_jobs


def average_wait_time(device_type, days: int = 7):
    """Mean wait for the jobs that started on ``device_type`` over ``days``."""
    return _weighted_average(device_type, days, "started_jobs", "average_wait_time")


def average_duration(device_type, days: int = 7):
    """
    Mean time a device of this type was occupied per job, over ``days``.

    This is the service time behind the queue: how long a board is held
    from the moment a job starts until it releases it.
    """
    return _weighted_average(device_type, days, "finished_jobs", "average_duration")


def utilisation(device_type, days: int = 7):
    """
    Fraction of usable boards busy, averaged over the sampled snapshots.

    Measured against the boards the scheduler could actually have used at
    each sample (busy + idle-and-healthy), so boards that were retired or
    on an offline worker do not count as spare capacity.
    """
    since = timezone.now() - datetime.timedelta(days=days)
    rows = DeviceTypeQueueSnapshot.objects.filter(
        device_type=device_type, timestamp__gte=since
    ).values_list("running_jobs", "available_devices")

    busy = usable = 0
    for running, available in rows:
        busy += running
        usable += running + available
    if not usable:
        return None
    return busy / usable * 100


def statistics(device_type, days: int = 7) -> dict:
    """
    Everything the device type page and the REST API report, in one dict.

    Keeping the aggregation here means the HTML view and the API cannot
    drift apart on how a window is bounded or an average is weighted.
    """
    since = timezone.now() - datetime.timedelta(days=days)
    wait, wait_jobs = average_wait_time(device_type, days=days)
    duration, duration_jobs = average_duration(device_type, days=days)
    latest = current_queue(device_type)

    return {
        "device_type": device_type.name,
        "days": days,
        "snapshots": DeviceTypeQueueSnapshot.objects.filter(
            device_type=device_type, timestamp__gte=since
        ).count(),
        "average_wait_time": wait,
        "average_wait_jobs": wait_jobs,
        "average_duration": duration,
        "average_duration_jobs": duration_jobs,
        "utilisation": utilisation(device_type, days=days),
        "queued_jobs": latest.queued_jobs if latest else None,
        "running_jobs": latest.running_jobs if latest else None,
        "available_devices": latest.available_devices if latest else None,
        "last_sample": latest.timestamp if latest else None,
    }


def annotate_statistics(queryset, days: int = 7, device_type_ref="device_type"):
    """
    Annotate average_wait_time, average_duration and utilisation onto rows
    keyed by device type.

    The same aggregation as statistics(), weighted the same way, but done
    in SQL as subqueries: a table of every device type then costs a single
    query, and can be sorted on the figures.
    """
    since = timezone.now() - datetime.timedelta(days=days)

    def snapshots(**filters):
        return (
            DeviceTypeQueueSnapshot.objects.filter(
                device_type=OuterRef(device_type_ref), timestamp__gte=since, **filters
            )
            .annotate(dummy_group_by=Value(1))  # Disable GROUP BY
            .values("dummy_group_by")
        )

    def weighted_average(count_field, average_field):
        total = ExpressionWrapper(
            F(count_field) * F(average_field), output_field=DurationField()
        )
        return Subquery(
            snapshots(**{f"{count_field}__gt": 0, f"{average_field}__isnull": False})
            .annotate(
                average=ExpressionWrapper(
                    Sum(total) / Sum(count_field), output_field=DurationField()
                )
            )
            .values("average"),
            output_field=DurationField(),
        )

    return queryset.annotate(
        average_wait_time=weighted_average("started_jobs", "average_wait_time"),
        average_duration=weighted_average("finished_jobs", "average_duration"),
        utilisation=Subquery(
            snapshots()
            .annotate(
                utilisation=ExpressionWrapper(
                    Cast(Sum("running_jobs"), FloatField())
                    * 100
                    / NullIf(Sum(F("running_jobs") + F("available_devices")), 0),
                    output_field=FloatField(),
                )
            )
            .values("utilisation"),
            output_field=FloatField(),
        ),
    )


def current_queue(device_type):
    """The most recent snapshot for a device type, or None."""
    return (
        DeviceTypeQueueSnapshot.objects.filter(device_type=device_type)
        .order_by("-timestamp")
        .first()
    )
