"""Wire-contract test: the Flipper app's emitted command tokens must match the
relay's accepted grammar AND every emitted command must drive a real G2 action.

This FAILS if anyone renames a token on EITHER side:
  * rename in flipper/apps/g2_controller/g2_controller.c -> emitted != contract
  * rename in relay/g2_relay.py Relay.on_flipper_line    -> accepted lacks token

Style mirrors tests/test_relay_offline.py (plain prints + check()), and feeds the
EXACT strings the C emits through the real relay FlipperLink / on_flipper_line
path into an in-memory FakeG2.
"""
import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "relay"))

import g2proto as g2
import g2_relay as R

C_FILE = os.path.join(ROOT, "flipper", "apps", "g2_controller", "g2_controller.c")
RELAY_FILE = os.path.join(ROOT, "relay", "g2_relay.py")

# The contract: exactly the gesture + keepalive tokens the app may emit.
EXPECTED = ["SCROLL_UP", "SCROLL_DOWN", "TAP", "DOUBLE_TAP", "LONG_TAP",
            "NAV_BACK", "NAV_HOME", "STATUS", "PING"]

FAIL = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def emitted_tokens():
    """Every `#define CMD_xxx "TOKEN"` string literal in the C app."""
    src = open(C_FILE).read()
    return sorted(set(re.findall(r'#define\s+CMD_\w+\s+"([^"]+)"', src)))


def accepted_tokens():
    """Tokens Relay.on_flipper_line actually branches on."""
    src = open(RELAY_FILE).read()
    start = src.index("def on_flipper_line")
    body = src[start:]
    m = re.search(r"\n    (async )?def ", body[1:])
    if m:
        body = body[:m.start() + 1]
    toks = set()
    for mm in re.finditer(r'cmd\s*==\s*"(\w+)"', body):
        toks.add(mm.group(1))
    for mm in re.finditer(r'cmd\s+in\s*\(([^)]*)\)', body):
        toks.update(re.findall(r'"(\w+)"', mm.group(1)))
    return sorted(toks)


class FakeG2:
    """Minimal in-memory G2 (mirrors tests/test_relay_offline.py)."""
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
            self.reply(sid, {4: {12: 77, 13: 1, 6: "9.9"}})
        else:
            dec = g2.Decoded(g2.pb_decode(payload))
            self.reply(sid, {1: dec.uint(1), 2: dec.uint(2)})

    def reply(self, sid, fields):
        self.link.feed(g2.frame(g2.pb_encode(fields), seq=0x55, sid=sid, flag=0x12))


async def main():
    emitted = emitted_tokens()
    accepted = accepted_tokens()
    print("C emits      :", emitted)
    print("relay accepts:", accepted)
    print()

    # ---- static contract: emitted set == expected; relay must accept them ----
    check(emitted == sorted(EXPECTED), "C emits exactly the contract tokens")
    missing = [t for t in EXPECTED if t not in accepted]
    check(not missing, "relay recognises every emitted token (missing: %s)" % missing)

    # ---- dynamic: push the EXACT emitted strings through the real relay path ----
    relay = R.Relay()
    link = R.EUSLink(on_event=relay.on_g2_event, on_page_live=relay.on_page_live, chunk=100)
    FakeG2(link)
    relay.g2 = link
    await link.open()
    check(link.page_live, "glasses session live (handshake + prelude)")

    fl = R.FlipperLink(relay.on_flipper_line)

    def feed(tok):  # exactly what the Flipper writes on the wire: token + '\n'
        fl.feed((tok + "\n").encode("ascii"))

    # scroll (menu page -> menu selection moves)
    relay.page, relay.sel = 0, 0
    feed("SCROLL_DOWN")
    check(relay.sel == 1, "SCROLL_DOWN -> menu selection down")
    feed("SCROLL_UP")
    check(relay.sel == 0, "SCROLL_UP -> menu selection up")

    # tap activates the selected menu item ("Status") -> status page
    relay.page, relay.sel = 0, 0
    feed("TAP")
    await asyncio.sleep(0.25)
    check(relay.pages[relay.page] == "status", "TAP -> activates item (status page shown)")

    # double-tap / long-tap / status all request a G2 status fetch
    for tok in ("DOUBLE_TAP", "LONG_TAP", "STATUS"):
        n = len(relay.sayq)
        feed(tok)
        await asyncio.sleep(0.25)
        got = relay.sayq[n:]
        check(any(l.startswith("INFO g2 battery 77") for l in got),
              "%s -> G2 status ping (%s)" % (tok, got[-1] if got else "no reply"))

    # navigation
    relay.page = 1
    feed("NAV_HOME")
    check(relay.page == 2, "NAV_HOME -> next page")
    feed("NAV_BACK")
    check(relay.page == 1, "NAV_BACK -> previous page")

    # keepalive
    n = len(relay.sayq)
    feed("PING")
    check("PONG" in relay.sayq[n:], "PING -> PONG")

    # split-line reassembly (framing robustness)
    n = len(relay.sayq)
    fl.feed(b"PIN")
    fl.feed(b"G\n")
    check("PONG" in relay.sayq[n:], "FlipperLink reassembles a split line")

    # NOTHING may fall through to the "EVT <tok>" unrecognised echo
    echoes = [l for l in relay.sayq if l.startswith("EVT ")]
    check(not echoes, "no emitted token fell through to the relay EVT echo (%s)" % echoes)

    print()
    print("FAILURES:", FAIL if FAIL else "none")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
