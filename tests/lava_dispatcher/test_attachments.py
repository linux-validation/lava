# Copyright (C) 2026 Qualcomm Inc.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from unittest.mock import MagicMock

import pytest
import responses

from lava_dispatcher.attachments import Attachments, attachment_name, clean_name

URL = "http://lava.example.com"
ENDPOINT = f"{URL}/scheduler/internal/v1/jobs/42/attachments/"


@pytest.fixture
def screenshot(tmp_path):
    path = tmp_path / "screen.png"
    path.write_bytes(b"\x89PNG not really")
    return path


@pytest.fixture
def logger():
    return MagicMock()


def results(logger):
    return [call.args[0] for call in logger.results.mock_calls]


def logged_attachments(logger):
    return [
        call.args[1]
        for call in logger.log_message.mock_calls
        if call.args[0] == "attachment"
    ]


@pytest.mark.parametrize(
    "text, cleaned",
    [
        ("screen", "screen"),
        ("boot menu", "boot-menu"),
        ("../../etc/passwd", "etc-passwd"),
        ("é", "file"),
        ("", "file"),
    ],
)
def test_clean_name(text, cleaned):
    assert clean_name(text) == cleaned


def test_attachment_name():
    assert (
        attachment_name("kvm_snapshot", 1, "screen.png")
        == "kvm_snapshot-001-screen.png"
    )
    assert attachment_name("cmd", 12, "no suffix") == "cmd-012-no-suffix"
    assert attachment_name("cmd", 1, ".hidden") == "cmd-001-hidden"


def test_attachment_name_is_kept_short():
    name = attachment_name("cmd", 1, "x" * 300 + ".png")
    assert len(name) == 128
    assert name.endswith("x.png")


def test_without_a_server(logger, screenshot):
    attachments = Attachments(42)
    assert attachments.attach(logger, screenshot, "cmd", "lava", "1.1") is None
    assert results(logger) == []
    logger.info.assert_called_once()


@responses.activate
def test_attach(logger, screenshot):
    responses.add(
        responses.POST, ENDPOINT, json={"name": "cmd-001-screen.png", "size": 15}
    )
    attachments = Attachments(42, URL, "job-token")

    name = attachments.attach(logger, screenshot, "cmd", "0_smoke", "3.1")

    assert name == "cmd-001-screen.png"
    (request,) = responses.calls
    assert request.request.headers["LAVA-Token"] == "job-token"
    assert b'name="name"\r\n\r\ncmd-001-screen.png' in request.request.body
    assert b"\x89PNG not really" in request.request.body
    assert logged_attachments(logger) == [{"name": "cmd-001-screen.png", "size": 15}]
    assert results(logger) == [
        {
            "definition": "0_smoke",
            "case": "cmd-001-screen.png",
            "level": "3.1",
            "result": "pass",
            "extra": {"attachment": "cmd-001-screen.png"},
        }
    ]


@responses.activate
def test_attachments_are_numbered(logger, screenshot):
    responses.add(responses.POST, ENDPOINT, json={})
    attachments = Attachments(42, URL, "job-token")
    first = attachments.attach(logger, screenshot, "cmd", "lava", "1")
    second = attachments.attach(logger, screenshot, "cmd", "lava", "1")
    assert (first, second) == ("cmd-001-screen.png", "cmd-002-screen.png")


@responses.activate
def test_failed_upload_is_tried_again(logger, screenshot):
    responses.add(responses.POST, ENDPOINT, status=502, body="Bad Gateway")
    responses.add(responses.POST, ENDPOINT, json={})
    attachments = Attachments(42, URL, "job-token")

    assert attachments.attach(logger, screenshot, "cmd", "lava", "1") is None
    assert results(logger) == []
    assert "502: Bad Gateway" in str(logger.warning.mock_calls[0])

    attachments.retry_failed(logger)
    assert [result["result"] for result in results(logger)] == ["pass"]
    assert attachments.failed == []


@responses.activate
def test_upload_that_never_succeeds(logger, screenshot):
    responses.add(
        responses.POST,
        ENDPOINT,
        status=413,
        json={"error": "Attachment larger than 10 bytes"},
    )
    attachments = Attachments(42, URL, "job-token")
    attachments.attach(logger, screenshot, "cmd", "lava", "1")
    attachments.retry_failed(logger)

    assert "413: Attachment larger than 10 bytes" in str(logger.error.mock_calls[0])
    assert logged_attachments(logger) == []
    assert [result["result"] for result in results(logger)] == ["fail"]


def test_unreachable_server(logger, screenshot):
    attachments = Attachments(42, "http://127.0.0.1:1", "job-token")
    assert attachments.attach(logger, screenshot, "cmd", "lava", "1") is None
    assert len(attachments.failed) == 1


def test_missing_file(logger, tmp_path):
    attachments = Attachments(42, URL, "job-token")
    assert attachments.attach(logger, tmp_path / "gone.png", "cmd", "lava", "1") is None
    assert len(attachments.failed) == 1
