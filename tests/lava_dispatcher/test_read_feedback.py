# Copyright 2026 Qualcomm Inc.
#
# Author: Matt Hart <matthart@qti.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from unittest.mock import MagicMock, patch

from lava_dispatcher.power import ReadFeedback
from lava_dispatcher.shell import ShellSession
from tests.lava_dispatcher.test_basic import LavaDispatcherTestCase


def shared_shell():
    """
    A ShellSession as two namespaces see it when the second one is declared
    with connection-namespace: they are the same object, so finalising it for
    one namespace closes it for the other.
    """
    raw = MagicMock()
    raw.closed = False
    raw.after = ""

    def expect(*args, **kwargs):
        # what pexpect.pty_spawn.read_nonblocking() does once the fd is gone
        if raw.closed:
            raise ValueError("I/O operation on closed file.")
        return 0

    raw.expect.side_effect = expect
    shell = ShellSession.__new__(ShellSession)
    shell.raw_connection = raw
    shell.shell_input_logger = MagicMock()
    shell.shell_output_logger = MagicMock()
    shell.connected = True

    def finalise():
        shell.connected = False
        raw.closed = True

    shell.finalise = finalise
    return shell


class TestReadFeedbackSharedConnection(LavaDispatcherTestCase):
    def test_finalising_a_connection_two_namespaces_share(self):
        # A qdl health check has a second namespace declared with
        # connection-namespace, so both entries are the same connection.
        # Finalising the first closes it, and reading the second must report
        # the nothing that is left rather than raising
        # "I/O operation on closed file" and aborting the rest of finalize.
        job = self.create_simple_job()
        action = ReadFeedback(job, finalize=True, repeat=True)
        action.duration = 1
        shell = shared_shell()

        job.context.update({"qdl": {}, "flasher": {}})
        with patch.object(action, "get_namespace_data", return_value=shell):
            action.run(MagicMock(), None)

        # both namespaces finalised, rather than stopping at the first
        self.assertFalse(shell.connected)
        self.assertTrue(shell.raw_connection.closed)
