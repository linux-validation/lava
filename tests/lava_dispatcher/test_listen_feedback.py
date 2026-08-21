# Copyright 2026 Qualcomm Inc.
#
# Author: Matt Hart <matthart@qti.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from unittest.mock import MagicMock

from lava_dispatcher.shell import ShellSession


def session(raw_connection):
    """A ShellSession wrapping the given connection, without spawning one."""
    shell = ShellSession.__new__(ShellSession)
    shell.raw_connection = raw_connection
    shell.shell_output_logger = MagicMock()
    return shell


def test_listen_feedback_on_a_closed_connection():
    # A finalised shell - a docker test container that has exited, say - keeps
    # its connection object but not its file descriptor. Reading raises
    # "I/O operation on closed file" rather than reporting the nothing that is
    # there, which fails the read-feedback at the end of the job.
    raw = MagicMock()
    raw.closed = True
    shell = session(raw)

    assert shell.listen_feedback(timeout=1, namespace="docker-test-shell") == 0
    raw.expect.assert_not_called()


def test_listen_feedback_without_a_connection():
    assert session(None).listen_feedback(timeout=1) == 0


def test_listen_feedback_reads_an_open_connection():
    raw = MagicMock()
    raw.closed = False
    raw.after = "some output"
    shell = session(raw)

    assert shell.listen_feedback(timeout=1, namespace="ns") == len("some output")
    raw.expect.assert_called_once()
