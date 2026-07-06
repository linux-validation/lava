# Copyright 2026 Qualcomm Inc.
#
# Author: Matt Hart <matthart@qti.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from unittest.mock import MagicMock, patch

from lava_dispatcher.utils import udev
from tests.lava_dispatcher.test_basic import LavaDispatcherTestCase


def _fake_usb_device(vendor_id, product_id):
    device = MagicMock()
    device.properties = {"ID_VENDOR_ID": vendor_id, "ID_MODEL_ID": product_id}
    return device


class TestUsbDevicePresent(LavaDispatcherTestCase):
    @patch("lava_dispatcher.utils.udev.pyudev.Context")
    def _run(self, present, product_ids, context_cls, vendor="05c6"):
        context_cls.return_value.list_devices.return_value = [
            _fake_usb_device(v, p) for v, p in present
        ]
        return udev.usb_device_present(vendor, product_ids)

    def test_matches_any_of_several_product_ids(self):
        # A board that crashed into the 0x90db diag-dump mode is detected even
        # though it is not the classic 0x900e.
        self.assertEqual(
            self._run([("05c6", "90db")], ["900e", "901d", "90db"]),
            "90db",
        )

    def test_single_product_id_string_still_supported(self):
        self.assertEqual(self._run([("05c6", "900e")], "900e"), "900e")

    def test_firehose_9008_is_not_a_match(self):
        # 0x9008 is the normal flashing mode - not in the crashdump set - so a
        # board sitting in firehose must not be mistaken for a crash.
        self.assertIsNone(self._run([("05c6", "9008")], ["900e", "901d", "90db"]))

    def test_no_device_returns_none(self):
        self.assertIsNone(self._run([], ["900e", "901d", "90db"]))

    def test_wrong_vendor_ignored(self):
        self.assertIsNone(self._run([("1234", "900e")], ["900e", "901d", "90db"]))
