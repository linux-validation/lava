# Copyright (C) 2026 Qualcomm Inc.
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Files the job attaches to itself on the server, such as screenshots.

Each file is uploaded as soon as it is attached, so it reaches the server even
if the job dies later, and shows up while the job is still running. A file
that cannot be uploaded is kept and tried once more before the job finishes.

Every attachment gets a test result in the suite it was taken for: "pass"
with the attachment's name once it is on the server, "fail" if it never got
there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from lava_common.version import __version__

if TYPE_CHECKING:
    from lava_common.log import YAMLLogger

UPLOAD_TIMEOUT = 60
# Matches the names the server accepts: lava_scheduler_app/attachments.py.
MAX_NAME_LENGTH = 128
UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")


class AttachmentError(Exception):
    """An attachment did not reach the server."""


@dataclass
class Attachment:
    path: Path
    name: str
    definition: str
    level: str


def clean_name(text: str) -> str:
    """Make text usable in an attachment name."""
    return UNSAFE_CHARACTERS.sub("-", text).strip(".-_") or "file"


def attachment_name(prefix: str, number: int, filename: str) -> str:
    """A unique name such as "kvm_snapshot-001-screen.png"."""
    path = Path(filename)
    suffix = clean_name(path.suffix.lstrip("."))
    stem = clean_name(path.stem)
    head = f"{clean_name(prefix)}-{number:03d}-"
    tail = f".{suffix}" if path.suffix else ""
    room = MAX_NAME_LENGTH - len(head) - len(tail)
    return head + stem[: max(room, 1)] + tail


class Attachments:
    """Uploads files to the job's attachments on the LAVA server.

    Without a server, as when lava-run is used on its own, files stay on the
    worker and are only logged.
    """

    def __init__(
        self,
        job_id: int | str,
        url: str | None = None,
        token: str | None = None,
    ) -> None:
        self.endpoint = None
        if url:
            self.endpoint = f"{url}/scheduler/internal/v1/jobs/{job_id}/attachments/"
        self.token = token
        self.count = 0
        self.failed: list[Attachment] = []
        self.session = requests.Session()

    def attach(
        self,
        logger: YAMLLogger,
        path: Path,
        prefix: str,
        definition: str,
        level: str,
    ) -> str | None:
        """Attach a file to the job, and return its name if it was uploaded."""
        self.count += 1
        attachment = Attachment(
            path=Path(path),
            name=attachment_name(prefix, self.count, Path(path).name),
            definition=definition,
            level=level,
        )
        if self.endpoint is None:
            logger.info("Not attaching %s: no LAVA server", attachment.path)
            return None

        try:
            self.upload(attachment)
        except AttachmentError as exc:
            logger.warning(
                "Unable to attach %s, will try again later: %s", attachment.name, exc
            )
            self.failed.append(attachment)
            return None

        self.record(logger, attachment, uploaded=True)
        return attachment.name

    def retry_failed(self, logger: YAMLLogger) -> None:
        """Try the uploads that failed once more. Must run before the job is
        reported finished, as the server takes attachments only while the job
        runs."""
        failed, self.failed = self.failed, []
        for attachment in failed:
            try:
                self.upload(attachment)
            except AttachmentError as exc:
                logger.error("Unable to attach %s: %s", attachment.name, exc)
                self.record(logger, attachment, uploaded=False)
                continue
            self.record(logger, attachment, uploaded=True)

    def upload(self, attachment: Attachment) -> None:
        headers = {"User-Agent": f"lava {__version__}", "LAVA-Token": self.token}
        try:
            with attachment.path.open("rb") as content:
                response = self.session.post(
                    self.endpoint,
                    data={"name": attachment.name},
                    files={"file": (attachment.name, content)},
                    headers=headers,
                    timeout=UPLOAD_TIMEOUT,
                )
        except (OSError, requests.RequestException) as exc:
            raise AttachmentError(str(exc)) from exc

        if response.status_code != 200:
            try:
                reason = response.json()["error"]
            except (ValueError, KeyError, TypeError):
                reason = response.text[:200]
            raise AttachmentError(f"server answered {response.status_code}: {reason}")

    def record(
        self, logger: YAMLLogger, attachment: Attachment, uploaded: bool
    ) -> None:
        if uploaded:
            # The job log shows this line where the file was attached.
            logger.log_message(
                "attachment",
                {"name": attachment.name, "size": attachment.path.stat().st_size},
            )
        logger.results(
            {
                "definition": attachment.definition,
                "case": attachment.name,
                "level": attachment.level,
                "result": "pass" if uploaded else "fail",
                "extra": {"attachment": attachment.name},
            }
        )
