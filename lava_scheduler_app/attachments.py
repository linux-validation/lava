# Copyright (C) 2026 Qualcomm Inc.
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Files a running job attaches to itself, such as screenshots of the device.

lava-run uploads each file while the job runs. They are kept with the rest of
the job's output, in an "attachments" directory, so they are removed with the
job and served only to users who can see the job.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

    from lava_scheduler_app.models import TestJob

# A plain file name: no directories, nothing hidden, nothing that needs
# escaping in a URL or a shell.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Attachments are served from the server's own site, so only types that a
# browser cannot run are shown inline. Anything else is downloaded.
INLINE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
}
DOWNLOAD_CONTENT_TYPE = "application/octet-stream"

CHUNK_SIZE = 64 * 1024


class AttachmentRefused(Exception):
    """The attachment cannot be stored; status is the HTTP status to answer."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def attachments_dir(job: TestJob) -> Path:
    return Path(job.output_dir) / "attachments"


def valid_name(name: str) -> bool:
    return bool(NAME_PATTERN.match(name))


def content_type(name: str) -> tuple[str, bool]:
    """The content type to serve the attachment as, and whether to show it
    inline rather than download it."""
    suffix = Path(name).suffix.lower()
    if suffix in INLINE_CONTENT_TYPES:
        return INLINE_CONTENT_TYPES[suffix], True
    return DOWNLOAD_CONTENT_TYPE, False


def list_attachments(job: TestJob) -> list[dict[str, int | str]]:
    """The job's attachments, in the order they were stored."""
    directory = attachments_dir(job)
    if not directory.is_dir():
        return []
    attachments = []
    for path in directory.iterdir():
        # Skip uploads still being written.
        if not path.is_file() or not valid_name(path.name):
            continue
        stat = path.stat()
        attachments.append((stat.st_mtime_ns, path.name, stat.st_size))
    attachments.sort()
    return [{"name": name, "size": size} for _, name, size in attachments]


def attachment_path(job: TestJob, name: str) -> Path | None:
    """Where an existing attachment is stored, or None."""
    if not valid_name(name):
        return None
    path = attachments_dir(job) / name
    if not path.is_file():
        return None
    return path


def save_attachment(job: TestJob, name: str, upload: UploadedFile) -> int:
    """Store an uploaded attachment and return its size."""
    if not valid_name(name):
        raise AttachmentRefused(400, f"Invalid name '{name}'")
    if upload.size > settings.ATTACHMENT_MAX_SIZE:
        raise AttachmentRefused(
            413, f"Attachment larger than {settings.ATTACHMENT_MAX_SIZE} bytes"
        )

    existing = list_attachments(job)
    if len(existing) >= settings.ATTACHMENT_MAX_COUNT:
        raise AttachmentRefused(
            413, f"Job already has {settings.ATTACHMENT_MAX_COUNT} attachments"
        )
    total = sum(int(attachment["size"]) for attachment in existing)
    if total + upload.size > settings.ATTACHMENT_MAX_TOTAL_SIZE:
        raise AttachmentRefused(
            413,
            f"Job attachments would exceed {settings.ATTACHMENT_MAX_TOTAL_SIZE} bytes",
        )

    directory = attachments_dir(job)
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    path = directory / name
    if path.exists():
        raise AttachmentRefused(409, f"Attachment '{name}' already exists")

    # Write under a name list_attachments() skips, then move it into place,
    # so a half-written file is never served.
    partial = directory / f".{name}.partial"
    try:
        with partial.open("wb") as output:
            for chunk in upload.chunks(CHUNK_SIZE):
                output.write(chunk)
        # link() fails if the name was taken meanwhile, unlike rename().
        os.link(partial, path)
    except FileExistsError:
        raise AttachmentRefused(409, f"Attachment '{name}' already exists")
    finally:
        partial.unlink(missing_ok=True)
    return path.stat().st_size
