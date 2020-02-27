# Copyright (C) 2019-2020 NXP
#
# Author: Larry Shen <larry.shen@nxp.com>
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

import re
from importlib import reload

from lava_dispatcher.actions.test import reset_shell
from tests.utils import DummyLogger
from tests.lava_dispatcher.test_basic import Factory, StdoutTestCase


class ConnectionMock:  # pylint: disable=too-few-public-methods
    @staticmethod
    def expect(*args, **kw):
        pass


class ResetShellFactory(Factory):  # pylint: disable=too-few-public-methods
    """
    Not Model based, this is not a Django factory.
    Factory objects are dispatcher based classes, independent
    of any database objects.
    """

    def create_imx_job(self, filename):
        return self.create_job("imx8mq-evk-01.jinja2", filename)


class TestResetShellAction(StdoutTestCase):  # pylint: disable=too-many-public-methods
    def setUp(self):
        super().setUp()
        reload(reset_shell)
        self.factory = ResetShellFactory()
        self.job = self.factory.create_imx_job("sample_jobs/reset_shell.yaml")

    def test_normal(self):
        self.assertIsNotNone(self.job)

        description_ref = self.pipeline_reference("reset_shell.yaml", job=self.job)
        self.assertEqual(description_ref, self.job.pipeline.describe(False))
        self.assertEqual(len(self.job.pipeline.describe()), 4)

        test_loop_action = [
            action
            for action in self.job.pipeline.actions
            if action.name == "lava-test-loop"
        ][0]
        self.assertIsNotNone(test_loop_action)

        reset_test_shell_action = [
            action
            for action in test_loop_action.pipeline.actions
            if action.name == "reset-test-shell"
        ][0]
        self.assertIsNotNone(reset_test_shell_action)
        reset_test_shell_action.validate()

        self.assertIn(
            "Segmentation fault", reset_test_shell_action.patterns["inject_boot"]
        )
        self.assertIn("Kernel panic", reset_test_shell_action.patterns["inject_boot"])
        self.assertEqual(
            "LAVA_HEARTBEAT", reset_test_shell_action.patterns["heartbeat"]
        )

        reset_test_shell_action.logger = DummyLogger()
        reset_test_shell_action.definition = "lava.0_smoke-case"
        reset_shell.ResetTestShellOverlayAction.case_info.setdefault(
            reset_test_shell_action.definition, {}
        )
        reset_shell.ResetTestShellAction.if_runner_started = [True, 0]
        search = r"<LAVA_SIGNAL_STARTTC Case_001>"
        pattern = r"<LAVA_SIGNAL_(\S+) ([^>]+)>"
        ConnectionMock.match = re.search(pattern, search)
        ConnectionMock.after = search
        reset_test_shell_action.check_patterns("signal", ConnectionMock, "")

    def test_exception_handling(self):
        self.assertIsNotNone(self.job)

        test_loop_action = [
            action
            for action in self.job.pipeline.actions
            if action.name == "lava-test-loop"
        ][0]
        self.assertIsNotNone(test_loop_action)

        reset_test_shell_action = [
            action
            for action in test_loop_action.pipeline.actions
            if action.name == "reset-test-shell"
        ][0]
        self.assertIsNotNone(reset_test_shell_action)
        reset_test_shell_action.validate()

        # test recover case timeout
        reload(reset_shell)
        try:
            reset_shell.ResetTestShellAction.if_runner_started = [True, 0]
            search = r"LAVA case timer: timeout"
            pattern = r"LAVA case timer: timeout"
            ConnectionMock.match = re.search(pattern, search)
            ConnectionMock.after = search
            reset_test_shell_action.check_patterns("inject_boot", ConnectionMock, "")
        except reset_shell.InjectBoot as exc:
            self.assertEqual("case_timeout", str(exc))
        else:
            self.assertFalse("Should raise an inject boot because of case timeout.")

        # test recover kernel panic
        reload(reset_shell)
        try:
            reset_shell.ResetTestShellAction.if_runner_started = [True, 0]
            search = (
                r"[    4.946791] Kernel panic - not syncing: Attempted to kill init!"
            )
            pattern = reset_test_shell_action.patterns["inject_boot"]
            ConnectionMock.match = re.search(pattern, search)
            ConnectionMock.after = search
            reset_test_shell_action.check_patterns("inject_boot", ConnectionMock, "")
        except reset_shell.InjectBoot as exc:
            self.assertEqual("recovery", str(exc))
        else:
            self.assertFalse("Should raise an inject boot because of kernel panic.")

        # test recover start runner failure
        reload(reset_shell)
        try:
            reset_test_shell_action.logger = DummyLogger()
            reset_test_shell_action.definition = "lava.0_smoke-case"
            reset_shell.ResetTestShellOverlayAction.case_info.setdefault(
                reset_test_shell_action.definition, {}
            )
            search = r"<LAVA_SIGNAL_STARTTC Case_001>"
            pattern = r"<LAVA_SIGNAL_(\S+) ([^>]+)>"
            ConnectionMock.match = re.search(pattern, search)
            ConnectionMock.after = search
            reset_test_shell_action.check_patterns("signal", ConnectionMock, "")
        except reset_shell.InjectBoot as exc:
            self.assertEqual("recovery", str(exc))
        else:
            self.assertFalse(
                "Should raise an inject boot because of runner start failure"
            )
