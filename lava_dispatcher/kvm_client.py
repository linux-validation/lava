# Copyright (C) 2026 Qualcomm Inc.
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Driving the device's keyboard, mouse and screen through a KVM.

A test asks for KVM actions with the lava-kvm helper. The dispatcher checks
each request against a fixed list of actions and runs the worker's KVM client
for it, naming the device's KVM target:

* the client is koidaemon's koictl, which supports several kinds of KVM and
  reaches its daemon at /run/koidaemon/koid.sock. A worker's dispatcher
  configuration can name another with ``kvm_command``: anything with koictl's
  command line.
* the target is the device's name, unless its device dictionary gives
  another with ``{% set kvm_target = 'board-a' %}``.
 The request comes from the device, so it is
never given to a shell: arguments are checked here and passed to the client
as separate arguments, after "--" so that none can be taken as an option.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from shlex import split as shlex_split

DEFAULT_KVM_COMMAND = "koictl"
MAX_TEXT_LENGTH = 4096
MAX_ARGUMENT_LENGTH = 64
BUTTONS = ("left", "right", "middle")
# Key names such as "f2", "ctrl+alt+delete", "-" or "KeyA".
KEY_PATTERN = re.compile(r"^[A-Za-z0-9+_.,;'=`/\\\[\]-]+$")
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
NUMBER_PATTERN = re.compile(r"^-?[0-9]+$")


class KVMRequestError(Exception):
    """The test asked for something the KVM cannot be asked for."""


@dataclass
class KVMRequest:
    action: str
    # The client's arguments after the target, e.g. ["keyboard", "press", ...].
    arguments: list[str]
    # For "snapshot": what to call the picture.
    label: str | None = None


def decode_arguments(encoded: list[str]) -> list[str]:
    """Undo lava-kvm's encoding of each argument: an "a" and then base64, so
    that spaces, ">" and empty arguments survive the test shell signal."""
    arguments = []
    for item in encoded:
        if not item.startswith("a"):
            raise KVMRequestError(f"Badly encoded argument {item!r}")
        try:
            data = base64.b64decode(item[1:], validate=True)
            arguments.append(data.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError):
            raise KVMRequestError(f"Badly encoded argument {item!r}")
    return arguments


def _check_count(action: str, arguments: list[str], low: int, high: int) -> None:
    if not low <= len(arguments) <= high:
        if low == high:
            expected = str(low)
        else:
            expected = f"{low} to {high}"
        raise KVMRequestError(
            f"'{action}' takes {expected} arguments, not {len(arguments)}"
        )


def _number(text: str, what: str, allow_negative: bool = False) -> str:
    if not NUMBER_PATTERN.match(text) or len(text) > 6:
        raise KVMRequestError(f"{what} must be a whole number, not {text!r}")
    if text.startswith("-") and not allow_negative:
        raise KVMRequestError(f"{what} must not be negative")
    return text


def _key(text: str) -> str:
    if not KEY_PATTERN.match(text) or len(text) > MAX_ARGUMENT_LENGTH:
        raise KVMRequestError(f"Invalid key name {text!r}")
    return text


def _button_and_position(action: str, arguments: list[str]) -> list[str]:
    """[BUTTON] [X Y], as taken by click and double-click."""
    _check_count(action, arguments, 0, 3)
    button = "left"
    if len(arguments) in (1, 3):
        button = arguments.pop(0)
    if button not in BUTTONS:
        raise KVMRequestError(f"Unknown button {button!r}")
    options = ["--button", button]
    if len(arguments) == 2:
        options += ["--at", _number(arguments[0], "x"), _number(arguments[1], "y")]
    elif arguments:
        raise KVMRequestError(f"'{action}' needs both x and y, or neither")
    return options


def parse_request(action: str, arguments: list[str]) -> KVMRequest:
    """Turn a test's request into the client's arguments."""
    arguments = list(arguments)

    if action == "snapshot":
        _check_count(action, arguments, 0, 1)
        label = arguments[0] if arguments else "screen"
        if not LABEL_PATTERN.match(label) or len(label) > MAX_ARGUMENT_LENGTH:
            raise KVMRequestError(f"Invalid snapshot label {label!r}")
        return KVMRequest(action, ["snapshot"], label=label)

    if action == "type":
        _check_count(action, arguments, 1, 1)
        if len(arguments[0]) > MAX_TEXT_LENGTH:
            raise KVMRequestError(f"Text longer than {MAX_TEXT_LENGTH} characters")
        return KVMRequest(action, ["keyboard", "type", "--", arguments[0]])

    if action == "press":
        _check_count(action, arguments, 1, 16)
        keys = [_key(key) for key in arguments]
        return KVMRequest(action, ["keyboard", "press", "--", *keys])

    if action in ("key-down", "key-up"):
        _check_count(action, arguments, 1, 1)
        direction = "down" if action == "key-down" else "up"
        return KVMRequest(action, ["keyboard", direction, "--", _key(arguments[0])])

    if action == "move":
        _check_count(action, arguments, 2, 2)
        x = _number(arguments[0], "x")
        y = _number(arguments[1], "y")
        return KVMRequest(action, ["mouse", "move", "--", x, y])

    if action == "click":
        return KVMRequest(
            action, ["mouse", "click", *_button_and_position(action, arguments)]
        )

    if action == "double-click":
        options = _button_and_position(action, arguments)
        return KVMRequest(action, ["mouse", "click", "--count", "2", *options])

    if action in ("mouse-down", "mouse-up"):
        _check_count(action, arguments, 0, 1)
        button = arguments[0] if arguments else "left"
        if button not in BUTTONS:
            raise KVMRequestError(f"Unknown button {button!r}")
        direction = "down" if action == "mouse-down" else "up"
        return KVMRequest(action, ["mouse", direction, "--button", button])

    if action == "scroll":
        _check_count(action, arguments, 1, 1)
        notches = _number(arguments[0], "notches", allow_negative=True)
        return KVMRequest(action, ["mouse", "scroll", "--", notches])

    raise KVMRequestError(f"Unknown KVM action {action!r}")


def client_command(kvm_command: str, target: str, arguments: list[str]) -> list[str]:
    """The full command line: the configured client, then the target."""
    return [*shlex_split(kvm_command), "-t", target, *arguments]
