#!/usr/bin/env python3
"""Remote-only web controller for HomeKit TVs (built for Element Xumo TVs).

Browsers can't speak HomeKit, so this small bridge pairs with the TV over
the HomeKit Accessory Protocol and serves a remote-control web page that
any phone, tablet or computer on your Wi-Fi can open.

    python xumo_remote.py discover            # find HomeKit TVs on the network
    python xumo_remote.py pair <id> <code>    # pair once with the TV's HomeKit code
    python xumo_remote.py serve               # start the web remote
    python xumo_remote.py unpair              # forget the TV
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import pathlib
import sys

from aiohttp import web
from aiohomekit import Controller
from aiohomekit.characteristic_cache import CharacteristicCacheFile
from aiohomekit.exceptions import HomeKitException
from aiohomekit.zeroconf import ZeroconfServiceListener
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

ALIAS = "tv"
HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_DATA = HERE / "pairing.json"

# HomeKit short UUIDs (HAP specification).
SVC_TELEVISION = "D8"
SVC_INPUT_SOURCE = "D9"
SVC_SPEAKER = "113"
CHR_ACTIVE = "B0"
CHR_ACTIVE_IDENTIFIER = "E7"
CHR_REMOTE_KEY = "E1"
CHR_VOLUME_SELECTOR = "EA"
CHR_MUTE = "11A"
CHR_POWER_MODE_SELECTION = "DF"
CHR_IDENTIFIER = "E6"
CHR_CONFIGURED_NAME = "E3"
CHR_NAME = "23"
CHR_IS_CONFIGURED = "D6"
CHR_VISIBILITY = "135"

REMOTE_KEYS = {
    "rewind": 0,
    "fast_forward": 1,
    "next": 2,
    "previous": 3,
    "up": 4,
    "down": 5,
    "left": 6,
    "right": 7,
    "select": 8,
    "back": 9,
    "exit": 10,
    "play_pause": 11,
    "info": 15,
}

log = logging.getLogger("xumo_remote")


def short_uuid(uuid: str) -> str:
    """'000000D8-0000-1000-8000-0026BB765291' -> 'D8'."""
    uuid = uuid.upper()
    if uuid.endswith("-0000-1000-8000-0026BB765291"):
        return uuid.split("-", 1)[0].lstrip("0") or "0"
    return uuid


@contextlib.asynccontextmanager
async def homekit(data_file: pathlib.Path):
    zc = AsyncZeroconf()
    controller = Controller(
        async_zeroconf_instance=zc,
        char_cache=CharacteristicCacheFile(data_file.with_name("charmap.json")),
    )
    async with zc:
        browser = AsyncServiceBrowser(
            zc.zeroconf,
            ["_hap._tcp.local.", "_hap._udp.local."],
            listener=ZeroconfServiceListener(),
        )
        async with controller:
            controller.load_data(str(data_file))
            yield controller
        await browser.async_cancel()


class TV:
    """Finds the Television service on a paired accessory and sends commands to it."""

    def __init__(self, pairing):
        self.pairing = pairing
        self.chars: dict[str, tuple[int, int]] = {}
        self.inputs: list[dict] = []
        self.name = "TV"
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        accessories = await self.pairing.list_accessories_and_characteristics()
        input_services = {}
        for acc in accessories:
            aid = acc["aid"]
            for svc in acc["services"]:
                stype = short_uuid(svc["type"])
                chars = {short_uuid(c["type"]): c for c in svc["characteristics"]}
                if stype == SVC_TELEVISION:
                    for key in (CHR_ACTIVE, CHR_ACTIVE_IDENTIFIER, CHR_REMOTE_KEY,
                                CHR_POWER_MODE_SELECTION):
                        if key in chars:
                            self.chars[key] = (aid, chars[key]["iid"])
                    name = chars.get(CHR_CONFIGURED_NAME) or chars.get(CHR_NAME)
                    if name and name.get("value"):
                        self.name = name["value"]
                elif stype == SVC_SPEAKER:
                    for key in (CHR_VOLUME_SELECTOR, CHR_MUTE):
                        if key in chars:
                            self.chars[key] = (aid, chars[key]["iid"])
                elif stype == SVC_INPUT_SOURCE:
                    input_services[svc["iid"]] = chars
        self.inputs = []
        for chars in input_services.values():
            if CHR_IDENTIFIER not in chars:
                continue
            if chars.get(CHR_IS_CONFIGURED, {}).get("value") == 0:
                continue
            if chars.get(CHR_VISIBILITY, {}).get("value") == 1:  # 1 = hidden
                continue
            name = chars.get(CHR_CONFIGURED_NAME) or chars.get(CHR_NAME) or {}
            ident = chars[CHR_IDENTIFIER]["value"]
            self.inputs.append({"id": ident, "name": name.get("value") or f"Input {ident}"})
        self.inputs.sort(key=lambda i: i["id"])
        if CHR_REMOTE_KEY not in self.chars:
            log.warning("This accessory has no Remote Key characteristic; "
                        "arrow/OK/back buttons won't work.")

    def supports(self) -> dict:
        return {
            "power": CHR_ACTIVE in self.chars,
            "keys": CHR_REMOTE_KEY in self.chars,
            "volume": CHR_VOLUME_SELECTOR in self.chars,
            "mute": CHR_MUTE in self.chars,
            "settings": CHR_POWER_MODE_SELECTION in self.chars,
            "inputs": CHR_ACTIVE_IDENTIFIER in self.chars and bool(self.inputs),
        }

    async def _get(self, key: str):
        aid_iid = self.chars[key]
        result = await self.pairing.get_characteristics([aid_iid])
        return result.get(aid_iid, {}).get("value")

    async def _put(self, key: str, value) -> None:
        if key not in self.chars:
            raise web.HTTPNotImplemented(text=f"The TV doesn't support this ({key}).")
        aid, iid = self.chars[key]
        async with self._lock:
            errors = await self.pairing.put_characteristics([(aid, iid, value)])
        if errors:
            raise web.HTTPBadGateway(text=f"TV rejected the command: {errors}")

    async def status(self) -> dict:
        status = {"name": self.name, "supports": self.supports(), "inputs": self.inputs}
        for field, key in (("power", CHR_ACTIVE), ("input", CHR_ACTIVE_IDENTIFIER),
                           ("muted", CHR_MUTE)):
            if key in self.chars:
                with contextlib.suppress(Exception):
                    status[field] = await self._get(key)
        return status

    async def key(self, name: str) -> None:
        await self._put(CHR_REMOTE_KEY, REMOTE_KEYS[name])

    async def power(self, on: bool | None) -> None:
        if on is None:
            on = not await self._get(CHR_ACTIVE)
        await self._put(CHR_ACTIVE, 1 if on else 0)

    async def volume(self, up: bool) -> None:
        await self._put(CHR_VOLUME_SELECTOR, 0 if up else 1)

    async def mute(self) -> None:
        await self._put(CHR_MUTE, not await self._get(CHR_MUTE))

    async def settings(self) -> None:
        await self._put(CHR_POWER_MODE_SELECTION, 0)

    async def set_input(self, ident: int) -> None:
        await self._put(CHR_ACTIVE_IDENTIFIER, ident)


def make_app(tv: TV) -> web.Application:
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(_):
        return web.FileResponse(HERE / "web" / "index.html")

    @routes.get("/api/status")
    async def status(_):
        return web.json_response(await tv.status())

    @routes.post("/api/key/{name}")
    async def key(request):
        name = request.match_info["name"]
        if name not in REMOTE_KEYS:
            raise web.HTTPNotFound(text=f"Unknown key {name}")
        await tv.key(name)
        return web.json_response({"ok": True})

    @routes.post("/api/power/{state}")
    async def power(request):
        state = request.match_info["state"]
        await tv.power({"on": True, "off": False}.get(state))
        return web.json_response({"ok": True})

    @routes.post("/api/volume/{direction}")
    async def volume(request):
        await tv.volume(request.match_info["direction"] == "up")
        return web.json_response({"ok": True})

    @routes.post("/api/mute")
    async def mute(_):
        await tv.mute()
        return web.json_response({"ok": True})

    @routes.post("/api/settings")
    async def settings(_):
        await tv.settings()
        return web.json_response({"ok": True})

    @routes.post("/api/input/{ident}")
    async def set_input(request):
        await tv.set_input(int(request.match_info["ident"]))
        return web.json_response({"ok": True})

    @web.middleware
    async def errors(request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except (HomeKitException, OSError, asyncio.TimeoutError) as e:
            log.warning("Command failed: %r", e)
            raise web.HTTPServiceUnavailable(text=f"Couldn't reach the TV: {e}")

    app = web.Application(middlewares=[errors])
    app.add_routes(routes)
    return app


async def cmd_discover(args) -> int:
    async with homekit(args.data) as controller:
        print(f"Searching for HomeKit accessories for {args.timeout} seconds...")
        await asyncio.sleep(args.timeout)
        found = 0
        async for d in controller.async_discover():
            desc = d.description
            found += 1
            print(f"\n  Name:      {desc.name}")
            print(f"  Device ID: {desc.id}")
            print(f"  Category:  {desc.category!s}")
            print(f"  Paired:    {'yes (remove it from Apple Home first)' if d.paired else 'no - ready to pair'}")
        if not found:
            print("\nNo HomeKit accessories found. Make sure this device is on the same "
                  "Wi-Fi as the TV and that HomeKit is enabled in the TV's settings.")
    return 0


async def cmd_pair(args) -> int:
    async with homekit(args.data) as controller:
        if ALIAS in controller.aliases:
            print("Already paired with a TV. Run 'unpair' first to pair a different one.")
            return 1
        print(f"Looking for {args.device_id}...")
        discovery = await controller.async_find(args.device_id, timeout=args.timeout)
        try:
            finish = await discovery.async_start_pairing(ALIAS)
            pairing = await finish(args.code)
        except HomeKitException as e:
            print(f"Pairing failed: {e}")
            print("If the TV is already in Apple Home, remove it there first, then retry.")
            return 1
        controller.load_pairing(ALIAS, pairing.pairing_data)
        controller.save_data(str(args.data))
        print("Paired! Now run:  python xumo_remote.py serve")
    return 0


async def cmd_unpair(args) -> int:
    async with homekit(args.data) as controller:
        if ALIAS not in controller.aliases:
            print("Not paired with any TV.")
            return 0
        try:
            await controller.remove_pairing(ALIAS)
        except Exception as e:  # TV unreachable: forget it locally anyway
            print(f"Couldn't tell the TV ({e}); forgetting it locally.")
            controller.aliases.pop(ALIAS, None)
        controller.save_data(str(args.data))
        print("Unpaired.")
    return 0


async def cmd_serve(args) -> int:
    async with homekit(args.data) as controller:
        if ALIAS not in controller.aliases:
            print("Not paired yet. Run 'discover' and then 'pair' first.")
            return 1
        tv = TV(controller.aliases[ALIAS])
        print("Connecting to the TV...")
        await tv.load()
        print(f"Connected to {tv.name}. Supported: "
              + ", ".join(k for k, v in tv.supports().items() if v))

        runner = web.AppRunner(make_app(tv))
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
        shown = "localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host
        print(f"\nRemote ready: open http://{shown}:{args.port} in your browser.")
        if args.host == "0.0.0.0":
            print(f"Other devices on your Wi-Fi: http://<this device's IP>:{args.port}")
        print("Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=pathlib.Path, default=DEFAULT_DATA,
                        help="where pairing keys are stored (default: %(default)s)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="list HomeKit accessories on the network")
    p.add_argument("--timeout", type=int, default=10)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("pair", help="pair with the TV using its HomeKit setup code")
    p.add_argument("device_id", help="Device ID shown by 'discover'")
    p.add_argument("code", help="HomeKit setup code shown on the TV, e.g. 123-45-678")
    p.add_argument("--timeout", type=int, default=30)
    p.set_defaults(func=cmd_pair)

    p = sub.add_parser("serve", help="start the web remote")
    p.add_argument("--host", default="0.0.0.0",
                   help="use 127.0.0.1 to allow only this device (default: %(default)s)")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("unpair", help="forget the paired TV")
    p.set_defaults(func=cmd_unpair)

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
