# Copyright 2026 Qualcomm Inc.
#
# Author: Matt Hart <matthart@qti.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from unittest.mock import MagicMock, patch

from lava_dispatcher.utils import udev
from tests.lava_dispatcher.test_basic import LavaDispatcherTestCase


def _product_string(serial):
    # what a Qualcomm board in EDL or crashdump mode calls itself
    return f"QUSB_BULK_CID:046A_SN:{serial}"


def _fake_usb_device(
    vendor_id, product_id, serial=None, id_serial_short=None, from_sysfs=False
):
    """
    A udev USB device. A Qualcomm board in EDL or crashdump mode advertises its
    serial only inside the product string, so `serial` goes there. Pass
    id_serial_short for a device reporting a serial number descriptor instead,
    or from_sysfs for one whose product string udev did not pick up, leaving it
    readable only as a sysfs attribute - which is what a dispatcher running in
    a container sees.
    """
    device = MagicMock()
    device.properties = {"ID_VENDOR_ID": vendor_id, "ID_MODEL_ID": product_id}
    if serial is not None and not from_sysfs:
        device.properties["ID_PRODUCT"] = _product_string(serial)
    if id_serial_short is not None:
        device.properties["ID_SERIAL_SHORT"] = id_serial_short

    sysfs = {}
    if serial is not None and from_sysfs:
        sysfs["product"] = _product_string(serial)

    def asstring(name):
        # a real EDL device has no sysfs "serial" attribute
        return sysfs[name]

    device.attributes.asstring.side_effect = asstring
    return device


class TestUsbDevicePresent(LavaDispatcherTestCase):
    @patch("lava_dispatcher.utils.udev.pyudev.Context")
    def _run(self, present, product_ids, context_cls, vendor="05c6", serial=None):
        context_cls.return_value.list_devices.return_value = [
            (
                _fake_usb_device(*device)
                if isinstance(device, tuple)
                else _fake_usb_device(**device)
            )
            for device in present
        ]
        return udev.usb_device_present(vendor, product_ids, serial=serial)

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

    def test_serial_found_in_the_product_string(self):
        # An EDL board has no serial descriptor: udev reports the serial only
        # inside ID_PRODUCT, e.g. "QUSB_BULK_CID:046A_SN:95BA0DAE".
        self.assertEqual(
            self._run([("05c6", "900e", "95BA0DAE")], ["900e"], serial="95BA0DAE"),
            "900e",
        )

    def test_serial_selects_one_board_of_several(self):
        present = [
            ("05c6", "900e", "AAAAAAAA"),
            ("05c6", "900e", "95BA0DAE"),
            ("05c6", "900e", "BBBBBBBB"),
        ]
        self.assertEqual(
            self._run(present, ["900e", "901d", "90db"], serial="95BA0DAE"), "900e"
        )

    def test_other_boards_in_crashdump_are_not_ours(self):
        # The reported bug (job 5622): two other boards on the worker were in
        # EDL crashdump mode, so every job there believed its board had crashed.
        present = [("05c6", "900e", "AAAAAAAA"), ("05c6", "900e", "BBBBBBBB")]
        self.assertIsNone(
            self._run(present, ["900e", "901d", "90db"], serial="95BA0DAE")
        )

    def test_a_different_serial_of_the_same_width_never_matches(self):
        for wanted in ("95BA0DAF", "AAAAAAAA", "95ba0dae"):
            with self.subTest(wanted=wanted):
                self.assertIsNone(
                    self._run([("05c6", "900e", "95BA0DAE")], ["900e"], serial=wanted)
                )

    def test_serial_descriptor_is_accepted_too(self):
        # Not every mode hides the serial in the product string.
        self.assertEqual(
            self._run(
                [("05c6", "900e", None, "95BA0DAE")], ["900e"], serial="95BA0DAE"
            ),
            "900e",
        )

    def test_device_advertising_no_serial_never_matches(self):
        self.assertIsNone(self._run([("05c6", "900e")], ["900e"], serial="95BA0DAE"))

    def test_without_a_serial_any_board_matches(self):
        # Backwards compatible: callers that do not care still get any match.
        self.assertEqual(self._run([("05c6", "900e", "AAAAAAAA")], ["900e"]), "900e")

    def test_product_string_read_from_sysfs(self):
        # In a container udev often supplies no properties at all; the product
        # string is then readable only as a sysfs attribute. This is the path
        # WaitQDLDeviceAction already relies on via get_device_properties().
        device = {
            "vendor_id": "05c6",
            "product_id": "900e",
            "serial": "95BA0DAE",
            "from_sysfs": True,
        }
        self.assertEqual(self._run([device], ["900e"], serial="95BA0DAE"), "900e")
        self.assertIsNone(self._run([device], ["900e"], serial="AAAAAAAA"))
