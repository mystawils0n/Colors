# Xumo TV Remote

A remote-only controller for **Element Xumo TVs** (and other HomeKit TVs) that runs in any web browser. It has power, arrows/OK, back, home, settings menu, volume, mute, play/pause, rewind/fast-forward, info, and an input picker. There are no ads and no streaming content.

Browsers can't talk HomeKit directly, so a small Python program (`xumo_remote.py`) pairs with the TV and serves the remote page. Run it on an Android phone (with Termux), a computer, or a Raspberry Pi on the same Wi-Fi as the TV.

## 1. Free up the TV's HomeKit pairing

A HomeKit accessory accepts a new pairing only while it isn't paired. **If the TV is in the Apple Home app, remove it there first** (Home app → TV → settings → Remove Accessory). It can be added back later, but then this remote stops working until you pair again.

Turn on HomeKit / AirPlay in the TV's settings so it shows a HomeKit setup code.

## 2. Install

**Android (Termux):** install [Termux from F-Droid](https://f-droid.org/packages/com.termux/), then:

```sh
pkg install python git
git clone https://github.com/mystawils0n/Colors && cd Colors/xumo-remote
pip install -r requirements.txt
```

**Windows / Mac / Linux:** install Python 3.10+, then run `pip install -r requirements.txt` in this folder.

## 3. Pair (once)

```sh
python xumo_remote.py discover
# Name:      Element Xumo TV
# Device ID: 12:34:56:AB:CD:EF
python xumo_remote.py pair 12:34:56:AB:CD:EF 123-45-678   # code from the TV screen
```

The pairing keys are saved in `pairing.json`. Keep that file private.

## 4. Use the remote

```sh
python xumo_remote.py serve
```

Open **http://localhost:8080** on the same device, or `http://<that device's IP>:8080` from any other phone, tablet or computer on your Wi-Fi. In the browser menu choose **Add to Home screen** to get an app icon. Use `--host 127.0.0.1` to allow only the device running the server.

On a computer, keyboard shortcuts work: arrows, Enter (OK), Backspace (back), Esc (home), Space (play/pause), +/- (volume), M (mute), P (power).

## Troubleshooting

- **`discover` finds nothing:** the TV may only offer HomeKit through AirPlay pairing and not as a standalone HomeKit accessory. In that case this tool can't reach it. Also check that both devices are on the same Wi-Fi network (not a guest network).
- **Buttons are greyed out:** the TV didn't advertise that control over HomeKit.
- **Try it without a TV:** `pip install HAP-python`, run `python fake_tv.py` (code `031-45-154`), then pair and serve as above. The fake TV prints every button press.
