# Copyright (C) 2020 Linaro Limited
#
# Author: Stevan Radaković <stevan.radakovic@linaro.org>
#
# SPDX-License-Identifier: GPL-2.0-or-later

import datetime
import importlib
import json

import pytest
import zmq
from django.utils import timezone

from lava_scheduler_app.models import Worker

lava_scheduler = importlib.import_module(
    "lava_server.management.commands.lava-scheduler"
)
Command = lava_scheduler.Command


@pytest.mark.django_db
def test_check_workers(mocker):
    Worker.objects.create(
        hostname="worker-01",
        health=Worker.HEALTH_ACTIVE,
        state=Worker.STATE_ONLINE,
        last_ping=timezone.now(),
    )
    Worker.objects.create(
        hostname="worker-02",
        health=Worker.HEALTH_ACTIVE,
        state=Worker.STATE_ONLINE,
        last_ping=timezone.now() - datetime.timedelta(seconds=10000),
    )
    Worker.objects.create(
        hostname="worker-03",
        health=Worker.HEALTH_MAINTENANCE,
        state=Worker.STATE_ONLINE,
        last_ping=timezone.now() - datetime.timedelta(seconds=10000),
    )

    now = timezone.now()
    mocker.patch("django.utils.timezone.now", return_value=now)

    cmd = Command()
    cmd.logger = mocker.Mock()
    cmd.check_workers()

    assert Worker.objects.get(hostname="worker-01").state == Worker.STATE_ONLINE
    assert Worker.objects.get(hostname="worker-02").state == Worker.STATE_OFFLINE
    assert Worker.objects.get(hostname="worker-03").state == Worker.STATE_OFFLINE


@pytest.mark.django_db
def test_get_available_dts(mocker):
    cmd = Command()
    cmd.logger = mocker.Mock()
    cmd.sub = mocker.Mock()

    # Ending the loop
    cmd.sub.recv_multipart = mocker.Mock(side_effect=[zmq.ZMQError])
    assert cmd.receive_events() is False

    # Ending the loop
    cmd.sub.recv_multipart = mocker.Mock(
        side_effect=[
            [
                b"test.testjob",
                "",
                "",
                "",
                json.dumps({"state": "Submitted", "device_type": "qemu"}),
            ],
            [
                b"test.device",
                "",
                "",
                "",
                json.dumps(
                    {"state": "Idle", "health": "Good", "device_type": "docker"}
                ),
            ],
            [],
            [b"\x81"],
            zmq.ZMQError,
        ]
    )
    assert cmd.receive_events() is True


@pytest.mark.django_db
def test_main_loop(mocker):
    schedule = mocker.Mock()
    mocker.patch(__name__ + ".lava_scheduler.schedule", schedule)

    cmd = Command()
    cmd.logger = mocker.Mock()
    cmd.poller = mocker.Mock()
    cmd.check_workers = mocker.Mock()
    cmd.receive_events = mocker.Mock(side_effect=[True, KeyError])

    with pytest.raises(KeyError):
        cmd.main_loop()
    assert len(cmd.receive_events.mock_calls) == 2
    assert len(schedule.mock_calls) == 2


@pytest.mark.django_db
def test_handle(mocker):
    mocker.patch("zmq.Context", mocker.Mock())
    cmd = Command()
    cmd.logger = mocker.Mock()
    cmd.main_loop = mocker.Mock(side_effect=KeyboardInterrupt)
    cmd.drop_privileges = mocker.Mock()

    cmd.handle(
        level="INFO",
        log_file="-",
        user="lavaserver",
        group="lavaserver",
        event_url="tcp://localhost:5500",
        ipv6=False,
    )


@pytest.mark.django_db
def test_first_snapshot_does_not_wait_for_uptime(mocker, settings):
    """
    time.monotonic() counts from boot, so a machine that has just started
    reports a small value. The "never sampled yet" sentinel must not be a
    real point on that clock, or the first samples are skipped until the
    machine has been up for a whole interval.
    """
    settings.QUEUE_SNAPSHOT_INTERVAL = 300
    record = mocker.patch(__name__ + ".lava_scheduler.record_snapshots", return_value=0)
    prune = mocker.patch(__name__ + ".lava_scheduler.prune_snapshots", return_value=0)
    mocker.patch.object(lava_scheduler.time, "monotonic", return_value=42.0)

    cmd = Command()
    cmd.logger = mocker.Mock()
    cmd.record_queue_snapshots()

    assert len(record.mock_calls) == 1
    assert len(prune.mock_calls) == 1


@pytest.mark.django_db
def test_record_queue_snapshots_throttles_on_the_interval(mocker, settings):
    settings.QUEUE_SNAPSHOT_INTERVAL = 300
    record = mocker.patch(__name__ + ".lava_scheduler.record_snapshots", return_value=0)
    mocker.patch(__name__ + ".lava_scheduler.prune_snapshots", return_value=0)

    cmd = Command()
    cmd.logger = mocker.Mock()

    # First pass records; an immediate second pass is throttled out.
    cmd.record_queue_snapshots()
    cmd.record_queue_snapshots()
    assert len(record.mock_calls) == 1

    # Once the interval has elapsed it records again.
    cmd.last_snapshot -= settings.QUEUE_SNAPSHOT_INTERVAL
    cmd.record_queue_snapshots()
    assert len(record.mock_calls) == 2


@pytest.mark.django_db
def test_record_queue_snapshots_disabled_by_a_zero_interval(mocker, settings):
    settings.QUEUE_SNAPSHOT_INTERVAL = 0
    record = mocker.patch(__name__ + ".lava_scheduler.record_snapshots", return_value=0)

    cmd = Command()
    cmd.logger = mocker.Mock()
    cmd.record_queue_snapshots()

    assert record.mock_calls == []


@pytest.mark.django_db
def test_prune_runs_on_its_own_slower_interval(mocker, settings):
    settings.QUEUE_SNAPSHOT_INTERVAL = 300
    settings.QUEUE_SNAPSHOT_PRUNE_INTERVAL = 24 * 3600
    mocker.patch(__name__ + ".lava_scheduler.record_snapshots", return_value=0)
    prune = mocker.patch(__name__ + ".lava_scheduler.prune_snapshots", return_value=0)

    cmd = Command()
    cmd.logger = mocker.Mock()

    # Both fire on the first pass.
    cmd.record_queue_snapshots()
    assert len(prune.mock_calls) == 1

    # Several snapshots later, the prune has not come round again.
    for _ in range(3):
        cmd.last_snapshot -= settings.QUEUE_SNAPSHOT_INTERVAL
        cmd.record_queue_snapshots()
    assert len(prune.mock_calls) == 1

    # It does once its own, much longer, interval has elapsed.
    cmd.last_snapshot -= settings.QUEUE_SNAPSHOT_INTERVAL
    cmd.last_prune -= settings.QUEUE_SNAPSHOT_PRUNE_INTERVAL
    cmd.record_queue_snapshots()
    assert len(prune.mock_calls) == 2
