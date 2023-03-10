# -*- coding: utf-8 -*-
# Copyright (C) 2020-present Linaro Limited
#
# Author: Remi Duraffort <remi.duraffort@linaro.org>
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
import signal
import time

from django.db import connection, transaction
from django.db.utils import InterfaceError, OperationalError
from django.utils import timezone

from lava_common.version import __version__
from lava_scheduler_app.models import Worker
from lava_scheduler_app.scheduler import schedule
from lava_server.cmdutils import LAVADaemonCommand

#############
# CONSTANTS #
#############

INTERVAL = 20
PING_TIMEOUT = 3 * INTERVAL

# Log format
FORMAT = "%(asctime)-15s %(levelname)7s %(message)s"


class Command(LAVADaemonCommand):
    logger = None
    help = "LAVA scheduler"
    default_logfile = "/var/log/lava-server/lava-scheduler.log"

    def handle(self, *args, **options):
        # Initialize logging.
        self.setup_logging(
            "lava-scheduler", options["level"], options["log_file"], FORMAT
        )

        self.logger.info("[INIT] Starting lava-scheduler")
        self.logger.info("[INIT] Version %s", __version__)

        self.logger.info("[INIT] Dropping privileges")
        if not self.drop_privileges(options["user"], options["group"]):
            self.logger.error("[INIT] Unable to drop privileges")
            return

        self.logger.info("[INIT] Connect to event stream")

        # Every signals should raise a KeyboardInterrupt
        def signal_handler(*_):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, signal_handler)

        # Main loop
        self.logger.info("[INIT] Starting main loop")
        try:
            self.main_loop()
        except KeyboardInterrupt:
            self.logger.info("Received a signal, leaving")
        except Exception as exc:
            self.logger.error("[CLOSE] Unknown exception raised, leaving!")
            self.logger.exception(exc)

    def check_workers(self):
        query = Worker.objects.select_for_update()
        query = query.filter(state=Worker.STATE_ONLINE)
        query = query.filter(
            last_ping__lt=timezone.now() - datetime.timedelta(seconds=PING_TIMEOUT)
        )
        for worker in query:
            self.logger.info(f"Worker <{worker.hostname}> is now offline")
            worker.go_state_offline()
            worker.save()

        return [
            w.hostname
            for w in Worker.objects.filter(
                state=Worker.STATE_ONLINE, health=Worker.HEALTH_ACTIVE
            )
        ]

    def main_loop(self) -> None:
        while True:
            begin = time.monotonic()
            try:
                # Check remote worker connectivity
                with transaction.atomic():
                    workers = self.check_workers()

                # Schedule jobs
                schedule(self.logger, workers)

                time.sleep(max(INTERVAL - (time.monotonic() - begin), 0))

            except (OperationalError, InterfaceError):
                self.logger.info("[RESET] database connection reset.")
                # Closing the database connection will force Django to reopen
                # the connection
                connection.close()
                time.sleep(2)
