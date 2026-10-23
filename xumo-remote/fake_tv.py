#!/usr/bin/env python3
"""A pretend HomeKit TV for trying out the remote without a real TV.

    pip install HAP-python
    python fake_tv.py        # setup code: 031-45-154

It prints every command it receives, so you can check the remote works.
"""

import logging
import signal

from pyhap.accessory import Accessory
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_TELEVISION

INPUTS = ["Live TV", "HDMI 1", "HDMI 2"]
KEYS = {0: "rewind", 1: "fast_forward", 2: "next", 3: "previous", 4: "up", 5: "down",
        6: "left", 7: "right", 8: "select", 9: "back", 10: "exit", 11: "play_pause",
        15: "info"}


class FakeTV(Accessory):
    category = CATEGORY_TELEVISION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tv = self.add_preload_service(
            "Television", ["Name", "ConfiguredName", "Active", "ActiveIdentifier",
                           "RemoteKey", "SleepDiscoveryMode", "PowerModeSelection"])
        tv.configure_char("ConfiguredName", value="Fake Xumo TV")
        tv.configure_char("SleepDiscoveryMode", value=1)
        tv.configure_char("Active", value=1,
                          setter_callback=lambda v: print(f"POWER -> {'on' if v else 'off'}"))
        tv.configure_char("ActiveIdentifier", value=1,
                          setter_callback=lambda v: print(f"INPUT -> {INPUTS[v - 1]}"))
        tv.configure_char("RemoteKey", setter_callback=lambda v: print(f"KEY -> {KEYS.get(v, v)}"))
        tv.configure_char("PowerModeSelection", setter_callback=lambda v: print("SETTINGS MENU"))

        for i, name in enumerate(INPUTS, start=1):
            src = self.add_preload_service(
                "InputSource", ["Name", "ConfiguredName", "Identifier", "IsConfigured",
                                "InputSourceType", "CurrentVisibilityState"])
            src.configure_char("Name", value=name)
            src.configure_char("ConfiguredName", value=name)
            src.configure_char("Identifier", value=i)
            src.configure_char("IsConfigured", value=1)
            tv.add_linked_service(src)

        speaker = self.add_preload_service(
            "TelevisionSpeaker", ["Active", "VolumeControlType", "VolumeSelector", "Mute"])
        speaker.configure_char("VolumeControlType", value=1)
        speaker.configure_char("VolumeSelector",
                               setter_callback=lambda v: print(f"VOLUME -> {'down' if v else 'up'}"))
        speaker.configure_char("Mute", setter_callback=lambda v: print(f"MUTE -> {bool(v)}"))
        tv.add_linked_service(speaker)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    driver = AccessoryDriver(port=51826, persist_file="fake_tv.state", pincode=b"031-45-154")
    driver.add_accessory(FakeTV(driver, "Fake Xumo TV"))
    signal.signal(signal.SIGTERM, driver.signal_handler)
    print("Fake TV running. HomeKit setup code: 031-45-154")
    driver.start()
