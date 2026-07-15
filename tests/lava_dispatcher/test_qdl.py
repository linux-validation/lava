# Copyright 2026 Qualcomm Inc.
#
# Author: Milosz Wasilewski <milosz.wasilewski@oss.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from unittest.mock import patch

from lava_common.exceptions import ConfigurationError, JobError
from lava_dispatcher.utils.containers import DockerDriver, NullDriver
from lava_dispatcher.utils.qdl import OptionalContainerQdlAction
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

    @patch("lava_dispatcher.actions.boot.qdl.which")
    def test_qdl_job_no_qdl(self, which_mock):
        which_mock.return_value = ""
        job = Factory().create_job("qcs6490-rb3gen2", "sample_jobs/qdl-boot.yaml")
        job.device.update({"board_qdl_id": "abcdef12"})
        job.device.update({"board_id": "abcdef12"})
        self.assertEqual(len(job.pipeline.actions), 4)
        with self.assertRaises(ConfigurationError):
            job.validate()

    @patch("lava_dispatcher.action.Action.parsed_command")
    @patch("lava_dispatcher.actions.boot.qdl.which")
    def test_qdl_job_qdl_1(self, which_mock, parsed_mock):
        which_mock.return_value = "/foo/qdl"
        parsed_mock.return_value = "qdl version v1.0"
        job = Factory().create_job("qcs6490-rb3gen2", "sample_jobs/qdl-boot.yaml")
        job.device.update({"board_qdl_id": "abcdef12"})
        job.device.update({"board_id": "abcdef12"})
        self.assertEqual(len(job.pipeline.actions), 4)
        with self.assertRaises(ConfigurationError):
            job.validate()


class TestQDLActionDriver(LavaDispatcherTestCase):
    def create_action(self, action_parameters=None):
        action = OptionalContainerQdlAction(
            self.create_simple_job(job_parameters={"dispatcher": {}})
        )
        action.parameters = action_parameters or {}
        return action

    def test_qdl_null_driver(self):
        action = self.create_action()
        self.assertIsInstance(action.driver, NullDriver)

    def test_qdl_docker_driver(self):
        action = self.create_action({"docker": {"image": "qualcomm/qdl:latest"}})
        self.assertIsInstance(action.driver, DockerDriver)

    @patch.object(OptionalContainerQdlAction, "run_cmd")
    def test_native_qdl_cmd(self, mock_cmd):
        action = self.create_action()
        action.run_qdl(["qdl", "prog", "raw", "patch"], "/flash")
        mock_cmd.assert_called_with(
            ["qdl", "prog", "raw", "patch"],
            False,
            None,
            cwd="/flash",
        )

    @patch.object(OptionalContainerQdlAction, "run_cmd")
    def test_docker_qdl_local_cmd(self, mock_cmd):
        action = self.create_action({"docker": {"image": "qualcomm/qdl:latest"}})
        action.run_qdl(["qdl", "prog", "raw", "patch"], "/flash")
        mock_cmd.assert_called_with(
            [
                "docker",
                "run",
                "--privileged",
                "--volume=/dev:/dev",
                "--net=host",
                "--volume=/flash:/flash",
                "--workdir=/flash",
                "--rm",
                "--init",
                "qualcomm/qdl:latest",
                "qdl",
                "prog",
                "raw",
                "patch",
            ],
            False,
            None,
            cwd="/flash",
        )

    @patch("lava_dispatcher.utils.qdl.dispatcher_ip", return_value="10.0.0.1")
    @patch.object(OptionalContainerQdlAction, "run_cmd")
    def test_docker_qdl_remote_cmd(self, mock_cmd, mock_ip):
        action = self.create_action(
            {
                "docker": {
                    "image": "qualcomm/qdl:latest",
                    "remote_options": "-H 10.192.244.5:2376",
                }
            }
        )
        action.run_qdl(["qdl", "prog", "raw", "patch"], "/flash")
        mock_cmd.assert_called_with(
            [
                "docker",
                "-H",
                "10.192.244.5:2376",
                "run",
                "--privileged",
                "--volume=/dev:/dev",
                "--net=host",
                "--rm",
                "--init",
                "qualcomm/qdl:latest",
                "bash",
                "-c",
                "mkdir -p /flash && mount -t nfs -o nolock 10.0.0.1:/flash /flash "
                "&& cd /flash && qdl prog raw patch",
            ],
            False,
            None,
            cwd="/flash",
        )
