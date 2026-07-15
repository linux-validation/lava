# Copyright (C) 2026 Qualcomm Inc.
#
# Author: Milosz Wasilewski <milosz.wasilewski@oss.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from lava_common.constants import RAMDUMP_TIMEOUT
from lava_common.exceptions import ConfigurationError, JobError
from lava_dispatcher.action import Action, Pipeline
from lava_dispatcher.connections.serial import ConnectDevice
from lava_dispatcher.logical import RetryAction
from lava_dispatcher.power import ResetDevice
from lava_dispatcher.utils.compression import compress_file, create_tarfile
from lava_dispatcher.utils.qdl import OptionalContainerQdlAction
from lava_dispatcher.utils.shell import which
from lava_dispatcher.utils.udev import WaitQDLDeviceAction, usb_device_present

if TYPE_CHECKING:
    from lava_dispatcher.job import Job

# EDL product ids (under vendor 0x05c6) a crashed board can re-enumerate under,
# which vary by SoC. 0x9008 (Firehose) is the normal flashing mode, not a crash,
# and is intentionally excluded. See linux-msm/qdl usb_is_edl_pid().
RAMDUMP_PRODUCT_IDS = ("900e", "901d", "90db")


class BootQDLRetry(RetryAction):
    name = "boot-qdl-retry"
    description = "boot to EDL mode using any available method"
    summary = "boot to EDL mode"

    def populate(self, parameters):
        self.pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        self.pipeline.add_action(ConnectDevice(self.job))
        self.pipeline.add_action(ResetDevice(self.job))
        self.pipeline.add_action(EnterQDL(self.job))
        self.pipeline.add_action(WaitQDLDeviceAction(self.job))
        self.pipeline.add_action(FlashQDLAction(self.job))
        self.pipeline.add_action(QDLRamdumpAction(self.job))


class EnterQDL(Action):
    name = "enter-qdl"
    description = "enter QDL mode"
    summary = "enter QDL mode"

    def validate(self):
        super().validate()
        parameters = self.job.device["actions"]["boot"]["methods"]["qdl"]["parameters"]
        if "enter-commands" not in parameters:
            self.errors = '"enter-commands" is not defined'
        elif not isinstance(parameters["enter-commands"], list):
            self.errors = '"enter-commands" should be a list'

    def run(self, connection, max_end_time):
        connection = super().run(connection, max_end_time)
        parameters = self.job.device["actions"]["boot"]["methods"]["qdl"]["parameters"]
        for _, cmd in enumerate(parameters["enter-commands"]):
            # this should run on the dispatcher
            self.run_cmd(cmd)


class FlashQDLAction(OptionalContainerQdlAction):
    name = "flash-qdl"
    description = "use qdl to flash flat build to the board"
    summary = "use qdl to flash flat build to the board"

    def __init__(self, job: Job, params=None):
        super().__init__(job)
        self.base_command = []
        self.exec_list = []
        self.board_qdl_id = "00000000"
        self.board_id = "0000000000"
        self.usb_vendor_id = "0000"
        self.usb_product_id = "0000"
        self.qcomflash_path = None  # path inside tarball where .XML files are located
        self.params = params

    def validate(self):
        super().validate()
        # - boot:
        #     firehose_program: "prog_firehose_ddr.elf"
        #     rawprogram: "rawprogram*.xml"
        #     patch: "patch*.xml"
        #     storage: "emmc"
        #     timeout:
        #       minutes: 5
        #     method: qdl

        try:
            boot = self.job.device["actions"]["boot"]["methods"]["qdl"]
            if self.is_container():
                # qdl lives inside the container image, not on the dispatcher
                qdl_binary = boot["parameters"]["command"]
            else:
                qdl_binary = which(boot["parameters"]["command"])
                if not qdl_binary:
                    self.logger.error("qdl not installed")
                    raise ConfigurationError("qdl not installed")
            # all paths are relative to the tarball
            qdl_flashing_prog_path = self.parameters["firehose_program"]
            qdl_rawprogram_path = self.parameters["rawprogram"]
            qdl_patch_path = self.parameters["patch"]
            qdl_storage = self.parameters.get("storage", None)
            qdl_debug = self.parameters.get("debug", False)
            self.qcomflash_path = self.parameters.get("path", ".")
            self.base_command = [qdl_binary]
            # execute qdl to detect version
            version_command = [qdl_binary, "--version"]
            qdl_output = self.parsed_qdl_command(version_command)
            # qdl version v2.7
            match = re.search(
                r"qdl\ version\ v(?P<version_major>\d+).(?P<version_minor>\d+)",
                qdl_output,
            )
            if match:
                version_major = int(match.group("version_major"))
                version_minor = int(match.group("version_minor"))
                if version_major < 2:
                    # version lower than 2.0 is unsupported
                    self.logger.error("qdl version 2.0 or higher is required")
                    self.logger.error(
                        f"Detected qdl version: v{version_major}.{version_minor}"
                    )
                    raise ConfigurationError("qdl version too low")

                if version_major == 2 and version_minor >= 7:
                    # --skipblock=sha256 is available
                    self.base_command.append("--skipblock=sha256")
            else:
                raise ConfigurationError("qdl not installed")

            if qdl_debug:
                self.base_command.extend(["--debug"])
            if qdl_storage:
                self.base_command.extend(["--storage", qdl_storage])
            if self.job.device["board_qdl_id"] == "00000000":
                self.errors = "[FLASH_QDL] board_qdl_id unset"
            self.usb_vendor_id = self.job.device["usb_vendor_id"]
            self.usb_product_id = self.job.device["usb_product_id"]
            self.board_qdl_id = self.job.device["board_qdl_id"]
            self.board_id = self.job.device["board_id"]
            self.base_command.extend(["--serial", self.board_qdl_id])
            self.base_command.extend(
                [qdl_flashing_prog_path, qdl_rawprogram_path, qdl_patch_path]
            )
        except AttributeError as exc:
            raise ConfigurationError(exc)
        except (KeyError, TypeError):
            self.errors = "Invalid parameters for %s" % self.name
        self.exec_list.append(self.base_command)
        if not self.exec_list:
            self.errors = "No QDL commands to execute"

    def run(self, connection, max_end_time):
        connection = super().run(connection, max_end_time)

        qcomflash_dir = self.get_namespace_data(
            action="qdl-deploy", label="qdl-directory", key="directory"
        )

        # at this stage it's assumed that qcomflash tarball is decompressed
        for qdl_command in self.exec_list:
            flash_dir = os.path.join(qcomflash_dir.as_posix(), self.qcomflash_path)
            self.run_qdl(qdl_command, flash_dir)

        return connection


class QDLRamdumpAction(Action):
    name = "qdl-ramdump"
    description = "capture a ramdump if the board crashed into EDL crashdump mode"
    summary = "capture qdl ramdump on crash"

    def __init__(self, job: Job):
        super().__init__(job)
        # Idempotency guard, set only once a dump has actually been attempted so
        # this action does its work at most once per job.
        self.captured = False

    def run(self, connection, max_end_time):
        connection = super().run(connection, max_end_time)
        # Opportunistic check on the normal path; the authoritative, always-last
        # check happens at teardown in cleanup().
        self._capture_if_crashed(in_cleanup=False)
        return connection

    def cleanup(self, connection, max_end_time=None):
        # cleanup() is walked for every action during job teardown, in pipeline
        # order and before the finalize PowerOff. So this runs last and still
        # runs when an earlier action crashed and run() was never reached.
        self._capture_if_crashed(in_cleanup=True)
        super().cleanup(connection, max_end_time)

    def _capture_if_crashed(self, in_cleanup):
        if self.captured:
            return
        if not self.parameters.get("ramdump"):
            return

        usb_vendor_id = self.job.device.get("usb_vendor_id")
        # A crashed board can re-enumerate under any of several EDL dump product
        # ids depending on the SoC, so check them all. A device may also pin an
        # extra id via 'qdl_ramdump_product_id'.
        ramdump_product_ids = set(RAMDUMP_PRODUCT_IDS)
        configured = self.job.device.get("qdl_ramdump_product_id")
        if configured:
            ramdump_product_ids.add(str(configured))
        product_id = usb_device_present(usb_vendor_id, ramdump_product_ids)
        if not product_id:
            # ramdump mode has not appeared - do nothing
            return

        # From here on the action has fired; never attempt more than once.
        self.captured = True
        self.logger.warning(
            "Board is in EDL crashdump mode (%s:%s) - capturing ramdump",
            usb_vendor_id,
            product_id,
        )
        board_qdl_id = self.job.device.get("board_qdl_id")
        qdl_binary = which(
            self.job.device["actions"]["boot"]["methods"]["qdl"]["parameters"][
                "command"
            ]
        )
        out_dir = os.path.join(self.mkdtemp(), "ramdump")
        os.makedirs(out_dir, exist_ok=True)
        command = [qdl_binary, "ramdump", "--serial", str(board_qdl_id), "-o", out_dir]
        segments = self.parameters.get("ramdump_segments")
        if segments:
            command.append(",".join(segments))

        # Bound the (potentially multi-minute) capture by its own budget.
        self.timeout.duration = int(
            self.parameters.get("ramdump_timeout", RAMDUMP_TIMEOUT)
        )
        rc = self._run_capture(command, in_cleanup)
        if rc != 0:
            self.logger.error("qdl ramdump failed (rc=%s)", rc)
            self.logger.results(
                {
                    "definition": "lava",
                    "case": "ramdump",
                    "level": self.level,
                    "result": "fail",
                }
            )
            return
        self.logger.info("Ramdump captured")

        # Bundle the per-segment dump into a single object and hand it to the
        # worker-configured publish command (if any). Ramdumps are large and
        # highly compressible, so compress the tarball before publishing.
        archive = os.path.join(self.mkdtemp(), "ramdump-%s.tar" % self.job.job_id)
        create_tarfile(out_dir, archive, arcname="ramdump")
        archive = compress_file(
            archive, self.parameters.get("ramdump_compression", "gz")
        )
        object_name = os.path.basename(archive)
        published = self._publish(archive, object_name)

        # Record a test result linking to the ramdump filename so an engineer
        # can find it from the job results.
        self.logger.results(
            {
                "definition": "lava",
                "case": "ramdump",
                "level": self.level,
                "result": "pass" if published else "fail",
                "extra": {"ramdump": object_name},
            }
        )

    def _run_capture(self, command, in_cleanup):
        """
        Run the qdl ramdump command, bounded by ``ramdump_timeout``.

        A full DDR dump routinely takes several minutes. During teardown
        (``in_cleanup``) this action runs under the shared job-cleanup alarm
        (CLEANUP_TIMEOUT, 300s), which is too short and, if exceeded, would also
        starve the finalize power-off. So run the capture under its own timeout
        window and then re-arm the shared guard so the rest of teardown stays
        bounded. On the opportunistic run()-time path the action's own (ample)
        run window already applies, so run directly. Returns the qdl exit code,
        or None if the capture timed out.
        """
        try:
            if not in_cleanup:
                return self.run_cmd(command, allow_fail=True)
            try:
                with self.timeout(None, None):
                    # allow_fail so a non-zero qdl exit can never block teardown.
                    return self.run_cmd(command, allow_fail=True)
            finally:
                # The window above disarms the shared cleanup alarm on exit.
                self.job.rearm_cleanup_timeout()
        except JobError as exc:
            # Capture exceeded ramdump_timeout; report failure rather than
            # propagating out of run()/cleanup().
            self.logger.error("qdl ramdump did not finish: %s", exc)
            return None

    def _publish(self, archive_path, object_name):
        """
        Hand the ramdump archive to the worker-configured publish command.

        Storing/delivering ramdumps is site specific (S3, scp, a shared NFS
        dir, ...), so LAVA core knows nothing about it. The worker's private
        dispatcher configuration provides a single ``ramdump_publish_command``;
        LAVA runs it via run_cmd_silent(), which logs neither its output nor
        its arguments, and passes the archive path and job id in the
        environment. Any credentials the command needs live wholly inside that
        command and never reach the logs.
        """
        command = self.job.parameters.get("dispatcher", {}).get(
            "ramdump_publish_command"
        )
        if not command:
            self.logger.warning(
                "No 'ramdump_publish_command' dispatcher configuration - "
                "ramdump captured locally but not published"
            )
            return None

        env = dict(os.environ)
        env["LAVA_RAMDUMP_FILE"] = archive_path
        env["LAVA_RAMDUMP_NAME"] = object_name
        env["LAVA_JOB_ID"] = str(self.job.job_id)

        self.logger.info("Publishing ramdump via configured command")
        if not self.run_cmd_silent(command, env=env):
            self.logger.error("Ramdump publish command failed")
            return None

        self.logger.info("Ramdump published (%s)", object_name)
        return object_name
