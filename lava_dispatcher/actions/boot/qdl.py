# Copyright (C) 2026 Qualcomm Inc.
#
# Author: Milosz Wasilewski <milosz.wasilewski@oss.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

import os
import re
from shlex import split as shlex_split
from typing import TYPE_CHECKING

from lava_common.constants import RAMDUMP_TIMEOUT
from lava_common.exceptions import ConfigurationError, JobError
from lava_dispatcher.action import Action, Pipeline
from lava_dispatcher.connections.serial import ConnectDevice
from lava_dispatcher.logical import RetryAction
from lava_dispatcher.power import ResetDevice
from lava_dispatcher.utils.compression import compress_file, create_tarfile
from lava_dispatcher.utils.shell import which
from lava_dispatcher.utils.udev import WaitQDLDeviceAction, usb_device_present

if TYPE_CHECKING:
    from lava_dispatcher.job import Job

# The version reported by "qdl --version" depends on how qdl was built:
#   "qdl version v2.7"          built from a git tag
#   "qdl version 2.7-1~bpo13+1" built from a Debian package, which reports the
#                               Debian package version, i.e.
#                               [epoch:]upstream_version[-debian_revision]
# Only the major and minor numbers of the upstream version are of interest,
# anything after them is ignored.
QDL_VERSION_PATTERN = re.compile(
    r"qdl version (?:\d+:)?v?(?P<major>\d+)\.(?P<minor>\d+)"
)
# EDL product ids (under vendor 0x05c6) a crashed board can re-enumerate under,
# which vary by SoC. 0x9008 (Firehose) is the normal flashing mode, not a crash,
# and is intentionally excluded. See linux-msm/qdl usb_is_edl_pid().
RAMDUMP_PRODUCT_IDS = ("900e", "901d", "90db")
# A ramdump publish command may print the URL of the dump it uploaded as the
# last line of its output. Any scheme will do, so this only looks for the
# shape of a URL, which is enough to tell one from a stray line of output.
RAMDUMP_URL_PATTERN = re.compile(r"^\S+://\S+$")


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
            self.errors_add('"enter-commands" is not defined')
        elif not isinstance(parameters["enter-commands"], list):
            self.errors_add('"enter-commands" should be a list')

    def run(self, connection, max_end_time):
        connection = super().run(connection, max_end_time)
        parameters = self.job.device["actions"]["boot"]["methods"]["qdl"]["parameters"]
        for _, cmd in enumerate(parameters["enter-commands"]):
            # this should run on the dispatcher
            self.run_cmd(cmd)


class FlashQDLAction(Action):
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
            qdl_command = boot["parameters"]["command"]
            qdl_binary = which(qdl_command)
            if not qdl_binary:
                self.logger.error("%r was not found in PATH", qdl_command)
                raise ConfigurationError(f"qdl not installed: {qdl_command} not found")
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
            qdl_output = self.parsed_command(version_command)
            match = QDL_VERSION_PATTERN.search(qdl_output)
            if not match:
                self.logger.error(
                    "Unable to parse the version reported by '%s --version': %r",
                    qdl_binary,
                    qdl_output.strip(),
                )
                raise ConfigurationError(
                    f"Unable to parse the version of qdl at {qdl_binary}"
                )
            version = (int(match.group("major")), int(match.group("minor")))
            self.logger.info("Detected qdl version %d.%d at %s", *version, qdl_binary)

            if version < (2, 0):
                # version lower than 2.0 is unsupported
                self.logger.error("qdl version 2.0 or higher is required")
                raise ConfigurationError(
                    "qdl version %d.%d is too low, 2.0 or higher is required" % version
                )

            if version >= (2, 7):
                # --skipblock=sha256 is available
                self.base_command.append("--skipblock=sha256")

            if qdl_debug:
                self.base_command.extend(["--debug"])
            if qdl_storage:
                self.base_command.extend(["--storage", qdl_storage])
            if self.job.device["board_qdl_id"] == "00000000":
                self.errors_add("[FLASH_QDL] board_qdl_id unset")
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
        except (KeyError, TypeError) as exc:
            self.errors_add(f"Invalid parameters for {self.name}: {exc}")
        self.exec_list.append(self.base_command)
        if not self.exec_list:
            self.errors_add("No QDL commands to execute")

    def run(self, connection, max_end_time):
        connection = super().run(connection, max_end_time)

        qcomflash_dir = self.get_namespace_data(
            action="qdl-deploy", label="qdl-directory", key="directory"
        )

        # at this stage it's assumed that qcomflash tarball is decompressed
        for _, qdl_command in enumerate(self.exec_list):
            qdl_cmd = " ".join(qdl_command)
            flash_dir = os.path.join(qcomflash_dir.as_posix(), self.qcomflash_path)
            self.run_cmd(qdl_cmd.split(" "), cwd=flash_dir)

        return connection


class QDLRamdumpAction(Action):
    name = "qdl-ramdump"
    description = "capture a ramdump if the board crashed into EDL crashdump mode"
    summary = "capture qdl ramdump on crash"

    # Guards the capture. Kept on the job rather than on the action because a
    # job may contain several qdl boot actions, each with its own instance of
    # this action, and between them they must dump at most once.
    JOB_GUARD = "qdl_ramdump_captured"

    def cleanup(self, connection, max_end_time=None):
        # Only at teardown, never during run(). cleanup() is walked for every
        # action in pipeline order before the finalize PowerOff, and Job.run()
        # calls it in a finally, so this runs exactly once whether the job
        # passed, failed or was interrupted - by which time the board has had
        # every chance to crash.
        self._capture_if_crashed()
        super().cleanup(connection, max_end_time)

    def publish_command(self):
        """The worker's configured way of delivering a ramdump, if it has one."""
        return self.job.parameters.get("dispatcher", {}).get("ramdump_publish_command")

    def _capture_if_crashed(self):
        if getattr(self.job, self.JOB_GUARD, False):
            return
        if not self.parameters.get("ramdump"):
            return
        # A dump is written to the job's temporary directory, which LAVA
        # deletes the moment teardown finishes. Without somewhere to send it
        # there is nothing to be gained from spending several minutes of that
        # teardown making one only to throw it away.
        if not self.publish_command():
            self.logger.warning(
                "No 'ramdump_publish_command' dispatcher configuration - "
                "nowhere to deliver a ramdump, so not capturing one"
            )
            return

        usb_vendor_id = self.job.device.get("usb_vendor_id")
        board_qdl_id = self.job.device.get("board_qdl_id")
        if not board_qdl_id or board_qdl_id == "00000000":
            self.logger.warning(
                "Device has no 'board_qdl_id', so this board cannot be told "
                "apart from the others on this worker - skipping ramdump"
            )
            return
        # A crashed board can re-enumerate under any of several EDL dump product
        # ids depending on the SoC, so check them all. A device may also pin an
        # extra id via 'qdl_ramdump_product_id'.
        ramdump_product_ids = set(RAMDUMP_PRODUCT_IDS)
        configured = self.job.device.get("qdl_ramdump_product_id")
        if configured:
            ramdump_product_ids.add(str(configured))
        # Match the serial too. A worker drives several boards, and any one of
        # them may be sitting in crashdump mode from an earlier crash; vendor
        # and product alone would make every job on the worker believe its own
        # board had crashed.
        product_id = usb_device_present(
            usb_vendor_id, ramdump_product_ids, serial=board_qdl_id
        )
        if not product_id:
            # this board is not in crashdump mode - do nothing
            self.logger.debug(
                "No crashdump device with serial %s, this board did not crash",
                board_qdl_id,
            )
            return

        # From here on the job has dumped; no other qdl boot action may.
        setattr(self.job, self.JOB_GUARD, True)
        self.logger.warning(
            "Board is in EDL crashdump mode (%s:%s serial %s) - capturing ramdump",
            usb_vendor_id,
            product_id,
            board_qdl_id,
        )
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

        # ramdump_timeout covers all of it: dumping the board, packaging what
        # came out and handing that to the publish command. A full DDR dump
        # routinely takes several minutes and the upload after it can take
        # longer still, far more than the shared job-cleanup alarm
        # (CLEANUP_TIMEOUT, 300s) allows. Exceeding that alarm would also
        # starve the finalize power-off, so run the lot under a window of its
        # own and re-arm the shared alarm afterwards to keep the rest of
        # teardown bounded.
        self.timeout.duration = int(
            self.parameters.get("ramdump_timeout", RAMDUMP_TIMEOUT)
        )
        extra = None
        try:
            with self.timeout(None, None):
                extra = self._capture_and_publish(command, out_dir)
        except JobError as exc:
            # Out of time somewhere in there; report it rather than let it
            # propagate out of cleanup() and take the rest of teardown with it.
            self.logger.error("qdl ramdump did not finish: %s", exc)
        finally:
            # The window above disarms the shared cleanup alarm on exit.
            self.job.rearm_cleanup_timeout()

        # Record a test result linking to the ramdump so an engineer can find
        # it from the job results.
        results = {
            "definition": "lava",
            "case": "ramdump",
            "level": self.level,
            "result": "pass" if extra else "fail",
        }
        if extra:
            results["extra"] = extra
        self.logger.results(results)

    def _capture_and_publish(self, command, out_dir):
        """
        Dump the board, bundle the per-segment dump into a single object and
        hand it to the worker-configured publish command, if there is one.

        Returns what to record about the dump, or None when there is no dump
        to describe. Runs inside the caller's ramdump_timeout window, so
        anything here may be interrupted by it.
        """
        # allow_fail so a non-zero qdl exit can never block teardown.
        rc = self.run_cmd(command, allow_fail=True)
        if rc != 0:
            self.logger.error("qdl ramdump failed (rc=%s)", rc)
            return None
        self.logger.info("Ramdump captured")

        # Ramdumps are large and highly compressible, so compress the tarball
        # before publishing.
        archive = os.path.join(self.mkdtemp(), "ramdump-%s.tar" % self.job.job_id)
        create_tarfile(out_dir, archive, arcname="ramdump")
        archive = compress_file(
            archive, self.parameters.get("ramdump_compression", "gz")
        )
        object_name = os.path.basename(archive)

        url = self._publish(archive, object_name)
        extra = {"ramdump": object_name}
        if url:
            extra["ramdump_url"] = url
        return extra

    def _publish(self, archive_path, object_name):
        """
        Hand the ramdump archive to the worker-configured publish command.

        Storing/delivering ramdumps is site specific (S3, scp, a shared NFS
        dir, ...), so LAVA core knows nothing about it. The worker's private
        dispatcher configuration provides a single ``ramdump_publish_command``;
        LAVA passes the archive path and job id in the environment. Any
        credentials the command needs live wholly inside that command.

        The command may print the URL of the uploaded dump as the last line of
        its output, which is then recorded in the ramdump result. Its output is
        captured rather than streamed to the job log, so a token or a signed
        URL it prints along the way is not exposed by a publicly visible job.

        Returns the URL it printed, if it printed one. Only called once a
        publish command is known to be configured.
        """
        command = self.publish_command()
        env = dict(os.environ)
        env["LAVA_RAMDUMP_FILE"] = archive_path
        env["LAVA_RAMDUMP_NAME"] = object_name
        env["LAVA_JOB_ID"] = str(self.job.job_id)

        self.logger.info("Publishing ramdump via configured command")
        output = self.parsed_command(
            shlex_split(command), allow_fail=True, show_output=False, env=env
        )
        url = self._published_url(output)
        self.logger.info("Ramdump published (%s)", url or object_name)
        return url

    def _published_url(self, output):
        """
        Take the URL the publish command printed as the last line of its
        output. Only the last line is considered, so a chatty uploader cannot
        put arbitrary output into the result. Printing no URL is fine; the
        dump is simply recorded by name.
        """
        lines = output.strip().splitlines()
        url = lines[-1].strip() if lines else ""
        if not RAMDUMP_URL_PATTERN.match(url):
            self.logger.debug("Ramdump publish command printed no URL")
            return None
        return url
