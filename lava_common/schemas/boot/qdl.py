# Copyright 2026 Qualcomm Inc.
#
# Author: Milosz Wasilewski <milosz.wasilewski@oss.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from voluptuous import Any, Msg, Optional, Required

from lava_common.schemas import boot, docker


def schema():
    base = {
        Required("method"): Msg("qdl", "'method' should be 'qdl'"),
        Required("firehose_program"): str,
        Required("rawprogram"): str,
        Optional("patch"): str,
        Optional("path"): str,
        Optional("storage"): str,
        Optional("debug"): bool,
        Optional("docker"): docker(),
        Optional("ramdump"): bool,
        Optional("ramdump_segments"): [str],
        Optional("ramdump_compression"): Any("gz", "xz", "bz2", "zstd"),
    }
    return {**boot.schema(), **base}
