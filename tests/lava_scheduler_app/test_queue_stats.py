#
# Copyright (C) 2026-present Linaro Limited
#
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from lava_scheduler_app.models import (
    Device,
    DeviceType,
    DeviceTypeQueueSnapshot,
    TestJob,
    Worker,
)
from lava_scheduler_app.queue_stats import (
    average_duration,
    average_wait_time,
    current_queue,
    prune_snapshots,
    queue_history,
    record_snapshots,
    statistics,
    utilisation,
)

JOB_DEFINITION = """
job_name: queue stats
visibility: public
timeouts:
  job:
    minutes: 10
  action:
    minutes: 5
actions: []
"""


class QueueStatsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="stats", password="stats")
        self.worker = Worker.objects.create(
            hostname="worker-01", state=Worker.STATE_ONLINE
        )
        self.panda = DeviceType.objects.create(name="panda")
        self.beagle = DeviceType.objects.create(name="beagle")
        self.device = Device.objects.create(
            hostname="panda01",
            device_type=self.panda,
            worker_host=self.worker,
            state=Device.STATE_IDLE,
            health=Device.HEALTH_GOOD,
        )

    def _job(self, device_type, state, submit_time, start_time=None, end_time=None):
        job = TestJob.objects.create(
            submitter=self.user,
            requested_device_type=device_type,
            definition=JOB_DEFINITION,
            state=state,
        )
        # submit_time has auto_now_add semantics, so set it after creation.
        TestJob.objects.filter(pk=job.pk).update(
            submit_time=submit_time, start_time=start_time, end_time=end_time
        )
        return TestJob.objects.get(pk=job.pk)


class TestRecordSnapshots(QueueStatsTestCase):
    def test_one_row_per_device_type(self):
        self.assertEqual(record_snapshots(), 2)
        self.assertEqual(DeviceTypeQueueSnapshot.objects.count(), 2)
        self.assertEqual(
            set(
                DeviceTypeQueueSnapshot.objects.values_list("device_type_id", flat=True)
            ),
            {"panda", "beagle"},
        )

    def test_counts_queued_running_and_available(self):
        now = timezone.now()
        self._job(self.panda, TestJob.STATE_SUBMITTED, now - timedelta(minutes=5))
        self._job(self.panda, TestJob.STATE_SUBMITTED, now - timedelta(minutes=3))
        self._job(self.panda, TestJob.STATE_RUNNING, now - timedelta(minutes=9))
        self._job(self.beagle, TestJob.STATE_SUBMITTED, now - timedelta(minutes=1))

        record_snapshots(now=now)

        panda = DeviceTypeQueueSnapshot.objects.get(device_type_id="panda")
        self.assertEqual(panda.queued_jobs, 2)
        self.assertEqual(panda.running_jobs, 1)
        self.assertEqual(panda.available_devices, 1)

        beagle = DeviceTypeQueueSnapshot.objects.get(device_type_id="beagle")
        self.assertEqual(beagle.queued_jobs, 1)
        self.assertEqual(beagle.running_jobs, 0)
        self.assertEqual(beagle.available_devices, 0)

    def test_offline_worker_devices_are_not_available(self):
        self.worker.state = Worker.STATE_OFFLINE
        self.worker.save()
        record_snapshots()
        self.assertEqual(
            DeviceTypeQueueSnapshot.objects.get(
                device_type_id="panda"
            ).available_devices,
            0,
        )

    def test_average_wait_time_over_jobs_that_started(self):
        now = timezone.now()
        # Waited 2 and 4 minutes -> mean of 3.
        self._job(
            self.panda,
            TestJob.STATE_RUNNING,
            now - timedelta(minutes=6),
            now - timedelta(minutes=4),
        )
        self._job(
            self.panda,
            TestJob.STATE_RUNNING,
            now - timedelta(minutes=7),
            now - timedelta(minutes=3),
        )
        # Still queued: contributes to the queue, not to the average.
        self._job(self.panda, TestJob.STATE_SUBMITTED, now - timedelta(minutes=1))

        record_snapshots(now=now, since=now - timedelta(minutes=10))

        panda = DeviceTypeQueueSnapshot.objects.get(device_type_id="panda")
        self.assertEqual(panda.started_jobs, 2)
        self.assertEqual(panda.average_wait_time, timedelta(minutes=3))
        self.assertEqual(panda.queued_jobs, 1)

    def test_average_wait_time_is_null_when_nothing_started(self):
        record_snapshots()
        panda = DeviceTypeQueueSnapshot.objects.get(device_type_id="panda")
        self.assertEqual(panda.started_jobs, 0)
        self.assertIsNone(panda.average_wait_time)

    def test_jobs_outside_the_window_are_excluded(self):
        now = timezone.now()
        self._job(
            self.panda,
            TestJob.STATE_RUNNING,
            now - timedelta(hours=3),
            now - timedelta(hours=2),
        )
        record_snapshots(now=now, since=now - timedelta(minutes=10))
        panda = DeviceTypeQueueSnapshot.objects.get(device_type_id="panda")
        self.assertEqual(panda.started_jobs, 0)

    @override_settings(QUEUE_SNAPSHOT_INTERVAL=300)
    def test_window_defaults_to_the_previous_snapshot(self):
        now = timezone.now()
        previous = now - timedelta(minutes=30)
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda, timestamp=previous
        )
        # Started 20 minutes ago: after the previous snapshot, so counted
        # even though that is more than QUEUE_SNAPSHOT_INTERVAL ago.
        self._job(
            self.panda,
            TestJob.STATE_RUNNING,
            now - timedelta(minutes=25),
            now - timedelta(minutes=20),
        )
        record_snapshots(now=now)
        panda = (
            DeviceTypeQueueSnapshot.objects.filter(device_type_id="panda")
            .order_by("-timestamp")
            .first()
        )
        self.assertEqual(panda.started_jobs, 1)
        self.assertEqual(panda.average_wait_time, timedelta(minutes=5))

    def test_stale_previous_snapshot_is_clamped_to_one_day(self):
        now = timezone.now()
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda, timestamp=now - timedelta(days=30)
        )
        self._job(
            self.panda,
            TestJob.STATE_RUNNING,
            now - timedelta(days=10),
            now - timedelta(days=9),
        )
        record_snapshots(now=now)
        panda = (
            DeviceTypeQueueSnapshot.objects.filter(device_type_id="panda")
            .order_by("-timestamp")
            .first()
        )
        self.assertEqual(panda.started_jobs, 0)


class TestReadHelpers(QueueStatsTestCase):
    def test_queue_history_is_ordered_and_windowed(self):
        now = timezone.now()
        for minutes, queued in ((5, 3), (15, 7), (25, 1)):
            DeviceTypeQueueSnapshot.objects.create(
                device_type=self.panda,
                timestamp=now - timedelta(minutes=minutes),
                queued_jobs=queued,
            )
        # Outside the window.
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(days=9),
            queued_jobs=99,
        )
        # Another device type.
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.beagle, timestamp=now, queued_jobs=42
        )

        history = queue_history(self.panda, days=7)
        self.assertEqual([row["queued_jobs"] for row in history], [1, 7, 3])

    def test_average_wait_time_is_weighted_by_job_count(self):
        now = timezone.now()
        # 1 job waiting 60 minutes, 9 jobs waiting 1 minute.
        # Weighted mean is (60 + 9) / 10 = 6.9 minutes, not (60 + 1) / 2.
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(minutes=10),
            started_jobs=1,
            average_wait_time=timedelta(minutes=60),
        )
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(minutes=5),
            started_jobs=9,
            average_wait_time=timedelta(minutes=1),
        )
        mean, count = average_wait_time(self.panda, days=7)
        self.assertEqual(count, 10)
        self.assertEqual(mean, timedelta(minutes=6.9))

    def test_average_wait_time_without_data(self):
        self.assertEqual(average_wait_time(self.panda, days=7), (None, 0))

    def test_current_queue_returns_latest(self):
        now = timezone.now()
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda, timestamp=now - timedelta(minutes=10), queued_jobs=1
        )
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda, timestamp=now, queued_jobs=5
        )
        self.assertEqual(current_queue(self.panda).queued_jobs, 5)
        self.assertIsNone(current_queue(self.beagle))

    @override_settings(QUEUE_SNAPSHOT_RETENTION_DAYS=30)
    def test_prune_drops_only_expired_snapshots(self):
        now = timezone.now()
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda, timestamp=now - timedelta(days=40)
        )
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda, timestamp=now - timedelta(days=10)
        )
        self.assertEqual(prune_snapshots(now=now), 1)
        self.assertEqual(DeviceTypeQueueSnapshot.objects.count(), 1)


class TestDeviceTypeQueueUI(QueueStatsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = "/scheduler/device_type/panda"

    def test_page_renders_without_snapshots(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No queue samples recorded yet")
        self.assertNotContains(response, 'id="queue-chart"')
        self.assertIsNone(response.context["average_duration"])
        self.assertIsNone(response.context["utilisation"])

    def test_page_renders_the_chart_and_average_wait(self):
        now = timezone.now()
        for minutes, queued in ((30, 4), (20, 2), (10, 0)):
            DeviceTypeQueueSnapshot.objects.create(
                device_type=self.panda,
                timestamp=now - timedelta(minutes=minutes),
                queued_jobs=queued,
                running_jobs=1,
                available_devices=2,
                started_jobs=2,
                average_wait_time=timedelta(minutes=5),
                finished_jobs=2,
                average_duration=timedelta(minutes=12),
            )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="queue-chart"')
        self.assertContains(response, 'id="queue-chart-data"')
        # Weighted average of three snapshots of 5 minutes each.
        self.assertContains(response, "5m 00s")
        self.assertContains(response, "6 jobs")
        # Duration and utilisation sit beside the wait.
        self.assertContains(response, "12m 00s")
        self.assertEqual(response.context["average_duration"], timedelta(minutes=12))
        self.assertEqual(response.context["average_duration_jobs"], 6)
        # 1 busy of 3 usable at every snapshot.
        self.assertAlmostEqual(response.context["utilisation"], 100 / 3)

        chart = response.context["queue_chart"]
        self.assertEqual(chart["queued"], [[0, 4], [1, 2], [2, 0]])
        self.assertEqual(chart["running"], [[0, 1], [1, 1], [2, 1]])
        self.assertEqual(chart["available"], [[0, 2], [1, 2], [2, 2]])
        self.assertEqual(len(chart["ticks"]), 3)

    def test_snapshots_of_other_device_types_are_not_shown(self):
        now = timezone.now()
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.beagle, timestamp=now, queued_jobs=99
        )
        response = self.client.get(self.url)
        self.assertEqual(response.context["queue_chart"]["queued"], [])
        self.assertTrue(response.context["queue_chart_empty"])


class TestDeviceTypesTableStats(QueueStatsTestCase):
    def setUp(self):
        super().setUp()
        self.arndale = DeviceType.objects.create(name="arndale")
        for hostname, device_type in (
            ("beagle01", self.beagle),
            ("arndale01", self.arndale),
        ):
            Device.objects.create(
                hostname=hostname,
                device_type=device_type,
                worker_host=self.worker,
                state=Device.STATE_IDLE,
                health=Device.HEALTH_GOOD,
            )
        self.client.force_login(self.user)

        now = timezone.now()
        # panda: wait weighted to 6.9 minutes, duration to 20 minutes,
        # 4 busy of 6 usable.
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(minutes=10),
            running_jobs=1,
            available_devices=1,
            started_jobs=1,
            average_wait_time=timedelta(minutes=60),
            finished_jobs=2,
            average_duration=timedelta(minutes=30),
        )
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(minutes=5),
            running_jobs=3,
            available_devices=1,
            started_jobs=9,
            average_wait_time=timedelta(minutes=1),
            finished_jobs=2,
            average_duration=timedelta(minutes=10),
        )
        # Outside the window.
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(days=9),
            running_jobs=50,
            started_jobs=50,
            average_wait_time=timedelta(hours=10),
        )
        # beagle: 1 busy of 4 usable, nothing started or finished.
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.beagle,
            timestamp=now - timedelta(minutes=5),
            running_jobs=1,
            available_devices=3,
        )
        # arndale has no snapshots at all.

    def _table(self, **params):
        response = self.client.get(reverse("lava.scheduler.device_types"), params)
        self.assertEqual(response.status_code, 200)
        return response, response.context["dt_table"]

    @override_settings(QUEUE_STATS_WINDOW_DAYS=7)
    def test_table_matches_the_device_type_page(self):
        response, table = self._table()
        rows = {row["device_type"]: row for row in table.data}

        panda = statistics(self.panda, days=7)
        self.assertEqual(rows["panda"]["average_wait_time"], timedelta(minutes=6.9))
        self.assertEqual(rows["panda"]["average_wait_time"], panda["average_wait_time"])
        self.assertEqual(rows["panda"]["average_duration"], panda["average_duration"])
        self.assertAlmostEqual(rows["panda"]["utilisation"], panda["utilisation"])
        self.assertAlmostEqual(rows["panda"]["utilisation"], 400 / 6)

        self.assertIsNone(rows["beagle"]["average_wait_time"])
        self.assertIsNone(rows["beagle"]["average_duration"])
        self.assertAlmostEqual(rows["beagle"]["utilisation"], 25.0)

        self.assertIsNone(rows["arndale"]["average_wait_time"])
        self.assertIsNone(rows["arndale"]["average_duration"])
        self.assertIsNone(rows["arndale"]["utilisation"])

        self.assertContains(response, "6m 54s")
        self.assertContains(response, "20m 00s")
        self.assertContains(response, "66.7%")
        self.assertContains(response, "25.0%")
        self.assertContains(response, "over the last 7 days")

    def test_sorting_keeps_device_types_without_data_last(self):
        for sort, expected in (
            ("utilisation", ["beagle", "panda", "arndale"]),
            ("-utilisation", ["panda", "beagle", "arndale"]),
            # Only panda has a wait; the rest tie and fall back to name order.
            ("average_wait_time", ["panda", "arndale", "beagle"]),
            ("-average_wait_time", ["panda", "arndale", "beagle"]),
        ):
            _, table = self._table(sort=sort)
            names = [row["device_type"] for row in table.data]
            self.assertEqual(names, expected, sort)


class TestDurationFilter(TestCase):
    def test_formats(self):
        from lava_scheduler_app.templatetags.utils import duration

        self.assertEqual(duration(None), "")
        self.assertEqual(duration(timedelta(seconds=0)), "0s")
        self.assertEqual(duration(timedelta(seconds=12.7)), "12s")
        self.assertEqual(duration(timedelta(minutes=5, seconds=29)), "5m 29s")
        self.assertEqual(duration(timedelta(hours=2, minutes=5)), "2h 05m")
        self.assertEqual(duration(timedelta(seconds=-1)), "")


class TestDurationStats(QueueStatsTestCase):
    def test_average_duration_over_jobs_that_finished(self):
        now = timezone.now()
        # Occupied a board for 10 and 20 minutes -> mean of 15.
        self._job(
            self.panda,
            TestJob.STATE_FINISHED,
            now - timedelta(minutes=40),
            now - timedelta(minutes=35),
            now - timedelta(minutes=25),
        )
        self._job(
            self.panda,
            TestJob.STATE_FINISHED,
            now - timedelta(minutes=45),
            now - timedelta(minutes=30),
            now - timedelta(minutes=10),
        )
        # Started but still running: occupies a board, but has no duration yet.
        self._job(
            self.panda,
            TestJob.STATE_RUNNING,
            now - timedelta(minutes=8),
            now - timedelta(minutes=5),
        )

        record_snapshots(now=now, since=now - timedelta(hours=1))

        panda = DeviceTypeQueueSnapshot.objects.get(device_type_id="panda")
        self.assertEqual(panda.finished_jobs, 2)
        self.assertEqual(panda.average_duration, timedelta(minutes=15))

    def test_average_duration_is_null_when_nothing_finished(self):
        record_snapshots()
        panda = DeviceTypeQueueSnapshot.objects.get(device_type_id="panda")
        self.assertEqual(panda.finished_jobs, 0)
        self.assertIsNone(panda.average_duration)

    def test_duration_window_excludes_jobs_that_ended_earlier(self):
        now = timezone.now()
        self._job(
            self.panda,
            TestJob.STATE_FINISHED,
            now - timedelta(hours=5),
            now - timedelta(hours=4),
            now - timedelta(hours=3),
        )
        record_snapshots(now=now, since=now - timedelta(minutes=10))
        panda = DeviceTypeQueueSnapshot.objects.get(device_type_id="panda")
        self.assertEqual(panda.finished_jobs, 0)

    def test_average_duration_is_weighted_by_job_count(self):
        now = timezone.now()
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(minutes=10),
            finished_jobs=1,
            average_duration=timedelta(minutes=60),
        )
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(minutes=5),
            finished_jobs=9,
            average_duration=timedelta(minutes=1),
        )
        mean, count = average_duration(self.panda, days=7)
        self.assertEqual(count, 10)
        self.assertEqual(mean, timedelta(minutes=6.9))

    def test_average_duration_without_data(self):
        self.assertEqual(average_duration(self.panda, days=7), (None, 0))

    def test_wait_and_duration_do_not_share_a_population(self):
        """A job that started but has not finished counts for one, not both."""
        now = timezone.now()
        self._job(
            self.panda,
            TestJob.STATE_RUNNING,
            now - timedelta(minutes=20),
            now - timedelta(minutes=15),
        )
        record_snapshots(now=now, since=now - timedelta(hours=1))
        panda = DeviceTypeQueueSnapshot.objects.get(device_type_id="panda")
        self.assertEqual(panda.started_jobs, 1)
        self.assertEqual(panda.average_wait_time, timedelta(minutes=5))
        self.assertEqual(panda.finished_jobs, 0)
        self.assertIsNone(panda.average_duration)


class TestUtilisation(QueueStatsTestCase):
    def test_utilisation_counts_busy_against_usable(self):
        now = timezone.now()
        # 3 busy of 4 usable, then 1 busy of 4 usable -> 4/8 = 50%.
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(minutes=10),
            running_jobs=3,
            available_devices=1,
        )
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now - timedelta(minutes=5),
            running_jobs=1,
            available_devices=3,
        )
        self.assertEqual(utilisation(self.panda, days=7), 50.0)

    def test_unusable_devices_are_not_spare_capacity(self):
        """A retired or offline board must not dilute utilisation."""
        now = timezone.now()
        # Only one board usable, and it is busy: fully utilised, even though
        # the device type owns more boards that are not available.
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=now,
            running_jobs=1,
            available_devices=0,
        )
        self.assertEqual(utilisation(self.panda, days=7), 100.0)

    def test_utilisation_without_snapshots(self):
        self.assertIsNone(utilisation(self.panda, days=7))

    def test_utilisation_with_no_usable_devices(self):
        DeviceTypeQueueSnapshot.objects.create(
            device_type=self.panda,
            timestamp=timezone.now(),
            running_jobs=0,
            available_devices=0,
        )
        self.assertIsNone(utilisation(self.panda, days=7))
