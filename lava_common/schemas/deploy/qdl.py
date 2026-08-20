# Copyright 2026 Qualcomm Inc.
#
# Author: Milosz Wasilewski <milosz.wasilewski@oss.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from voluptuous import Optional, Required

from lava_common.schemas import deploy


def schema():
    base = {
        Required("to"): "qdl",
        # deploy.url() also accepts "format" plus an "overlays" dictionary,
        # which describe the image named by "rootfs_image" inside the tarball,
        # not the tarball itself.
        Required("qcomflash"): deploy.url({Optional("apply-overlay"): bool}),
        Optional("uniquify"): bool,
        Optional("rootfs_image"): str,
    }
    return {**deploy.schema(), **base}
