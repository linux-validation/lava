# Copyright (C) 2020 Arm Limited
#
# Author: Dean Birch <dean.birch@arm.com>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

import shlex
import time

from lava_common.constants import DISPATCHER_DOWNLOAD_DIR
from lava_common.exceptions import JobError
from lava_dispatcher.action import Action


def upload_path_from_job(job):
    return f"{DISPATCHER_DOWNLOAD_DIR}/{job.job_id}/uploads"


class UploadAction(Action):
    name = "upload-action"
    description = "Upload any files created in the artifact directory"
    summary = "upload files action"

    def __init__(self):
        super().__init__()
        self.section = "upload"
        self.path = None
        self.local = False
        self.commands = []

    def command_prefix(self):
        name = self.job.parameters["upload"]["docker"]["name"]
        return "docker run --volume {}:{} {} ".format(
            upload_path_from_job(self.job), upload_path_from_job(self.job), name
        )

    def validate(self):
        super().validate()

        if "upload" not in self.job.parameters:
            # No upload required
            return

        self.path = upload_path_from_job(self.job)
        if "commands" not in self.job.parameters["upload"]:
            self.errors = "No 'commands' specified in upload block"
            raise JobError("No 'commands' specified in upload block")
        self.commands = self.job.parameters["upload"]["commands"]

        if "docker" not in self.job.parameters["upload"]:
            self.errors = "'upload' action currently only supports docker driver."
            raise JobError("'upload' action currently only supports docker driver.")
        self.local = self.job.parameters["upload"]["docker"].get("local", False)

        self.logger.debug("Creating upload file path.")
        self.run_cmd(f"mkdir -p {self.path}".split())

    def run(self, connection, max_end_time):
        # Update docker image if required
        if not self.local:
            self.run_cmd(
                "docker pull {}".format(self.job.parameters["upload"]["docker"]["name"])
            )
        upload_dir = upload_path_from_job(self.job)
        self.logger.debug(f"Running commands to upload artifacts in {upload_dir}")
        upload_num = 0
        for command in self.commands:
            upload_num += 1
            cmd = self.command_prefix() + command.replace("{DIR}", upload_dir)
            cmd_parsed = shlex.split(cmd)
            self.logger.debug(f"About to run {cmd_parsed}")
            start = time.time()
            stdout = self.parsed_command(cmd_parsed)
            self.logger.debug(f"Finished running {cmd_parsed}")
            duration = time.time() - start
            result_value = "pass" if not self.errors else "fail"
            result = {
                "definition": "lava",
                "case": f"upload-{upload_num}",
                "level": self.level,
                "extra": {"stdout": stdout},
                "result": str(result_value),
                "duration": "%.02f" % duration,
            }
            self.logger.results(result)
            self.logger.debug(f"Finished loop for {command}")
        self.logger.debug(f"Finished uploading {upload_dir}")
