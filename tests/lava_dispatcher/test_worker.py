# Copyright (C) 2025-present Linaro Limited
#
# Author: Chase Qi <chase.qi@linaro.org>
#
# SPDX-License-Identifier: GPL-2.0-or-later

import time
from unittest.mock import MagicMock, mock_open, patch

import pytest

from lava_common.constants import DISPATCHER_DOWNLOAD_DIR
from lava_dispatcher.worker import (
    Job,
    ServerUnavailable,
    VersionMismatch,
    get_job_data,
    read_cpu_model,
    static_worker_stats,
    read_meminfo,
    worker_stats,
)


@pytest.fixture
@patch("lava_dispatcher.worker.Job.__init__", return_value=None)
def job(init):
    job = Job(row=MagicMock())
    job.job_id = 123
    job.last_update = int(time.monotonic())
    job.description = MagicMock(return_value="")
    return job


def test_finalize_timed_out_no_desc(job):
    assert job.finalize_timed_out() is True


def test_finalize_timed_out_yaml_error(job):
    job.description = MagicMock(return_value="invalid: yaml: content:")
    assert job.finalize_timed_out() is True


def test_finalize_timed_out_attr_error(job):
    job.description = MagicMock(return_value="[]")
    assert job.finalize_timed_out() is True


def test_finalize_timed_out(job):
    desc = """pipeline:
- class: FinalizeAction
  name: finalize
  timeout: 1
    """
    job.description = MagicMock(return_value=desc)
    job.last_update = int(time.monotonic()) - 2
    assert job.finalize_timed_out() is True


def test_finalize_timed_out_false(job):
    desc = """pipeline:
- class: FinalizeAction
  name: finalize
  timeout: 300
    """
    job.description = MagicMock(return_value=desc)
    assert job.finalize_timed_out() is False


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture
def mock_options():
    options = MagicMock()
    options.exit_on_version_mismatch = True
    options.url = "http://example.com"
    options.token = "worker_token"
    options.name = "worker_name"
    return options


@pytest.mark.asyncio
async def test_get_job_data(mock_session, mock_options):
    expected_data = {
        "running": [{"id": 1, "token": "token1"}],
        "cancel": [{"id": 2, "token": "token2"}],
        "start": [{"id": 3, "token": "token3"}],
    }

    with patch("lava_dispatcher.worker.ping") as mock_ping:
        mock_ping.return_value = expected_data

        data = await get_job_data(mock_session, mock_options)

        mock_ping.assert_called_once_with(
            mock_session, "http://example.com", "worker_token", "worker_name"
        )

        assert data == expected_data


@pytest.mark.asyncio
async def test_get_job_data_server_unavailable(mock_session, mock_options):
    with patch("lava_dispatcher.worker.ping") as mock_ping:
        mock_ping.side_effect = ServerUnavailable("Server unavailable")

        data = await get_job_data(mock_session, mock_options)

        assert data == {}


@pytest.mark.asyncio
async def test_get_job_data_version_mismatch_exit(mock_session, mock_options):
    mock_options.exit_on_version_mismatch = True
    with patch("lava_dispatcher.worker.ping") as mock_ping:
        mock_ping.side_effect = VersionMismatch("Version mismatch")

        with pytest.raises(VersionMismatch):
            await get_job_data(mock_session, mock_options)


@pytest.mark.asyncio
async def test_get_job_data_version_mismatch_no_exit(mock_session, mock_options):
    mock_options.exit_on_version_mismatch = False
    with patch("lava_dispatcher.worker.ping") as mock_ping:
        mock_ping.side_effect = VersionMismatch("Version mismatch")

        data = await get_job_data(mock_session, mock_options)

        assert data == {}


CPUINFO_X86 = """\
processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Intel(R) Core(TM) i7-10710U CPU @ 1.10GHz
cpu MHz\t\t: 1608.001

processor\t: 1
model name\t: Intel(R) Core(TM) i7-10710U CPU @ 1.10GHz
"""

# arm64 has no per-CPU name, only a board level one at the end
CPUINFO_ARM64 = """\
processor\t: 0
BogoMIPS\t: 108.00
CPU implementer\t: 0x41
CPU part\t: 0xd0b

Hardware\t: Qualcomm Technologies, Inc QCS6490
Model\t\t: Qualcomm Technologies, Inc. Robotics RB3gen2
"""

MEMINFO = """\
MemTotal:       16311248 kB
MemFree:          412300 kB
MemAvailable:   11002448 kB
Buffers:          182364 kB
"""


def read_proc(contents):
    """Patch open() so reading a /proc file returns `contents`."""
    return patch("builtins.open", mock_open(read_data=contents))


def test_read_cpu_model_x86():
    with read_proc(CPUINFO_X86):
        assert read_cpu_model() == "Intel(R) Core(TM) i7-10710U CPU @ 1.10GHz"


def test_read_cpu_model_arm64():
    # no "model name" line, so the board level "Hardware" is used
    with read_proc(CPUINFO_ARM64):
        assert read_cpu_model() == "Qualcomm Technologies, Inc QCS6490"


def test_read_cpu_model_unreadable():
    with patch("builtins.open", side_effect=OSError):
        assert read_cpu_model() is None


def test_read_cpu_model_without_a_usable_key():
    with read_proc("processor\t: 0\nBogoMIPS\t: 108.00\n"):
        assert read_cpu_model() is None


def test_read_meminfo():
    with read_proc(MEMINFO):
        # reported in kB, stored in bytes
        assert read_meminfo() == (16311248 * 1024, 11002448 * 1024)


def test_read_meminfo_unreadable():
    with patch("builtins.open", side_effect=OSError):
        assert read_meminfo() is None


def test_read_meminfo_without_memtotal():
    with read_proc("MemFree:          412300 kB\n"):
        assert read_meminfo() is None


def test_worker_stats_reports_everything():
    static_worker_stats.cache_clear()
    with (
        patch("lava_dispatcher.worker.read_cpu_model", return_value="Some CPU"),
        patch("lava_dispatcher.worker.read_meminfo", return_value=(2048, 1024)),
        patch("lava_dispatcher.worker.os.sched_getaffinity", return_value=range(8)),
        patch("lava_dispatcher.worker.os.getloadavg", return_value=(1.5, 2.25, 0.125)),
        patch(
            "lava_dispatcher.worker.disk_usage",
            return_value=MagicMock(total=500, free=200),
        ),
        read_proc("1234.56 9876.54\n"),
    ):
        stats = worker_stats()

    assert stats["cpu_model"] == "Some CPU"
    assert stats["nproc"] == "8"
    assert stats["load_1"] == "1.50"
    assert stats["load_5"] == "2.25"
    assert stats["load_15"] == "0.12"
    assert stats["mem_total"] == "2048"
    assert stats["mem_available"] == "1024"
    assert stats["tmp_disk_total"] == "500"
    assert stats["tmp_disk_free"] == "200"
    assert stats["uptime"] == "1234"
    # every value must survive being put in a query string
    assert all(isinstance(v, str) for v in stats.values())


def test_worker_stats_survives_everything_failing():
    # A worker that can read none of it still pings, just with less to say.
    static_worker_stats.cache_clear()
    with (
        patch("lava_dispatcher.worker.read_cpu_model", return_value=None),
        patch("lava_dispatcher.worker.read_meminfo", return_value=None),
        patch("lava_dispatcher.worker.os.sched_getaffinity", side_effect=OSError),
        patch("lava_dispatcher.worker.os.getloadavg", side_effect=OSError),
        patch("lava_dispatcher.worker.disk_usage", side_effect=OSError),
        patch("builtins.open", side_effect=OSError),
    ):
        stats = worker_stats()

    assert set(stats) == {"kernel", "arch", "cpu_model"}
    for absent in ("nproc", "load_1", "mem_total", "tmp_disk_free", "uptime"):
        assert absent not in stats


def test_static_worker_stats_are_read_once():
    # /proc/cpuinfo is tens of kilobytes and cannot change while the worker
    # runs, so it must not be re-read on every ping.
    static_worker_stats.cache_clear()
    with patch(
        "lava_dispatcher.worker.read_cpu_model", return_value="Some CPU"
    ) as read:
        for _ in range(5):
            worker_stats()
    read.assert_called_once()


def test_worker_stats_measure_the_default_download_dir():
    # Before the worker has been given a job it has not been told where the
    # server wants job files, so it reports the default.
    with patch("lava_dispatcher.worker.disk_usage") as usage:
        usage.return_value = MagicMock(total=1, free=1)
        stats = worker_stats()
    usage.assert_called_once_with(DISPATCHER_DOWNLOAD_DIR)
    assert stats["tmp_dir"] == DISPATCHER_DOWNLOAD_DIR


def test_start_job_learns_the_configured_download_dir(tmp_path):
    # The server sends dispatcher_download_dir with each job; once a job has
    # started, the disk a ping reports must be the one jobs land on.
    import lava_dispatcher.worker as worker_module

    original = worker_module.download_dir
    try:
        with (
            patch("lava_dispatcher.worker.tmp_dir", tmp_path),
            patch("lava_dispatcher.worker.subprocess.Popen"),
            # so lava-run's output goes to the existing streams rather than to
            # files this test would have to close itself
            patch("lava_dispatcher.worker.debug", True),
        ):
            worker_module.start_job(
                url="http://example.com",
                token="t",
                job_id=1,
                definition="",
                device="",
                dispatcher="dispatcher_download_dir: /srv/big-disk/lava\n",
                env_str="",
                env_dut="",
                job_log_interval=1,
            )
        assert worker_module.download_dir == "/srv/big-disk/lava"

        with patch("lava_dispatcher.worker.disk_usage") as usage:
            usage.return_value = MagicMock(total=1, free=1)
            stats = worker_stats()
        usage.assert_called_once_with("/srv/big-disk/lava")
        assert stats["tmp_dir"] == "/srv/big-disk/lava"
    finally:
        worker_module.download_dir = original
