# Copyright (C) 2026 Qualcomm Inc.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import base64

import pytest

from lava_dispatcher.kvm_client import (
    KVMRequestError,
    client_command,
    decode_arguments,
    parse_request,
)


def encode(*arguments):
    """As lava-kvm encodes them."""
    return [
        "a" + base64.b64encode(argument.encode()).decode() for argument in arguments
    ]


def test_decode_arguments():
    arguments = ["root", "two words", "<LAVA_SIGNAL_X>", "", "naïve"]
    assert decode_arguments(encode(*arguments)) == arguments


@pytest.mark.parametrize("encoded", ["cm9vdA==", "a!!!", "a/w=="])
def test_badly_encoded_arguments(encoded):
    with pytest.raises(KVMRequestError, match="Badly encoded"):
        decode_arguments([encoded])


@pytest.mark.parametrize(
    "action, arguments, client_arguments",
    [
        ("type", ["root\n"], ["keyboard", "type", "--", "root\n"]),
        ("type", ["-h"], ["keyboard", "type", "--", "-h"]),
        (
            "press",
            ["f2", "ctrl+alt+delete"],
            ["keyboard", "press", "--", "f2", "ctrl+alt+delete"],
        ),
        ("press", ["-"], ["keyboard", "press", "--", "-"]),
        ("key-down", ["shift"], ["keyboard", "down", "--", "shift"]),
        ("key-up", ["shift"], ["keyboard", "up", "--", "shift"]),
        ("move", ["10", "20"], ["mouse", "move", "--", "10", "20"]),
        ("click", [], ["mouse", "click", "--button", "left"]),
        ("click", ["right"], ["mouse", "click", "--button", "right"]),
        ("click", ["5", "6"], ["mouse", "click", "--button", "left", "--at", "5", "6"]),
        (
            "double-click",
            ["middle", "5", "6"],
            ["mouse", "click", "--count", "2", "--button", "middle", "--at", "5", "6"],
        ),
        ("mouse-down", [], ["mouse", "down", "--button", "left"]),
        ("mouse-up", ["right"], ["mouse", "up", "--button", "right"]),
        ("scroll", ["-3"], ["mouse", "scroll", "--", "-3"]),
    ],
)
def test_parse_request(action, arguments, client_arguments):
    request = parse_request(action, arguments)
    assert request.arguments == client_arguments
    assert request.label is None


def test_snapshot():
    assert parse_request("snapshot", []).label == "screen"
    request = parse_request("snapshot", ["boot-menu"])
    assert (request.arguments, request.label) == (["snapshot"], "boot-menu")


@pytest.mark.parametrize(
    "action, arguments, message",
    [
        ("reboot", [], "Unknown KVM action"),
        ("snapshot", ["../x"], "Invalid snapshot label"),
        ("snapshot", ["a", "b"], "takes 0 to 1 arguments"),
        ("type", [], "takes 1 arguments"),
        ("type", ["x" * 4097], "longer than"),
        ("press", ["ctrl c"], "Invalid key name"),
        ("move", ["1"], "takes 2 arguments"),
        ("move", ["-1", "2"], "must not be negative"),
        ("move", ["1.5", "2"], "whole number"),
        ("click", ["5"], "Unknown button"),
        ("click", ["left", "5"], "whole number"),
        ("mouse-down", ["thumb"], "Unknown button"),
        ("scroll", ["up"], "whole number"),
    ],
)
def test_invalid_requests(action, arguments, message):
    with pytest.raises(KVMRequestError, match=message):
        parse_request(action, arguments)


def test_options_cannot_be_smuggled_in():
    # Everything from the device comes after "--", so it is never an option.
    request = parse_request("press", ["--socket=/tmp/x"])
    assert request.arguments == ["keyboard", "press", "--", "--socket=/tmp/x"]


def test_client_command():
    command = client_command(
        "koictl --socket '/run/k oid.sock'", "board-a", ["snapshot"]
    )
    assert command == [
        "koictl",
        "--socket",
        "/run/k oid.sock",
        "-t",
        "board-a",
        "snapshot",
    ]
