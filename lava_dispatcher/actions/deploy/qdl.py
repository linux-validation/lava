# Copyright 2026 Qualcomm Inc.
#
# Author: Milosz Wasilewski <milosz.wasilewski@oss.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lava_common.exceptions import JobError
from lava_dispatcher.action import Action, Pipeline
from lava_dispatcher.actions.deploy.apply_overlay import (
    AppendOverlays,
    ApplyQDLOverlay,
)
from lava_dispatcher.actions.deploy.download import DownloaderAction
from lava_dispatcher.actions.deploy.overlay import CreateOverlay, OverlayAction
from lava_dispatcher.utils.compression import decompress_file, untar_file

if TYPE_CHECKING:
    from lava_dispatcher.job import Job


class ExtractQcomflashAction(Action):
    """
    Extract the qcomflash tarball into the qdl directory, from where the boot
    action flashes it. This is the only place the tarball is extracted.

    When "overlays" are in use, the extracted rootfs image is also registered
    under rootfs_key: AppendOverlays locates the image it modifies through the
    namespace data left by a download action, and this one is extracted from
    the tarball rather than downloaded.
    """

    name = "qdl-extract"
    description = "extract the qcomflash tarball"
    summary = "extract qcomflash tarball"

    def __init__(
        self,
        job: Job,
        rootfs_key: str | None = None,
        rootfs_image: str | None = None,
    ):
        super().__init__(job)
        self.rootfs_key = rootfs_key
        self.rootfs_image = rootfs_image

    def validate(self):
        super().validate()
        if self.rootfs_key and not self.rootfs_image:
            self.errors_add("rootfs_image is empty or missing")

    def run(self, connection, max_end_time):
        connection = super().run(connection, max_end_time)

        qcomflash = self.get_namespace_data(
            action="download-action", label="qcomflash", key="file"
        )
        if qcomflash is None:
            raise JobError("QCOMflash file missing")

        qdl_dir = Path(
            self.get_namespace_data(
                action="qdl-deploy", label="qdl-directory", key="directory"
            )
        )

        # The download action decompresses the tarball itself when the job
        # declares the compression, otherwise it is still gzipped.
        if self.get_namespace_data(
            action="download-action", label="qcomflash", key="decompressed"
        ):
            out_path = qcomflash
        else:
            self.logger.info("Decompressing %s", qcomflash)
            out_path = decompress_file(qcomflash, "gz")

        self.logger.info("Extracting %s to %s", out_path, qdl_dir)
        untar_file(out_path, qdl_dir)

        if self.rootfs_key:
            rootfs = qdl_dir / self.rootfs_image
            if not rootfs.is_file():
                self.logger.error(
                    f"rootfs_image file '{self.rootfs_image}' doesn't exist"
                )
                raise JobError("rootfs_file missing from tarball")
            # Leaving "compression" and "decompressed" unset is correct: the
            # image comes out of the tarball ready to use.
            self.set_namespace_data(
                action="download-action",
                label=self.rootfs_key,
                key="file",
                value=str(rootfs),
            )

        return connection


class QDLAction(Action):
    name = "qdl-deploy"
    description = "deploy qcomflash tarball using qdl"
    summary = "qdl deployment"

    def __init__(self, job: Job):
        super().__init__(job)
        self.param_key = "qcomflash"
        # - deploy:
        #     rootfs_image: rootfs.img
        #     qcomflash:
        #       url: ...
        #       format: ext4
        #       overlays:
        #         lava: true
        #         modules:
        #           url: ...
        #           format: tar
        #           path: /
        #     to: qdl

    def validate(self):
        super().validate()
        image_params = self.parameters.get(self.param_key)
        if not image_params:
            self.errors_add(
                f"action {self.name} can't work without {self.param_key} file"
            )
            return
        if image_params.get("overlays") and image_params.get("apply-overlay"):
            self.errors_add(
                "'apply-overlay' and 'overlays' cannot be used together, "
                "use 'overlays: {lava: true}' rather than 'apply-overlay: true'"
            )

    def populate(self, parameters):
        self.parameters = parameters
        self.pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        if self.test_needs_overlay(parameters):
            self.pipeline.add_action(OverlayAction(self.job))

        namespace = parameters["namespace"]
        download_dir = Path(self.job.tmp_dir) / "qdl" / namespace
        self.set_namespace_data(
            action="qdl-deploy",
            label="qdl-directory",
            key="directory",
            value=download_dir,
        )
        image_params = parameters.get(self.param_key) or {}
        overlays = image_params.get("overlays") or {}
        rootfs_image = parameters.get("rootfs_image", "rootfs.img")
        # AppendOverlays derives the label of each overlay download from this
        # key, and finds the image to modify under it too.
        rootfs_key = f"{self.param_key}.rootfs"

        # "overlays" is kept away from the downloader: it would otherwise
        # append them to the qcomflash tarball itself, next to the rawprogram
        # XML, where they would never reach the device.
        self.pipeline.add_action(
            DownloaderAction(
                self.job,
                self.param_key,
                download_dir,
                params={k: v for k, v in image_params.items() if k != "overlays"},
                uniquify=parameters.get("uniquify", False),
            )
        )

        if overlays:
            for overlay, overlay_params in overlays.items():
                if overlay == "lava":
                    # Special case, built from the job rather than downloaded
                    continue
                self.pipeline.add_action(
                    DownloaderAction(
                        self.job,
                        f"{rootfs_key}.{overlay}",
                        download_dir,
                        params=overlay_params,
                    )
                )
            if "lava" in overlays and not self.test_needs_overlay(parameters):
                self.pipeline.add_action(CreateOverlay(self.job))
            self.pipeline.add_action(
                ExtractQcomflashAction(self.job, rootfs_key, rootfs_image)
            )
            self.pipeline.add_action(
                AppendOverlays(self.job, rootfs_key, params=image_params)
            )
        else:
            self.pipeline.add_action(ExtractQcomflashAction(self.job))
            if image_params.get("apply-overlay", False) and self.test_needs_overlay(
                parameters
            ):
                self.pipeline.add_action(
                    ApplyQDLOverlay(
                        self.job,
                        rootfs_image=rootfs_image,
                    )
                )
