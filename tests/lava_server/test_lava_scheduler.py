# -*- coding: utf-8 -*-
# Copyright (C) 2020 Linaro Limited
#
# Author: Stevan Radaković <stevan.radakovic@linaro.org>
#
# This file is part of LAVA.
#
# LAVA is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License version 3
# as published by the Free Software Foundation
#
# LAVA is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with LAVA.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import importlib

import pytest
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
def test_main_loop(mocker):
    schedule = mocker.Mock(side_effect=KeyError)
    mocker.patch(__name__ + ".lava_scheduler.schedule", schedule)

    cmd = Command()
    cmd.logger = mocker.Mock()

    with pytest.raises(KeyError):
        cmd.main_loop()

    assert len(schedule.mock_calls) == 1


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
    )
