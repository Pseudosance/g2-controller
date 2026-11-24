#!/usr/bin/env python3
"""G2 relay daemon: Flipper Zero (BLE peripheral, Serial service) -> EUS frames -> Even G2 glasses.

    Flipper buttons --BLE serial--> this relay --BLE(EUS)--> G2 display
                                       ^ events/acks back to Flipper as text lines

The Flipper app advertises itself with the Serial profile device_name_prefix "G2Ctrl"
(see flipper/apps/g2_controller/). One process drives both BLE roles: we are Central
to each device. Runs on Windows (bleak WinRT backend) or Linux (BlueZ Central).

Line protocol with the Flipper app (newline-terminated, ASCII):
  Flipper -> relay:  TAP | DOUBLE_TAP | LONG_TAP | SCROLL_UP | SCROLL_DOWN |
                     NAV_BACK | NAV_HOME | STATUS | PING | BYE
  relay -> Flipper:  HELLO <ver> | PONG | LINK 0|1 | PAGE <desc> | INFO <text>

Protocol layer (relay/g2proto.py) is golden-tested against vendor/even-g2-js/g2.js.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    BleakClient = BleakScanner = None  # offline tests still import this module

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g2proto as g2

log = logging.getLogger("g2relay")

RELAY_VERSION = b"1"
FLIPPER_NAME_RE = re.compile(r"g2ctrl|flipper", re.I)
G2_NAME_RE = re.compile(r"^(even[ _-]?g2|g2_)", re.I)

GESTURE_TO_EVENT = {
    "tap": "TAP", "double-tap": "DOUBLE_TAP", "swipe-forward": "NAV_HOME",
    "swipe-back": "NAV_BACK", "foreground-enter": "FOREGROUND",
}
N = "\n"  # becomes a real backslash-n at write time


class EUSLink:
    """One G2 glasses connection: handshake, session, framed send/ack, heartbeat."""

    def __init__(self, on_event=None, on_page_live=None, chunk=200):
        self._write = lambda data: asyncio.sleep(0)
        self.on_event = on_event or (lambda ev: None)
        self.on_page_live = on_page_live or (lambda live: None)
        self.chunk = chunk
        self.seq = 0x40
        self.magic = 100
        self.page_live = False
        self.status = None
        self._reasm = g2.Reassembler()
        self._waiters = {}
        self._raw_waiters = {}
        self._hb_task = None
        self._closed = False

    def _next_magic(self):
        self.magic = 100 if self.magic >= 255 else self.magic + 1
        return self.magic

    async def _send(self, sid, payload, *, timeout=3.0, magic=None, flag=g2.FLAG_REQUEST):
        if self._closed:
            raise ConnectionError("link closed")
        m = self._next_magic() if magic is None else magic
        fut = asyncio.get_running_loop().create_future()
        self._waiters[f"{sid}:{m}"] = fut
        try:
            for f in g2.frames(payload, seq=self.seq, sid=sid, flag=flag, chunk=self.chunk):
                await self._write(f)
            self.seq = (self.seq + 1) & 0xFF
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"no reply for sid={sid:#x} magic={m}")
        finally:
            self._waiters.pop(f"{sid}:{m}", None)

    def feed(self, raw):
        """One BLE notification -> complete frame -> waiters / events.

        Frame layout: MAGIC(2) seq len total index sid flag payload+crc -- the
        byte after MAGIC is constant 0x21, the request/notify/response flag is
        byte 7 (verified against g2.js via the golden tests).
        """
        out = self._reasm.push(raw)
        if out is None:
            return
        sid, flag, payload = out
        dec = g2.Decoded(g2.pb_decode(payload)) if payload else g2.Decoded(b"")
        m = dec.uint(2)
        key = f"{sid}:{m}"
        fut = self._raw_waiters.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result((sid, payload))
            return
        if flag == g2.FLAG_NOTIFY:
            ev = g2.decode_event(payload)
            if ev and self.on_event:
                self.on_event(ev)
            return
        fut = self._waiters.get(key)
        if fut is None:
            # Responses on some sids (settings/status!) carry payload data in
            # field 2 instead of echoing the request magic, so magic matching
            # alone stalls. Complete the oldest pending waiter on that sid --
            # exact while requests are sequential (all callers await). VERIFY
            # against a BLE sniff when hardware is available
            # (HARDWARE-CHECKLIST.md item 3).
            for k, f in list(self._waiters.items()):
                if k.startswith(str(sid) + ":") and not f.done():
                    fut = f
                    break
        if fut and not fut.done():
            fut.set_result((sid, payload))

    async def open(self):
        for flag, payload in g2.handshake_payloads(int(time.time() * 1000)):
            m = g2.Decoded(g2.pb_decode(payload)).uint(2)
            try:
                await self._send(g2.SID["handshake"], payload, timeout=1.0, magic=m, flag=flag)
            except TimeoutError:
                log.warning("handshake message magic=%s not acknowledged", m)
        prelude_fut = self._raw_wait(g2.SID["app"], 156)  # register BEFORE write
        await self._write(bytes(g2.PRELUDE))  # verbatim launch frame (ack magic 156)
        try:
            await asyncio.wait_for(prelude_fut, 4.0)
        except asyncio.TimeoutError:
            raise ConnectionError("glasses didn't accept the session "
                                  "(connected to another app or phone?)")
        self.page_live = True
        self.on_page_live(True)
        self._hb_task = asyncio.create_task(self._heartbeat())
        return self

    def _raw_wait(self, sid, magic):
        fut = asyncio.get_running_loop().create_future()
        self._waiters[f"{sid}:{magic}"] = fut
        return fut

    async def _heartbeat(self):
        while not self._closed:
            await asyncio.sleep(5.0)
            try:
                await self._send(g2.SID["evenhub"], g2.msg_heartbeat(0), timeout=2.0)
            except (TimeoutError, ConnectionError):
                log.warning("heartbeat failed")
                self.page_live = False
                self.on_page_live(False)
                return

    async def close(self):
        self._closed = True
        if self._hb_task:
            self._hb_task.cancel()

    async def render_text(self, name, content):
        reply = await self._send(g2.SID["evenhub"],
                                 g2.msg_text_upgrade(self.magic, name, content), timeout=2.0)
        pb = g2.Decoded(g2.pb_decode(reply[1]))
        up = pb.message(10)
        code = up.uint(1) if up else 0
        if code == 9:
            return True
        await self._send(g2.SID["evenhub"],
                         g2.msg_rebuild_text(self.magic, name=name, content=content))
        return False

    async def create_page(self, name="g2relay", items=("flipper",)):
        await self._send(g2.SID["evenhub"], g2.msg_create(0, name=name, items=list(items)))

    async def show_list(self, name, items):
        return await self._send(g2.SID["evenhub"], g2.msg_rebuild_list(0, name=name, items=items[:20]))

    async def fetch_status(self):
        sid, payload = await self._send(g2.SID["settings"], g2.msg_status_query(0), timeout=3.0)
        self.status = g2.parse_status(payload)
        return self.status


class FlipperLink:
    """Text-line protocol over the Flipper Serial service."""

    def __init__(self, on_line=None):
        self.on_line = on_line or (lambda s: None)
        self._buf = ""

    def feed(self, raw):
        self._buf += bytes(raw).decode("utf-8", "replace")
        while chr(10) in self._buf:
            line, self._buf = self._buf.split(chr(10), 1)
            line = line.strip()
            if line:
                self.on_line(line)


class Relay:
    """Glue: Flipper button events -> glasses display; glasses events -> Flipper."""

    def __init__(self, args=None):
        self.args = args
        self.flip = None
        self.g2 = None
        self.page = 0
        self.pages = ["menu", "notes", "status"]
        self.sel = 0
        self.menu = ["Status", "Battery", "Time", "Notes", "Clear", "Re-link"]
        self.note = "flipper relay live"
        self._render_task = None
        self._dirty = False
        self.text_name = "text-a"
        self.list_name = "list-a"
        self.flip_link_up = False
        self._flip_write = lambda b: asyncio.sleep(0)
        self.sayq = []  # captured relay->flipper lines (tests inspect)

    def say(self, text):
        self.sayq.append(text)
        asyncio.ensure_future(self._write_flipper((text + chr(10)).encode()))

    async def _write_flipper(self, data):
        try:
            await self._flip_write(data)
        except Exception as e:
            log.debug("flipper write failed: %s", e)

    def request_render(self):
        self._dirty = True
        if self._render_task is None or self._render_task.done():
            self._render_task = asyncio.create_task(self._render_loop())

    async def _render_loop(self):
        while self._dirty:
            self._dirty = False
            if not (self.g2 and self.g2.page_live):
                return
            name = self.pages[self.page]
            try:
                if name == "menu":
                    items = [("> " if i == self.sel else "  ") + it
                             for i, it in enumerate(self.menu)]
                    self.list_name = "list-b" if self.list_name == "list-a" else "list-a"
                    await self.g2.show_list(self.list_name, items)
                    self.say(f"PAGE menu {self.sel + 1}/{len(self.menu)}")
                elif name == "status":
                    st = self.g2.status or await self.g2.fetch_status() or {}
                    body = ("Even G2" + chr(10) + chr(10) +
                            f"battery: {st.get('battery', '?')}%" +
                            ("  charging" if st.get("charging") else "") + chr(10) +
                            f"fw: {st.get('firmware', '?')}" + chr(10) + "relay: flipper zero")
                    await self._show_text(body)
                else:
                    await self._show_text(self.note)
            except (TimeoutError, ConnectionError) as e:
                log.warning("render failed: %s", e)
                self.g2.page_live = False
                return

    async def _show_text(self, body):
        self.text_name = "text-b" if self.text_name == "text-a" else "text-a"
        await self.g2.render_text(self.text_name, body)
        self.say("PAGE " + self.pages[self.page])

    def on_flipper_line(self, line):
        log.info("flipper> %s", line)
        cmd = line.split(maxsplit=1)[0].upper()
        on_text_page = self.pages[self.page] != "menu"
        if cmd == "PING":
            self.say("PONG")
            return
        if cmd == "BYE":
            # Flipper is closing the app: log and do nothing else (no EVT echo,
            # no render) so the farewell stays a clean, recognised token.
            log.info("flipper signed off (BYE)")
            return
        if cmd == "SCROLL_DOWN":
            if on_text_page:
                self.page = (self.page + 1) % len(self.pages)
            else:
                self.sel = min(self.sel + 1, len(self.menu) - 1)
        elif cmd == "SCROLL_UP":
            if on_text_page:
                self.page = (self.page - 1) % len(self.pages)
            else:
                self.sel = max(self.sel - 1, 0)
        elif cmd == "NAV_HOME":
            self.page = (self.page + 1) % len(self.pages)
        elif cmd == "NAV_BACK":
            self.page = (self.page - 1) % len(self.pages)
        elif cmd == "TAP":
            asyncio.ensure_future(self.activate())
            return
        elif cmd in ("DOUBLE_TAP", "LONG_TAP", "STATUS"):
            asyncio.ensure_future(self._g2_status_ping())
        else:
            self.say("EVT " + cmd)
        self.request_render()

    async def activate(self):
        if not (self.g2 and self.g2.page_live):
            self.say("INFO glasses session not live")
            return
        if self.pages[self.page] != "menu":
            self.page = 0
            self.request_render()
            return
        item = self.menu[self.sel]
        if item == "Status":
            self.page = self.pages.index("status")
        elif item == "Battery":
            st = await self.g2.fetch_status()
            self.say(f"INFO g2 battery {st and st.get('battery')}%")
        elif item == "Time":
            await self._show_text(time.strftime("%H:%M"))
            return
        elif item == "Notes":
            self.page = self.pages.index("notes")
        elif item == "Clear":
            await self._show_text(" ")
            return
        elif item == "Re-link":
            self.g2.page_live = False
            self.g2.on_page_live(False)
            return
        self.request_render()

    async def _g2_status_ping(self):
        if self.g2 and self.g2.page_live:
            st = await self.g2.fetch_status()
            self.say(f"INFO g2 battery {st and st.get('battery')}% fw {st and st.get('firmware')}")
        else:
            self.say("INFO glasses session not live")

    def on_g2_event(self, ev):
        log.info("g2 event: %s", ev)
        if ev.get("container", "").startswith("list-") and ev.get("index") is not None and ev.get("item"):
            idx = ev["index"]
            if 0 <= idx < len(self.menu):
                self.sel = idx
                asyncio.ensure_future(self.activate())
            return
        name = GESTURE_TO_EVENT.get(ev.get("type"))
        if name == "FOREGROUND":
            if self.g2:
                self.g2.page_live = False
                self.g2.on_page_live(False)
                asyncio.ensure_future(self._restart_session())
            return
        if name:
            self.on_flipper_line(name)

    async def _restart_session(self):
        if not self.g2:
            return
        try:
            await self.g2.open()
            await self.g2.create_page()
            self.request_render()
        except (ConnectionError, TimeoutError) as e:
            log.warning("session restart failed: %s", e)

    def on_page_live(self, live):
        self.say("LINK " + ("1" if live else "0"))
        if live:
            self.request_render()


def find_device_name(devs, pattern):
    for d in devs:
        name = getattr(d, "name", "") or ""
        if pattern.search(name):
            return d.address, name
    return None, None


async def connect_flipper(relay, wanted=None):
    while True:
        try:
            if BleakScanner is None:
                raise RuntimeError("bleak not installed (pip install bleak)")
            addr, name = (wanted, wanted)
            if not addr:
                addr, name = find_device_name(await BleakScanner.discover(timeout=5.0), FLIPPER_NAME_RE)
            if not addr:
                log.info("waiting for Flipper (G2Ctrl)...")
                await asyncio.sleep(3)
                continue

            async with BleakClient(addr, timeout=15) as client:
                log.info("flipper connected: %s", name)
                relay.flip_link_up = True

                async def w(data):
                    for ch in client.services:
                        for c in ch.characteristics:
                            props = c.properties
                            if "write-without-response" in props or "write" in props:
                                await client.write_gatt_char(c, data,
                                    response="write-without-response" not in props)
                                return
                relay._flip_write = w
                rx = None
                for ch in client.services:
                    for c in ch.characteristics:
                        if "notify" in c.properties:
                            rx = c
                if rx:
                    await client.start_notify(rx, lambda _, d: relay.flip.feed(d))
                await relay._write_flipper(b"HELLO " + RELAY_VERSION + bytes([10]))
                while client.is_connected:
                    await asyncio.sleep(0.5)
        except Exception as e:
            log.warning("flipper link error: %s", e)
        finally:
            relay.flip_link_up = False
        await asyncio.sleep(3)


async def connect_g2(relay, wanted=None):
    while True:
        try:
            if BleakScanner is None:
                raise RuntimeError("bleak not installed (pip install bleak)")
            addr, name = (wanted, wanted)
            if not addr:
                addr, name = find_device_name(await BleakScanner.discover(timeout=5.0), G2_NAME_RE)
            if not addr:
                log.info("waiting for G2 glasses...")
                await asyncio.sleep(3)
                continue

            link = EUSLink(on_event=relay.on_g2_event, on_page_live=relay.on_page_live)
            relay.g2 = link

            async with BleakClient(addr, timeout=15) as client:
                log.info("g2 connected: %s", name)

                async def w(data):
                    await client.write_gatt_char(g2.UUID_WRITE, data, response=False)
                link._write = w
                await client.start_notify(g2.UUID_NOTIFY, lambda _, d: link.feed(d))
                await link.open()
                await link.create_page()
                relay.request_render()
                while client.is_connected and not link._closed:
                    await asyncio.sleep(0.5)
        except Exception as e:
            log.warning("g2 link error: %s", e)
            relay.on_page_live(False)
        await asyncio.sleep(3)


async def amain():
    ap = argparse.ArgumentParser(description="Flipper Zero -> Even G2 BLE relay")
    ap.add_argument("--flipper", help="Flipper BLE address (auto-detect G2Ctrl if omitted)")
    ap.add_argument("--glasses", help="G2 BLE address (auto-detect by name if omitted)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    relay = Relay(args)
    relay.flip = FlipperLink(on_line=relay.on_flipper_line)
    await asyncio.gather(connect_flipper(relay, args.flipper), connect_g2(relay, args.glasses))


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
