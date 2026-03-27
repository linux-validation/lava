# Copyright 2026 Qualcomm Inc.
#
# Author: Milosz Wasilewski <milosz.wasilewski@oss.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from unittest.mock import patch

from tests.lava_dispatcher.test_basic import Factory, LavaDispatcherTestCase


class TestQDLBootAction(LavaDispatcherTestCase):
    @patch("lava_dispatcher.actions.boot.qdl.which")
    def test_qdl_job(self, which_mock):
        which_mock.return_value = "/foo/qdl"
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
