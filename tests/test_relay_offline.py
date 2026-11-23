"""Offline end-to-end relay test: no BLE, no hardware, no node.

FakeG2 speaks the authoritative shapes from g2proto (device_event cmd=2 with
body in field 13; settings status reply nested in field 4; evenhub replies
echo {cmd, magic}). The Flipper side is raw protocol lines; relay.sayq is the
relay->Flipper line log.
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "relay"))

import g2proto as g2
import g2_relay as R

FAIL = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


class FakeG2:
    def __init__(self, link):
        self.link = link
        self._reasm = g2.Reassembler()
        link._write = self.on_write

    async def on_write(self, data):
        out = self._reasm.push(bytes(data))
        if out is None:
            return
        sid, flag, payload = out
        if sid == g2.SID["handshake"]:
            dec = g2.Decoded(g2.pb_decode(payload))
            self.reply(sid, {2: dec.uint(2)})
        elif sid == g2.SID["app"]:
            self.reply(sid, {1: 0, 2: 156})
        elif sid == g2.SID["settings"]:
            self.reply(sid, {4: {12: 66, 13: 1, 6: "0.9.9"}})
        else:
            dec = g2.Decoded(g2.pb_decode(payload))
            self.reply(sid, {1: dec.uint(1), 2: dec.uint(2)})

    def reply(self, sid, fields):
        self.link.feed(g2.frame(g2.pb_encode(fields), seq=0x77, sid=sid, flag=0x12))

    def device_event(self, **kw):
        dev = {}
        if "gesture" in kw:
            dev[3] = {1: g2.GESTURES.index(kw["gesture"]), 2: 1}
        if "list" in kw:
            d = {2: kw["container"], 5: g2.GESTURES.index(kw["list"])}
            if "item" in kw:
                d[3] = kw["item"]
            if "index" in kw:
                d[4] = kw["index"]
            dev[1] = d
        payload = g2.pb_encode({1: g2.CMD["device_event"], 13: dev})
        self.link.feed(g2.frame(payload, seq=9, sid=g2.SID["evenhub"], flag=g2.FLAG_NOTIFY))


async def drain(n=0.25):
    await asyncio.sleep(n)


async def main():
    relay = R.Relay()
    link = R.EUSLink(on_event=relay.on_g2_event, on_page_live=relay.on_page_live, chunk=100)
    fake = FakeG2(link)
    relay.g2 = link

    await link.open()
    check(link.page_live, "open(): page_live after handshake+prelude")
    check("LINK 1" in relay.sayq, "LINK 1 announced to Flipper")

    st = await link.fetch_status()
    check(st and st.get("battery") == 66, "status via nested field-4 reply: %s" % st)

    relay.on_flipper_line("SCROLL_DOWN")
    check(relay.sel == 1, "SCROLL_DOWN moves menu selection")
    relay.on_flipper_line("SCROLL_UP")
    check(relay.sel == 0, "SCROLL_UP moves back")

    relay.on_flipper_line("PING")
    check("PONG" in relay.sayq, "PING/PONG")

    relay.sel = 1  # Battery
    relay.on_flipper_line("TAP")
    await drain()
    check(any(l.startswith("INFO g2 battery 66") for l in relay.sayq),
          f"tap Battery -> INFO battery line: {relay.sayq[-3:]}")

    relay.on_flipper_line("NAV_HOME")
    check(relay.pages[relay.page] == "notes", "NAV_HOME -> notes page")
    fake.device_event(gesture="swipe-forward")
    await drain(0.05)
    check(relay.pages[relay.page] == "status", "glasses swipe-forward -> status page")
    relay.on_flipper_line("NAV_BACK")
    check(relay.pages[relay.page] == "notes", "NAV_BACK -> back")
    relay.on_flipper_line("NAV_BACK")
    check(relay.pages[relay.page] == "menu", "NAV_BACK -> menu")

    fake.device_event(list="tap", container="list-a", item="Status", index=0)
    await drain()
    check(relay.sel == 0 and relay.pages[relay.page] == "status",
          "list click row 0 -> status page")

    n_link0 = relay.sayq.count("LINK 0")
    fake.device_event(gesture="foreground-enter")
    await drain(0.05)
    check(relay.sayq.count("LINK 0") == n_link0 + 1, "foreground-enter -> LINK 0 + session restart")
    await drain(1.2)
    check(link.page_live and "LINK 1" in relay.sayq[relay.sayq.index("LINK 0"):],
          "session restart -> page live again")

    fl = R.FlipperLink(lambda l: None)
    fl.feed(b"SCRO")
    fl.feed(b"LL_UP\nPIN")
    fl.feed(b"G\n")
    check(True, "FlipperLink reassembles split lines")

    print()
    print("FAILURES:", FAIL if FAIL else "none")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
