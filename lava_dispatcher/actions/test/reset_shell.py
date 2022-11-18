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

# pylint: disable=too-many-branches,too-many-statements,too-many-instance-attributes
# pylint: disable=logging-not-lazy,no-else-raise,missing-function-docstring,missing-class-docstring
# pylint: disable=too-many-locals,too-many-nested-blocks,missing-module-docstring

import re
import os
import time
import types
import threading
import signal
import datetime
import pexpect

from lava_dispatcher.action import Pipeline
from lava_dispatcher.logical import LavaTest
from lava_dispatcher.action import Action
from lava_dispatcher.actions.boot import (
    AutoLoginAction,
    BootHasMixin,
    OverlayUnpack,
    BootloaderInterruptAction,
    BootloaderCommandsAction,
)
from lava_dispatcher.actions.boot.environment import ExportDeviceEnvironment
from lava_dispatcher.connections.serial import ConnectDevice
from lava_dispatcher.shell import ExpectShellSession
from lava_dispatcher.logical import Boot
from lava_dispatcher.actions.test.shell import TestShellAction
from lava_common.exceptions import LAVABug, TestError, LAVAError


def empty_buffer(test_connection):
    if test_connection:
        index = 0
        cnt = 0
        while index == 0 and cnt < 60:
            cnt += 1
            index = test_connection.expect(
                [".+", pexpect.EOF, pexpect.TIMEOUT], timeout=1
            )


class ResetTestShell(LavaTest):
    """
    LavaResetTestShell Strategy object
    """

    priority = 3

    @classmethod
    def action(cls, parameters):
        return TestShellLoop()

    @classmethod
    def accepts(cls, device, parameters):  # pylint: disable=unused-argument
        if "reset_shell_para" not in parameters:
            return False, '"reset_shell_para" not in parameters'

        if "definitions" not in parameters:
            return False, '"definitions" not in parameters'

        for per_definition in parameters["definitions"]:
            if "inline" != per_definition["from"]:
                return False, "just inline definition supported"

        return True, "accepted"

    @classmethod
    def needs_deployment_data(cls, parameters):
        """ Some, not all, deployments will want deployment_data """
        return True

    @classmethod
    def needs_overlay(cls, parameters):
        return True

    @classmethod
    def has_shell(cls, parameters):
        return True


class ShellLoopAction(Action):
    """
    ShellLoopAction support shell recover after device reboot.
    """

    def __init__(self):
        super().__init__()
        self.sleep = 1

    def validate(self):
        self.logger.info("Reset shell introduced.")
        super().validate()
        if not self.pipeline:
            raise LAVABug(
                "Loop action %s needs to implement an internal pipeline" % self.name
            )

    def run(self, connection, max_end_time):
        self.call_protocols()
        while True:
            try:
                connection = self.pipeline.run_actions(connection, max_end_time)
                return connection
            except Exception as exc:  # pylint: disable=broad-except
                # ignore case timeout handler
                signal_handler = signal.getsignal(signal.SIGUSR2)
                signal.signal(signal.SIGUSR2, signal.SIG_IGN)

                self.logger.warning("Loop shell receive exception.")
                max_end_time += time.time() - self.timeout.start
                self.timeout.start = time.time()

                if not hasattr(exc, "error_type") or (
                    exc.error_type != "InjectBoot"
                    and str(exc)
                    not in (
                        "wait for prompt timed out",
                        "Connection closed",
                        "lava_test_shell connection dropped.",
                    )
                ):  # pylint: disable=no-member
                    raise
                else:
                    if str(exc) in (
                        "wait for prompt timed out",
                        "Connection closed",
                        "lava_test_shell connection dropped.",
                    ):
                        ResetTestShellOverlayAction.cur_case.clear()
                        ResetTestShellAction.unexpected_exception_counter += 1
                        if (
                            ResetTestShellAction.unexpected_exception_counter
                            > ResetTestShellOverlayAction.unexpected_exception_retry
                        ):
                            self.logger.error(
                                "Unexpected exception retry exceeds max limit, disable reboot!"
                            )
                            raise

                    if str(exc) == "disable":
                        self.logger.error(
                            "Runner retry exceeds max limit, disable reboot!"
                        )
                        raise

                    self.logger.warning("Reboot reason: " + str(exc))
                    if str(exc) != "case_reboot":
                        if "case_timer" in ResetTestShellAction.__dict__:
                            ResetTestShellAction.case_timer.cancel()
                        self.logger.info(
                            "Use predefined boot to make environment clean."
                        )
                        with connection.test_connection() as test_connection:
                            empty_buffer(test_connection)

                self.cleanup(connection)
                time.sleep(self.sleep)
                # resume case timeout handler
                signal.signal(signal.SIGUSR2, signal_handler)

        return connection


class TestShellLoop(ShellLoopAction):

    name = "lava-test-loop"
    description = "loop wrapper for lava-test-shell"
    summary = "Loop support for Lava Test Shell"

    def populate(self, parameters):
        TestShellLoop.parse_para(parameters)
        self.pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        self.pipeline.add_action(ResetTestShellBootAction())
        self.pipeline.add_action(ResetTestShellOverlayAction())
        self.pipeline.add_action(ResetTestShellAction())

    @staticmethod
    def parse_para(parameters):
        if "heartbeat" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.heartbeat_interval = parameters[
                "reset_shell_para"
            ]["heartbeat"]["interval"]
            ResetTestShellOverlayAction.heartbeat_retry = parameters[
                "reset_shell_para"
            ]["heartbeat"]["retry"]

        if "runner_recover_retry" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.runner_recover_retry = parameters[
                "reset_shell_para"
            ]["runner_recover_retry"]

        if "case_start_msg_max_gap" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.case_start_msg_max_gap = parameters[
                "reset_shell_para"
            ]["case_start_msg_max_gap"]

        if "case_default_timeout" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.case_default_timeout = parameters[
                "reset_shell_para"
            ]["case_default_timeout"]

        if "case_reboot_indications" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.case_reboot_indications = parameters[
                "reset_shell_para"
            ]["case_reboot_indications"]

        if "exception_patterns" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.exception_patterns = parameters[
                "reset_shell_para"
            ]["exception_patterns"]

        if "max_log_lines_per_minute" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.max_log_lines_per_minute = parameters[
                "reset_shell_para"
            ]["max_log_lines_per_minute"]

        if "log_poll_interval" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.log_poll_interval = parameters[
                "reset_shell_para"
            ]["log_poll_interval"]

        if "overlay_pre_cmd" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.pre_cmd_for_non_reset_flag = parameters[
                "reset_shell_para"
            ]["overlay_pre_cmd"]["pre_cmd_for_non_reset_flag"]
            ResetTestShellOverlayAction.pre_cmd_for_reset_flag = parameters[
                "reset_shell_para"
            ]["overlay_pre_cmd"]["pre_cmd_for_reset_flag"]

        if "unexpected_exception_retry" in parameters["reset_shell_para"]:
            ResetTestShellOverlayAction.unexpected_exception_retry = parameters[
                "reset_shell_para"
            ]["unexpected_exception_retry"]


class InjectBoot(LAVAError):
    """ Indicate to inject boot """

    error_help = "InjectBoot: A test need to inject boot."
    error_type = "InjectBoot"

    boot_reasons = {
        "case_reboot": "tiny_boot",
        "case_timeout": "predefined_boot",
        "unexpected_reboot": "predefined_boot",
        "recovery": "predefined_boot",
        "disable": "disable",
    }
    boot_option = "predefined_boot"

    def __init__(self, reason):  # pylint: disable=super-init-not-called
        InjectBoot.boot_option = InjectBoot.boot_reasons[reason]


class TinyBoot(Action, BootHasMixin):

    name = "tiny-boot"
    description = "connect and auto login device"
    summary = "connect and auto login device"

    def populate(self, parameters):
        self.pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        self.pipeline.add_action(ConnectDevice())
        self.pipeline.add_action(BootloaderInterruptAction())
        self.pipeline.add_action(BootloaderCommandsAction())

        if self.has_prompts(parameters):
            self.pipeline.add_action(AutoLoginAction())
            if self.test_has_shell(parameters):
                self.pipeline.add_action(ExpectShellSession())
                if "transfer_overlay" in parameters:
                    self.pipeline.add_action(OverlayUnpack())
                self.pipeline.add_action(ExportDeviceEnvironment())

    def run(self, connection, max_end_time):
        connection = self.get_namespace_data(
            action="shared", label="shared", key="connection", deepcopy=False
        )

        try:
            connection = super().run(connection, max_end_time)
        except Exception as exc:
            if len(ResetTestShellOverlayAction.cur_case) == 1:
                cur_case_suite = list(ResetTestShellOverlayAction.cur_case.keys())[0]
                ResetTestShellOverlayAction.cur_case[cur_case_suite][1] = "dead"

            if hasattr(exc, "error_type"):
                if exc.error_type in (
                    "InjectBoot",
                    "Canceled",
                ):  # pylint: disable=no-member
                    raise

            raise InjectBoot("recovery")

        self.set_namespace_data(
            action="shared", label="shared", key="connection", value=connection
        )
        return connection


class ResetTestShellBootAction(Action):

    name = "reset-test-shell-boot"
    description = "Executing boot when reset"
    summary = "Boot for Reset Lava Test Shell"

    def __init__(self):
        super().__init__()
        self.need_boot = False
        self.tiny_boot_action_run = None
        self.boot_action_run = None

    def populate(self, parameters):
        self.pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)

        boot_action = None
        boot_action_para = None
        for per_action_data in self.job.parameters["actions"]:
            if "boot" in per_action_data:
                candidate_action_para = per_action_data["boot"]
                if (
                    "namespace" in candidate_action_para
                    and candidate_action_para["namespace"] == parameters["namespace"]
                ):
                    boot_action_para = candidate_action_para
                    boot_action = Boot.select(
                        self.job.device, boot_action_para
                    ).action()

        self.pipeline.add_action(boot_action, boot_action_para)
        boot_action.section = "boot"

        boot_action_para["failure_retry"] = 1
        tinyboot = TinyBoot()
        self.pipeline.add_action(tinyboot, boot_action_para)
        tinyboot.section = "boot"

    def run(self, connection, max_end_time):
        if not self.need_boot:
            for per_action in self.pipeline.actions:
                if per_action.parameters["namespace"] == self.parameters["namespace"]:
                    if per_action.section == "boot":
                        if per_action.__class__.__name__ == "TinyBoot":
                            self.tiny_boot_action_run = per_action.run
                        else:
                            self.boot_action_run = per_action.run
                        per_action.run = types.MethodType(
                            lambda self, *_: self.__errors__.clear(), per_action
                        )

            self.need_boot = True
        else:
            for per_action in self.pipeline.actions:
                if per_action.parameters["namespace"] == self.parameters["namespace"]:
                    if per_action.section == "boot":
                        per_action.run = types.MethodType(
                            lambda self, *_: self.__errors__.clear(), per_action
                        )
                        if per_action.__class__.__name__ == "TinyBoot":
                            if InjectBoot.boot_option == "tiny_boot":
                                per_action.run = self.tiny_boot_action_run
                        else:
                            if InjectBoot.boot_option == "predefined_boot":
                                per_action.run = self.boot_action_run

        connection = super().run(connection, max_end_time)

        return connection


class ResetTestShellOverlayAction(Action):

    name = "reset-test-shell-overlay"
    description = "Overlay for boot when reset"
    summary = "Overlay for Boot for Reset Lava Test Shell"

    cur_run_done = True
    done_cases = {}
    cur_case = {}
    case_info = {}
    forced_cases = {}
    last_heartbeat = 0

    # default value for reset shell parameters
    heartbeat_interval = 0
    heartbeat_retry = 0
    runner_recover_retry = 2
    case_start_msg_max_gap = 60
    case_default_timeout = -1
    case_reboot_indications = []
    exception_patterns = []
    max_log_lines_per_minute = 2000
    log_poll_interval = 60
    pre_cmd_for_non_reset_flag = []
    pre_cmd_for_reset_flag = []
    unexpected_exception_retry = 2

    def run(self, connection, max_end_time):
        running = self.parameters["stage"]
        lava_test_results_dir = self.get_namespace_data(
            action="test", label="results", key="lava_test_results_dir"
        )

        # prepare env for device
        connection.sendline(
            r"echo 8 > /proc/sys/kernel/printk;"
            "echo 1 > /proc/sys/kernel/panic_on_oops;"
            "echo 60 > /proc/sys/kernel/panic;",
            delay=max(self.character_delay, 5),
        )
        connection.wait()

        # recover runner conf
        if ResetTestShellOverlayAction.cur_run_done:
            ResetTestShellOverlayAction.cur_run_done = False
            connection.sendline(
                "rm -f %s/%s/lava-test-runner.conf-*"
                % (lava_test_results_dir, running),
                delay=max(self.character_delay, 5),
            )
            connection.wait()
        else:
            connection.sendline(
                "mv %s/%s/lava-test-runner.conf-* %s/%s/lava-test-runner.conf"
                % (lava_test_results_dir, running, lava_test_results_dir, running),
                delay=max(self.character_delay, 5),
            )
            connection.wait()

        # export lava binary path
        connection.sendline(
            "export PATH=%s/bin:${PATH}" % (lava_test_results_dir),
            delay=max(self.character_delay, 5),
        )
        connection.wait()

        if len(ResetTestShellOverlayAction.cur_case) == 0:
            for per_flag in ResetTestShellOverlayAction.pre_cmd_for_non_reset_flag:
                connection.sendline(per_flag, delay=max(self.character_delay, 5))
                connection.wait()
        else:
            cur_case_suite = list(ResetTestShellOverlayAction.cur_case.keys())[0]
            cur_case_name = ResetTestShellOverlayAction.cur_case[cur_case_suite][0]
            cur_case_status = ResetTestShellOverlayAction.cur_case[cur_case_suite][1]
            reset_flag = ResetTestShellOverlayAction.case_info[cur_case_suite][
                cur_case_name
            ]["resetflag"]

            if reset_flag:
                chk_ret_code = 2

                if cur_case_status == "alive":
                    for per_flag in ResetTestShellOverlayAction.pre_cmd_for_reset_flag:
                        connection.sendline(
                            per_flag, delay=max(self.character_delay, 5)
                        )
                        connection.wait()

                    connection.sendline(reset_flag, delay=max(self.character_delay, 5))
                    connection.wait()

                    chk_cmd = 'echo "LAVA_CASE_RESET_FLAG=$?"'
                    connection.sendline(chk_cmd, delay=max(self.character_delay, 5))
                    connection.wait()
                    chk_ret_content = connection.raw_connection.before
                    for per_content in chk_ret_content.split("\n"):
                        rc_match = re.match(r".*FLAG=(\d+)", per_content)
                        if rc_match:
                            chk_ret_code = int(rc_match.group(1))
                            break

                if chk_ret_code == 0:
                    self.logger.info(
                        "Continue run after case reboot: %s" % cur_case_name
                    )
                else:
                    run_script = "%s/%s/tests/%s/run.sh" % (
                        lava_test_results_dir,
                        running,
                        cur_case_suite,
                    )
                    # comment the last already runned case in run.sh after device reboot to skip it
                    connection.sendline(
                        r"sed -i '/ \"\{0,\}%s\"\{0,\} /s/^/#/g' %s"
                        % (
                            ResetTestShellOverlayAction.cur_case[cur_case_suite][0],
                            run_script,
                        ),
                        delay=max(self.character_delay, 5),
                    )
                    connection.wait()

                    if chk_ret_code == 1:
                        self.logger.info(
                            "Set current case as pass after check reset flag: %s"
                            % cur_case_name
                        )
                        ResetTestShellOverlayAction.forced_cases[
                            ResetTestShellOverlayAction.cur_case[cur_case_suite][0]
                        ] = "pass"
                    else:
                        if cur_case_status != "alive":
                            self.logger.info(
                                "Set current case as fail as soft reboot failure: %s"
                                % (cur_case_name)
                            )
                        else:
                            self.logger.info(
                                "Set current case as fail after chk reset flag: %s"
                                % (cur_case_name)
                            )

                        ResetTestShellOverlayAction.forced_cases[
                            ResetTestShellOverlayAction.cur_case[cur_case_suite][0]
                        ] = "fail"

        for (
            per_suite,
            suite_done_cases,
        ) in ResetTestShellOverlayAction.done_cases.items():
            run_script = "%s/%s/tests/%s/run.sh" % (
                lava_test_results_dir,
                running,
                per_suite,
            )

            done_case_start = suite_done_cases[0]
            done_case_end = suite_done_cases[1]

            # comment all already runned case in run.sh after device reboot to skip these cases
            if done_case_start != done_case_end:
                connection.sendline(
                    r"sed -i '/ \"\{0,\}%s\"\{0,\} /,/ \"\{0,\}%s\"\{0,\} /s/^/#/g' %s"
                    % (done_case_start, done_case_end, run_script),
                    delay=max(self.character_delay, 5),
                )
            else:
                connection.sendline(
                    r"sed -i '/ \"\{0,\}%s\"\{0,\} /s/^/#/g' %s"
                    % (done_case_start, run_script),
                    delay=max(self.character_delay, 5),
                )
            connection.wait()

        # device heartbeat run script
        if ResetTestShellOverlayAction.heartbeat_interval > 0:
            heartbeat_script = "/bin/heartbeat"
            connection.sendline(
                r'echo "while : ; do sleep %s; echo %s; done" > %s; sync; chmod +x %s; '
                "kill $(jobs -p) > /dev/null 2>&1; heartbeat &"
                % (
                    ResetTestShellOverlayAction.heartbeat_interval,
                    "LAVA_HEARTBEAT",
                    heartbeat_script,
                    heartbeat_script,
                ),
                delay=max(self.character_delay, 5),
            )
            connection.wait()

        # reset parameters
        ResetTestShellOverlayAction.last_heartbeat = 0
        ResetTestShellAction.if_runner_started[0] = False
        ResetTestShellAction.if_case_started[0] = False
        ResetTestShellAction.if_case_started[2] = 0


class ResetTestShellAction(TestShellAction):

    name = "reset-test-shell"
    description = "Executing lava-test-runner"
    summary = "Reset Lava Test Shell"
    timeout_exception = TestError

    if_runner_started = [False, 0]
    if_case_started = [False, 0, 0]
    runner_exit_counter = 0
    last_line_numbers = 0
    unexpected_exception_counter = 0

    def __init__(self):
        super().__init__()
        inject_boot_string = "|".join(
            [r"Hit any key to stop autoboot"]
            + [r"/lava-.*: line [0-9]+: signal: command not found"]
            + ResetTestShellOverlayAction.exception_patterns
            + ResetTestShellOverlayAction.case_reboot_indications
        )

        self.reset_shell_dict = {
            "inject_boot": inject_boot_string,
            "heartbeat": r"LAVA_HEARTBEAT",
        }

    def validate(self):
        super().validate()
        self.patterns.update(self.reset_shell_dict)

    def run(self, connection, max_end_time):  # pylint: disable=too-many-locals
        self.connection_timeout.duration = ResetTestShellOverlayAction.log_poll_interval
        signal.signal(signal.SIGUSR2, self.case_timeout_signal_handler)
        super().run(connection, max_end_time)

    def _reset_patterns(self):
        super()._reset_patterns()
        self.patterns.update(self.reset_shell_dict)

    def case_timeout_signal_handler(
        self, signum, stack
    ):  # pylint: disable=unused-argument
        search = r"LAVA case timer: timeout"
        pattern = r"LAVA case timer: timeout"
        ResetTestShellAction.ConnectionMock.match = re.search(pattern, search)
        ResetTestShellAction.ConnectionMock.after = search
        self.check_patterns("inject_boot", ResetTestShellAction.ConnectionMock, "")

    def case_timeout_handler(self, name, test_connection):
        self.logger.warning("Case %s timeout." % name)
        os.kill(os.getpid(), signal.SIGUSR2)

    class ConnectionMock:  # pylint: disable=too-few-public-methods
        @staticmethod
        def expect(*args, **kw):
            pass

    def force_assign_result(self, check_char, case, result):
        """
        Force set case result.
        """
        self.logger.info("Force set result for testcase: %s" % case)
        event = "signal"
        search = "<LAVA_SIGNAL_ENDTC %s>" % (case)
        self.logger.target(search)
        pattern = r"<LAVA_SIGNAL_(\S+) ([^>]+)>"
        ResetTestShellAction.ConnectionMock.match = re.search(pattern, search)
        ResetTestShellAction.ConnectionMock.after = search
        self.check_patterns(event, ResetTestShellAction.ConnectionMock, check_char)
        search = "<LAVA_SIGNAL_TESTCASE TEST_CASE_ID=%s RESULT=%s>" % (case, result)
        self.logger.target(search)
        ResetTestShellAction.ConnectionMock.match = re.search(pattern, search)
        ResetTestShellAction.ConnectionMock.after = search
        self.check_patterns(event, ResetTestShellAction.ConnectionMock, check_char)

    def check_patterns(self, event, test_connection, check_char):
        """
        Calls the parent check_patterns first, then checks for subclass pattern.
        """
        ret = super().check_patterns(event, test_connection, check_char)

        if not ResetTestShellAction.if_runner_started[0]:
            if event not in ("signal", "heartbeat"):
                self.logger.warning("Failed to get runner start msg.")
                cnt = ResetTestShellAction.if_runner_started[1] + 1
                ResetTestShellAction.if_runner_started = [False, cnt]
                if (
                    ResetTestShellAction.if_runner_started[1]
                    > ResetTestShellOverlayAction.runner_recover_retry
                ):
                    raise InjectBoot("disable")
                else:
                    raise InjectBoot("recovery")
        else:
            if not ResetTestShellAction.if_case_started[0]:
                if ResetTestShellAction.if_case_started[2] == 0:
                    ResetTestShellAction.if_case_started[2] = time.time()
                diff = time.time() - ResetTestShellAction.if_case_started[2]
                if diff > ResetTestShellOverlayAction.case_start_msg_max_gap:
                    self.logger.warning(
                        "No new case start msg received, recover environment."
                    )
                    cnt = ResetTestShellAction.if_case_started[1] + 1
                    ResetTestShellAction.if_case_started = [False, cnt, 0]
                    if (
                        ResetTestShellAction.if_case_started[1]
                        > ResetTestShellOverlayAction.runner_recover_retry
                    ):
                        raise InjectBoot("disable")
                    else:
                        raise InjectBoot("recovery")

        if event == "timeout":
            if ResetTestShellOverlayAction.max_log_lines_per_minute > 0:
                current_line_numbers = test_connection.before.count("\n")
                if current_line_numbers < ResetTestShellAction.last_line_numbers:
                    ResetTestShellAction.last_line_numbers = 0

                if current_line_numbers - ResetTestShellAction.last_line_numbers > int(
                    ResetTestShellOverlayAction.max_log_lines_per_minute
                    * ResetTestShellOverlayAction.log_poll_interval
                    / 60
                ):
                    self.logger.warning("Log increases too quick, restart the device.")
                    if len(ResetTestShellOverlayAction.cur_case) > 0:
                        self.force_assign_result(
                            check_char,
                            ResetTestShellOverlayAction.cur_case[self.definition][0],
                            "fail",
                        )
                    raise InjectBoot("recovery")
                ResetTestShellAction.last_line_numbers = current_line_numbers

            if ResetTestShellOverlayAction.last_heartbeat == 0:
                ResetTestShellOverlayAction.last_heartbeat = time.time()
            diff = time.time() - ResetTestShellOverlayAction.last_heartbeat
            if diff > ResetTestShellOverlayAction.heartbeat_interval * (
                1 + ResetTestShellOverlayAction.heartbeat_retry
            ):
                self.logger.warning("Heartbeat failure, restart the device.")
                if len(ResetTestShellOverlayAction.cur_case) > 0:
                    self.force_assign_result(
                        check_char,
                        ResetTestShellOverlayAction.cur_case[self.definition][0],
                        "fail",
                    )
                raise InjectBoot("recovery")

        elif event == "inject_boot":
            event_match_expression = test_connection.after
            self.logger.warning("Start to inject boot.")

            if (
                event_match_expression
                in ResetTestShellOverlayAction.case_reboot_indications
            ):
                if len(ResetTestShellOverlayAction.cur_case) > 0:
                    cur_case_name = ResetTestShellOverlayAction.cur_case[
                        self.definition
                    ][0]

                    if (
                        cur_case_name
                        in ResetTestShellOverlayAction.case_info[self.definition]
                        and ResetTestShellOverlayAction.case_info[self.definition][
                            cur_case_name
                        ]["resetflag"]
                    ):
                        raise InjectBoot("case_reboot")
                    else:
                        self.logger.warning(
                            "Ignore the case reboot indications as this is not a reboot case."
                        )
                        ret = True
                else:
                    self.logger.warning("Not reboot during case running.")
                    ret = True
            else:
                if event_match_expression == "LAVA case timer: timeout":
                    inject_boot_reason = "case_timeout"
                elif event_match_expression == "Hit any key to stop autoboot":
                    inject_boot_reason = "unexpected_reboot"
                else:
                    inject_boot_reason = "recovery"
                    self.logger.info("Exception matched: %s" % event_match_expression)

                if len(ResetTestShellOverlayAction.cur_case) > 0:
                    self.force_assign_result(
                        check_char,
                        ResetTestShellOverlayAction.cur_case[self.definition][0],
                        "fail",
                    )
                    ResetTestShellOverlayAction.cur_case.clear()

                empty_buffer(test_connection)
                raise InjectBoot(inject_boot_reason)

        elif event == "signal":
            name, params = test_connection.match.groups()

            if not ResetTestShellAction.if_runner_started[0]:
                if name != "STARTRUN":
                    self.logger.info("Failed to get runner start msg.")
                    cnt = ResetTestShellAction.if_runner_started[1] + 1
                    ResetTestShellAction.if_runner_started = [False, cnt]
                    if (
                        ResetTestShellAction.if_runner_started[1]
                        > ResetTestShellOverlayAction.runner_recover_retry
                    ):
                        raise InjectBoot("disable")
                    else:
                        raise InjectBoot("recovery")

            params = params.split()
            if name == "STARTRUN":
                ResetTestShellAction.if_runner_started = [True, 0]
                ResetTestShellAction.unexpected_exception_counter = 0
                if len(ResetTestShellOverlayAction.forced_cases) > 0:
                    case_name = list(ResetTestShellOverlayAction.forced_cases.keys())[0]
                    case_result = list(
                        ResetTestShellOverlayAction.forced_cases.values()
                    )[0]
                    ResetTestShellOverlayAction.forced_cases.clear()
                    self.force_assign_result(check_char, case_name, case_result)

                suite = self.definition.split("_", 1)[1]

                for per_definition in self.parameters["definitions"]:
                    if per_definition["name"] == suite:
                        ResetTestShellOverlayAction.case_info.setdefault(
                            self.definition, {}
                        )
                        for per_step in per_definition["repository"]["run"]["steps"]:
                            step_parts = re.match(
                                "lava-test-case +(.*) +--shell +(.*)", per_step
                            )
                            if step_parts:
                                case_name = (
                                    step_parts.group(1)
                                    .replace('"', "")
                                    .replace("'", "")
                                )
                                case_cmd = step_parts.group(2)
                                case_cmd_parts = re.match(
                                    r".*# *(?:timeout *= *(?P<timeout>\d+))?(?: *, *)?"
                                    "(?:resetflag *= *(?P<resetflag>.*))?",
                                    case_cmd,
                                )
                                if case_cmd_parts:
                                    case_timeout = case_cmd_parts.group("timeout")
                                    case_reset_flag = case_cmd_parts.group("resetflag")
                                    if case_timeout or case_reset_flag:
                                        if case_timeout:
                                            case_timeout = case_timeout.strip()
                                        if case_reset_flag:
                                            case_reset_flag = case_reset_flag.strip()
                                            if case_reset_flag[-1] in ("'", '"'):
                                                case_reset_flag = case_reset_flag[:-1]

                                        ResetTestShellOverlayAction.case_info[
                                            self.definition
                                        ].setdefault(case_name, {})
                                        ResetTestShellOverlayAction.case_info[
                                            self.definition
                                        ][case_name]["timeout"] = case_timeout
                                        ResetTestShellOverlayAction.case_info[
                                            self.definition
                                        ][case_name]["resetflag"] = case_reset_flag
                                        if case_reset_flag:
                                            ResetTestShellOverlayAction.case_info[
                                                self.definition
                                            ][case_name]["casecmd"] = case_cmd
                        break

            elif name == "STARTTC":
                ResetTestShellAction.if_case_started = [True, 0, 0]
                ResetTestShellAction.runner_exit_counter = 0

                if (
                    len(ResetTestShellOverlayAction.cur_case) == 0
                    or params[0]
                    != ResetTestShellOverlayAction.cur_case[self.definition][0]
                ):
                    case_timeout = ResetTestShellOverlayAction.case_default_timeout
                    if (
                        params[0]
                        in ResetTestShellOverlayAction.case_info[self.definition]
                    ):
                        if ResetTestShellOverlayAction.case_info[self.definition][
                            params[0]
                        ]["timeout"]:
                            case_timeout = int(
                                ResetTestShellOverlayAction.case_info[self.definition][
                                    params[0]
                                ]["timeout"]
                            )

                    self.logger.info(
                        "%s timeout = %s s" % (params[0], str(case_timeout))
                    )

                    if case_timeout != -1:
                        ResetTestShellAction.case_timer = threading.Timer(
                            case_timeout,
                            self.case_timeout_handler,
                            [params[0], test_connection],
                        )
                        ResetTestShellAction.case_timer.start()

                    ResetTestShellOverlayAction.cur_case.clear()
                    ResetTestShellOverlayAction.cur_case = {
                        self.definition: [params[0], "alive", datetime.datetime.now()]
                    }

            elif name == "ENDTC":
                if len(ResetTestShellOverlayAction.cur_case) != 0:
                    self.logger.info(
                        "Case %s run duration = %d s."
                        % (
                            ResetTestShellOverlayAction.cur_case[self.definition][0],
                            (
                                datetime.datetime.now()
                                - ResetTestShellOverlayAction.cur_case[self.definition][
                                    2
                                ]
                            ).seconds,
                        )
                    )

        elif event == "exit":
            if self.current_run is not None:
                if len(ResetTestShellOverlayAction.cur_case) > 0:
                    self.logger.info(
                        "Runner exit, reboot and ignore current case: %s"
                        % (ResetTestShellOverlayAction.cur_case)
                    )
                    self.force_assign_result(
                        check_char,
                        ResetTestShellOverlayAction.cur_case[self.definition][0],
                        "fail",
                    )
                    ResetTestShellOverlayAction.cur_case.clear()
                else:
                    self.logger.info("Runner exit, reboot and retry.")
                    ResetTestShellAction.runner_exit_counter += 1
                    if (
                        ResetTestShellAction.runner_exit_counter
                        > ResetTestShellOverlayAction.runner_recover_retry
                    ):
                        raise InjectBoot("disable")
                raise InjectBoot("recovery")
            else:
                ResetTestShellOverlayAction.cur_run_done = True

        elif event == "heartbeat":
            ResetTestShellOverlayAction.last_heartbeat = time.time()
            ret = True

        return ret

    def signal_test_case(self, params):
        super().signal_test_case(params)

        if len(ResetTestShellOverlayAction.cur_case) == 0:
            cur_case_name = params[0].replace("TEST_CASE_ID=", "")
        else:
            cur_case_name = ResetTestShellOverlayAction.cur_case[self.definition][0]

        ResetTestShellOverlayAction.done_cases.setdefault(self.definition, [])
        if len(ResetTestShellOverlayAction.done_cases[self.definition]) == 0:
            ResetTestShellOverlayAction.done_cases[self.definition].append(
                cur_case_name
            )
            ResetTestShellOverlayAction.done_cases[self.definition].append(
                cur_case_name
            )
        else:
            ResetTestShellOverlayAction.done_cases[self.definition][1] = cur_case_name

        ResetTestShellOverlayAction.cur_case.clear()
        if "case_timer" in ResetTestShellAction.__dict__:
            ResetTestShellAction.case_timer.cancel()

        ResetTestShellAction.if_case_started = [False, 0, time.time()]
