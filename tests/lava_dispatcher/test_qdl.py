# Copyright 2026 Qualcomm Inc.
#
# Author: Milosz Wasilewski <milosz.wasilewski@oss.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from unittest.mock import patch

from lava_common.constants import RAMDUMP_TIMEOUT
from lava_common.exceptions import ConfigurationError, JobError
from lava_dispatcher.actions.boot.qdl import FlashQDLAction, QDLRamdumpAction
from lava_dispatcher.actions.deploy.apply_overlay import AppendOverlays
from lava_dispatcher.actions.deploy.download import DownloaderAction
from lava_dispatcher.actions.deploy.qdl import ExtractQcomflashAction
from tests.lava_dispatcher.test_basic import Factory, LavaDispatcherTestCase


class TestQDLBootAction(LavaDispatcherTestCase):
    @patch("lava_dispatcher.action.Action.parsed_command")
    @patch("lava_dispatcher.actions.boot.qdl.which")
    def test_qdl_job(self, which_mock, parsed_mock):
        which_mock.return_value = "/foo/qdl"
        parsed_mock.return_value = "qdl version v2.7"
        job = Factory().create_job("qcs6490-rb3gen2", "sample_jobs/qdl-boot.yaml")
        job.device.update({"board_qdl_id": "abcdef12"})
        job.device.update({"board_id": "abcdef12"})
        self.assertEqual(len(job.pipeline.actions), 4)
        job.validate()
        for action in job.pipeline.actions:
            action.validate()
            self.assertTrue(action.valid)
        description_ref = self.pipeline_reference("qdl.yaml", job=job)
        self.assertEqual(description_ref, job.pipeline.describe())

    @patch("lava_dispatcher.action.Action.parsed_command")
    @patch("lava_dispatcher.actions.boot.qdl.which")
    def test_qdl_job_empty_rootfs(self, which_mock, parsed_mock):
        which_mock.return_value = "/foo/qdl"
        parsed_mock.return_value = "qdl version v2.7"
        job = Factory().create_job(
            "qcs6490-rb3gen2", "sample_jobs/qdl-boot-empty-rootfs.yaml"
        )
        job.device.update({"board_qdl_id": "abcdef12"})
        job.device.update({"board_id": "abcdef12"})
        self.assertEqual(len(job.pipeline.actions), 5)
        with self.assertRaises(JobError):
            job.validate()

    @patch("lava_dispatcher.action.Action.parsed_command")
    @patch("lava_dispatcher.actions.boot.qdl.which")
    def test_qdl_job_overlays(self, which_mock, parsed_mock):
        which_mock.return_value = "/foo/qdl"
        parsed_mock.return_value = "qdl version v2.7"
        job = Factory().create_job(
            "qcs6490-rb3gen2", "sample_jobs/qdl-boot-overlays.yaml"
        )
        job.device.update({"board_qdl_id": "abcdef12"})
        job.device.update({"board_id": "abcdef12"})
        job.validate()
        description_ref = self.pipeline_reference("qdl-overlays.yaml", job=job)
        self.assertEqual(description_ref, job.pipeline.describe())

        deploy = job.pipeline.actions[0]
        # The downloader must not see "overlays": it would append them to the
        # qcomflash tarball instead of to the image inside it.
        downloader = deploy.pipeline.find_action(DownloaderAction)
        self.assertNotIn("overlays", downloader.params)

        extract = deploy.pipeline.find_action(ExtractQcomflashAction)
        self.assertEqual("disk-sdcard.img2", extract.rootfs_image)

        append = deploy.pipeline.find_action(AppendOverlays)
        # AppendOverlays looks the image up under this key, which is what
        # ExtractQcomflashAction publishes it as.
        self.assertEqual("qcomflash.rootfs", append.key)
        self.assertEqual(extract.rootfs_key, append.key)

        # one download for the tarball, one per non-lava overlay
        labels = [
            action.key
            for action in deploy.pipeline.actions
            if isinstance(action, DownloaderAction)
        ]
        self.assertEqual(
            ["qcomflash", "qcomflash.rootfs.modules", "qcomflash.rootfs.config"],
            labels,
        )

    @patch("lava_dispatcher.actions.boot.qdl.which")
    def test_qdl_job_overlays_and_apply_overlay(self, which_mock):
        which_mock.return_value = "/foo/qdl"
        job = Factory().create_job(
            "qcs6490-rb3gen2", "sample_jobs/qdl-boot-overlays-conflict.yaml"
        )
        job.device.update({"board_qdl_id": "abcdef12"})
        job.device.update({"board_id": "abcdef12"})
        with self.assertRaises(JobError) as exc:
            job.validate()
        self.assertIn("cannot be used together", str(exc.exception))

    @patch("lava_dispatcher.actions.boot.qdl.which")
    def test_qdl_job_no_qdl(self, which_mock):
        which_mock.return_value = ""
        job = Factory().create_job("qcs6490-rb3gen2", "sample_jobs/qdl-boot.yaml")
        job.device.update({"board_qdl_id": "abcdef12"})
        job.device.update({"board_id": "abcdef12"})
        self.assertEqual(len(job.pipeline.actions), 4)
        with self.assertRaises(ConfigurationError):
            job.validate()

    def test_qdl_job_qdl_1(self):
        self.assert_version_too_low("qdl version v1.0", "1.0")

    def validate_qdl_job(self, version_output):
        """
        Create and validate a qdl job with "qdl --version" reporting
        version_output. Returns the validated job.
        """
        with (
            patch("lava_dispatcher.actions.boot.qdl.which") as which_mock,
            patch("lava_dispatcher.action.Action.parsed_command") as parsed_mock,
        ):
            which_mock.return_value = "/foo/qdl"
            parsed_mock.return_value = version_output
            job = Factory().create_job("qcs6490-rb3gen2", "sample_jobs/qdl-boot.yaml")
            job.device.update({"board_qdl_id": "abcdef12"})
            job.device.update({"board_id": "abcdef12"})
            self.assertEqual(len(job.pipeline.actions), 4)
            job.validate()
            return job

    def assert_skipblock(self, version_output, expected):
        """
        Assert whether --skipblock=sha256, which requires qdl 2.7, is passed
        to qdl for the given "qdl --version" output.
        """
        job = self.validate_qdl_job(version_output)
        action = job.pipeline.find_action(FlashQDLAction)
        if expected:
            self.assertIn("--skipblock=sha256", action.base_command)
        else:
            self.assertNotIn("--skipblock=sha256", action.base_command)

    def assert_version_too_low(self, version_output, version):
        with self.assertRaises(ConfigurationError) as exc:
            self.validate_qdl_job(version_output)
        self.assertEqual(
            f"qdl version {version} is too low, 2.0 or higher is required",
            str(exc.exception),
        )

    def assert_version_unparsable(self, version_output):
        with self.assertRaises(ConfigurationError) as exc:
            self.validate_qdl_job(version_output)
        self.assertEqual(
            "Unable to parse the version of qdl at /foo/qdl", str(exc.exception)
        )

    def test_qdl_version_from_git_tag(self):
        # built from a git tag, the version is prefixed with "v"
        for version_output, skipblock in (
            ("qdl version v2.0", False),
            ("qdl version v2.6", False),
            ("qdl version v2.7", True),
            ("qdl version v2.8", True),
            # compared as numbers, not as strings
            ("qdl version v2.10", True),
            ("qdl version v3.0", True),
            ("qdl version v10.0", True),
        ):
            with self.subTest(version_output=version_output):
                self.assert_skipblock(version_output, skipblock)

    def test_qdl_version_from_debian_package(self):
        # installed from a Debian package, the version reported is the package
        # version: [epoch:]upstream_version[-debian_revision]
        for version_output, skipblock in (
            ("qdl version 2.0-1", False),
            ("qdl version 2.6-1~bpo13+1", False),
            ("qdl version 2.6.9-1", False),
            ("qdl version 2.7-1~bpo13+1", True),
            ("qdl version 2.7-1", True),
            ("qdl version 2.7.1-1+deb13u1", True),
            ("qdl version 1:2.7+dfsg-2", True),
            ("qdl version 3.0-1~bpo13+1", True),
            ("qdl version 1:3.0-1", True),
            # a Debian package built from a git tag keeps the "v" prefix
            ("qdl version v2.7-1~bpo13+1", True),
        ):
            with self.subTest(version_output=version_output):
                self.assert_skipblock(version_output, skipblock)

    def test_qdl_version_surrounded_by_other_output(self):
        for version_output in (
            "  qdl version v2.7  \n",
            "text before version\nqdl version v2.7\n",
            "qdl version v2.7\ntext after version\n",
            "text before version\nqdl version v2.7\ntext after version",
        ):
            with self.subTest(version_output=version_output):
                self.assert_skipblock(version_output, True)

    def test_qdl_version_too_low(self):
        for version_output, version in (
            ("qdl version v0.1", "0.1"),
            ("qdl version v1.0", "1.0"),
            ("qdl version v1.9", "1.9"),
            # compared as numbers, not as strings
            ("qdl version v1.10", "1.10"),
            ("qdl version 1.0-1~bpo13+1", "1.0"),
            ("qdl version 1:1.9-1", "1.9"),
        ):
            with self.subTest(version_output=version_output):
                self.assert_version_too_low(version_output, version)

    def test_qdl_version_unparsable(self):
        for version_output in (
            "",
            "qdl: unrecognized option '--version'",
            "qdl version unknown",
            "qdl version\n",
            "some other tool version v2.7",
        ):
            with self.subTest(version_output=version_output):
                self.assert_version_unparsable(version_output)


class TestQDLRamdump(LavaDispatcherTestCase):
    DEVICE = {
        "usb_vendor_id": "05c6",
        "board_qdl_id": "abcdef12",
        "actions": {"boot": {"methods": {"qdl": {"parameters": {"command": "qdl"}}}}},
    }

    def create_ramdump_action(self, dispatcher=None):
        job = self.create_simple_job(
            device_dict=self.DEVICE,
            job_parameters={"dispatcher": dispatcher or {}},
        )
        action = QDLRamdumpAction(job)
        action.parameters = {"ramdump": True}
        action.level = "1.1"
        return action

    def capture(
        self, action, publish_output="", crashed=("abcdef12",), rearm_mock=None
    ):
        """
        Drive the action against a worker on which the boards in `crashed` are
        in EDL crashdump mode, returning the results it recorded.

        Detection is stubbed the way usb_device_present behaves: it reports a
        product id only for the serial it was asked about.
        """
        results = []
        tmp = str(self.create_temporary_directory())

        def present(vendor, product_ids, serial=None):
            return "900e" if serial in crashed else None

        with (
            patch(
                "lava_dispatcher.actions.boot.qdl.usb_device_present",
                side_effect=present,
            ),
            patch("lava_dispatcher.actions.boot.qdl.which", return_value="/foo/qdl"),
            patch("lava_dispatcher.actions.boot.qdl.create_tarfile"),
            patch(
                "lava_dispatcher.actions.boot.qdl.compress_file",
                side_effect=lambda path, comp: f"{path}.{comp}",
            ),
            patch.object(action, "mkdtemp", return_value=tmp),
            patch.object(action, "run_cmd", return_value=0),
            patch.object(action, "parsed_command", return_value=publish_output),
            patch.object(action.logger, "results", side_effect=results.append),
            # in a real job this alarm is cleared by Job.cleanup()'s own
            # timeout context, which these tests are not running inside
            (
                patch.object(action.job, "rearm_cleanup_timeout", rearm_mock)
                if rearm_mock is not None
                else patch.object(action.job, "rearm_cleanup_timeout")
            ),
        ):
            action._capture_if_crashed()
        return results

    def capture_one(self, action, publish_output=""):
        (result,) = self.capture(action, publish_output=publish_output)
        return result

    def object_name(self, action):
        return f"ramdump-{action.job.job_id}.tar.gz"

    def test_url_from_last_line_of_output(self):
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        result = self.capture_one(
            action, publish_output="uploading...\nhttps://dumps.example.com/a.tar.gz\n"
        )
        self.assertEqual("pass", result["result"])
        self.assertEqual(self.object_name(action), result["extra"]["ramdump"])
        self.assertEqual(
            "https://dumps.example.com/a.tar.gz", result["extra"]["ramdump_url"]
        )

    def test_any_url_scheme_is_accepted(self):
        for url in (
            "https://dumps.example.com/a.tar.gz",
            "s3://bucket/a.tar.gz",
            "scp://host/srv/a.tar.gz",
            "file:///srv/dumps/a.tar.gz",
        ):
            with self.subTest(url=url):
                action = self.create_ramdump_action(
                    {"ramdump_publish_command": "/bin/true"}
                )
                result = self.capture_one(action, publish_output=url)
                self.assertEqual(url, result["extra"]["ramdump_url"])

    def test_no_url_printed_still_passes(self):
        # Printing a URL is optional, not a failure.
        for output in ("", "uploaded 4.2GB in 31s\n", "   \n"):
            with self.subTest(output=output):
                action = self.create_ramdump_action(
                    {"ramdump_publish_command": "/bin/true"}
                )
                result = self.capture_one(action, publish_output=output)
                self.assertEqual("pass", result["result"])
                self.assertNotIn("ramdump_url", result["extra"])

    def test_chatter_is_not_mistaken_for_a_url(self):
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        result = self.capture_one(
            action, publish_output="done: uploaded to the archive"
        )
        self.assertNotIn("ramdump_url", result["extra"])

    def test_without_a_publish_command_nothing_is_captured(self):
        # The dump would go to the job's temporary directory, which LAVA
        # deletes as teardown ends, so with nowhere to deliver it there is
        # nothing to be gained from spending teardown making one.
        action = self.create_ramdump_action()
        results, rearm = self.drive(action, run_cmd={"return_value": 0})
        self.assertEqual([], results)
        rearm.assert_not_called()

    def test_without_a_publish_command_qdl_is_not_run(self):
        action = self.create_ramdump_action()
        with (
            patch(
                "lava_dispatcher.actions.boot.qdl.usb_device_present",
                return_value="900e",
            ),
            patch("lava_dispatcher.actions.boot.qdl.which") as which,
            patch.object(action, "run_cmd") as run_cmd,
        ):
            action.cleanup(connection=None)
        run_cmd.assert_not_called()
        which.assert_not_called()
        # and the job stays free to dump if a later action can deliver one
        self.assertFalse(getattr(action.job, action.JOB_GUARD, False))

    def test_detection_is_scoped_to_this_board(self):
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        with patch(
            "lava_dispatcher.actions.boot.qdl.usb_device_present", return_value=None
        ) as present:
            action._capture_if_crashed()
        # The serial must be part of the query, not checked afterwards.
        self.assertEqual("abcdef12", present.call_args.kwargs["serial"])

    def test_another_board_in_crashdump_is_ignored(self):
        # Two other boards on the worker are in EDL crashdump mode. Ours is
        # not, so there is nothing to capture and no result to record.
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        self.assertEqual([], self.capture(action, crashed=("AAAAAAAA", "BBBBBBBB")))
        self.assertFalse(getattr(action.job, action.JOB_GUARD, False))

    def test_our_board_among_several_in_crashdump(self):
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        (result,) = self.capture(action, crashed=("AAAAAAAA", "abcdef12"))
        self.assertEqual("pass", result["result"])
        self.assertTrue(getattr(action.job, action.JOB_GUARD, False))

    def test_board_without_a_qdl_id_is_skipped(self):
        # Without a serial the board cannot be told apart from the others on
        # the worker, so capturing would risk dumping somebody else's board.
        for board_qdl_id in ("", "00000000"):
            with self.subTest(board_qdl_id=board_qdl_id):
                job = self.create_simple_job(
                    device_dict={**self.DEVICE, "board_qdl_id": board_qdl_id},
                    job_parameters={"dispatcher": {}},
                )
                action = QDLRamdumpAction(job)
                action.parameters = {"ramdump": True}
                action.level = "1.1"
                self.assertEqual([], self.capture(action))
                self.assertFalse(getattr(action.job, action.JOB_GUARD, False))

    def test_only_one_ramdump_per_job(self):
        # A job may hold several qdl boot actions, each with its own ramdump
        # action. Between them they must dump once: they all publish under
        # ramdump-<job_id>, so a second would overwrite the first.
        job = self.create_simple_job(
            device_dict=self.DEVICE,
            job_parameters={"dispatcher": {"ramdump_publish_command": "/bin/true"}},
        )
        actions = []
        for level in ("2.6", "3.6", "4.6"):
            action = QDLRamdumpAction(job)
            action.parameters = {"ramdump": True}
            action.level = level
            actions.append(action)

        results = []
        for action in actions:
            results += self.capture(action)

        self.assertEqual(1, len(results))
        self.assertEqual("2.6", results[0]["level"])

    def test_ramdump_only_runs_at_cleanup(self):
        # Checking during run() as well would dump from the first qdl boot
        # action, before the board has had the rest of the job to crash.
        self.assertNotIn("run", QDLRamdumpAction.__dict__)
        action = self.create_ramdump_action()
        with patch.object(action, "_capture_if_crashed") as capture:
            action.cleanup(connection=None)
        capture.assert_called_once_with()

    def test_capture_rearms_the_cleanup_alarm(self):
        # The capture runs under its own timeout window, which disarms the
        # shared job-cleanup alarm on exit; the rest of teardown must not be
        # left running unguarded.
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        with patch.object(action.job, "rearm_cleanup_timeout") as rearm:
            self.capture(action, rearm_mock=rearm)
        rearm.assert_called_once_with()

    def drive(self, action, run_cmd=None, parsed_command=None):
        """
        Run a capture on a crashed board with the two external commands - qdl
        itself and the publish command - under the caller's control.
        """
        results = []
        with (
            patch(
                "lava_dispatcher.actions.boot.qdl.usb_device_present",
                return_value="900e",
            ),
            patch("lava_dispatcher.actions.boot.qdl.which", return_value="/foo/qdl"),
            patch("lava_dispatcher.actions.boot.qdl.create_tarfile"),
            patch(
                "lava_dispatcher.actions.boot.qdl.compress_file",
                side_effect=lambda path, comp: f"{path}.{comp}",
            ),
            patch.object(
                action, "mkdtemp", return_value=str(self.create_temporary_directory())
            ),
            patch.object(action, "run_cmd", **(run_cmd or {"return_value": 0})),
            patch.object(
                action, "parsed_command", **(parsed_command or {"return_value": ""})
            ),
            patch.object(action.logger, "results", side_effect=results.append),
            patch.object(action.job, "rearm_cleanup_timeout") as rearm,
        ):
            # must not escape into the rest of teardown
            action.cleanup(connection=None)
        return results, rearm

    def test_ramdump_timeout_covers_the_publish(self):
        # Uploading a multi-gigabyte dump can take longer than the shared
        # teardown budget, so the window has to still be open by then. If the
        # publish were outside it, this JobError would escape cleanup().
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        results, rearm = self.drive(
            action, parsed_command={"side_effect": JobError("timed out")}
        )
        (result,) = results
        self.assertEqual("fail", result["result"])
        rearm.assert_called_once_with()

    def test_ramdump_timeout_covers_the_capture(self):
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        results, rearm = self.drive(
            action, run_cmd={"side_effect": JobError("timed out")}
        )
        (result,) = results
        self.assertEqual("fail", result["result"])
        self.assertNotIn("extra", result)
        rearm.assert_called_once_with()

    def test_ramdump_timeout_is_the_window(self):
        # The configured budget, not the action's own timeout, bounds the work.
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        action.parameters["ramdump_timeout"] = 1234
        seen = []
        self.drive(
            action,
            run_cmd={
                "side_effect": lambda *a, **k: seen.append(action.timeout.duration) or 0
            },
        )
        self.assertEqual([1234], seen)

    def test_ramdump_timeout_defaults(self):
        action = self.create_ramdump_action({"ramdump_publish_command": "/bin/true"})
        seen = []
        self.drive(
            action,
            run_cmd={
                "side_effect": lambda *a, **k: seen.append(action.timeout.duration) or 0
            },
        )
        self.assertEqual([RAMDUMP_TIMEOUT], seen)
