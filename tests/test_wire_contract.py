"""Wire-contract test: the Flipper app's emitted command tokens must match the
relay's accepted grammar AND every emitted command must drive a real G2 action.

The dynamic half feeds the string literals PARSED OUT OF g2_controller.c (not
independent copies), so the two halves are coupled behaviourally, not just by a
static equality check. This FAILS if anyone renames a token on EITHER side:
  * change a string in flipper/.../g2_controller.c -> the fed literal changes and
    the relay no longer does the right thing (or echoes EVT)
  * rename a branch/token in relay/g2_relay.py Relay.on_flipper_line -> the
    emitted token is no longer accepted

Style mirrors tests/test_relay_offline.py (plain prints + check()), and drives the
exact strings through the real relay FlipperLink / on_flipper_line path into an
in-memory FakeG2.
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

# The contract: exactly the tokens the app may emit (gestures + keepalive + BYE).
EXPECTED = ["BYE", "DOUBLE_TAP", "LONG_TAP", "NAV_BACK", "NAV_HOME", "PING",
            "SCROLL_DOWN", "SCROLL_UP", "STATUS", "TAP"]

FAIL = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def parse_emitted():
    """{macro_name: "string"} for every `#define CMD_* "..."` in the C app."""
    src = open(C_FILE).read()
    return dict(re.findall(r'#define\s+(CMD_\w+)\s+"([^"]+)"', src))


def accepted_tokens():
    """Tokens Relay.on_flipper_line actually branches on."""
    src = open(RELAY_FILE).read()
    body = src[src.index("def on_flipper_line"):]
    m = re.search(r"\n    (async )?def ", body[1:])
    if m:
        body = body[:m.start() + 1]
    toks = set()
    for mm in re.finditer(r'cmd\s*==\s*"(\w+)"', body):
        toks.add(mm.group(1))
    for mm in re.finditer(r'cmd\s+in\s*\(([^)]*)\)', body):
        toks.update(re.findall(r'"(\w+)"', mm.group(1)))
    return sorted(toks)


def c_function_body(signature_prefix):
    """Extract a C function body by brace matching (no strings with braces in
    the functions we look at)."""
    src = open(C_FILE).read()
    i = src.index(signature_prefix)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return src[j:k + 1]


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
    E = parse_emitted()                       # {"CMD_SCROLL_UP": "SCROLL_UP", ...}
    emitted = sorted(set(E.values()))
    accepted = accepted_tokens()
    print("C emits      :", emitted)
    print("relay accepts:", accepted)
    print()

    # ---- static contract: emitted set == expected; relay must accept them ----
    check(emitted == sorted(EXPECTED), "C emits exactly the contract tokens")
    missing = [t for t in emitted if t not in accepted]
    check(not missing, "relay recognises every emitted token (missing: %s)" % missing)

    # ---- structural guard for the deferred single tap (defect-1 regression) --
    ok_body = c_function_body("static void g2_ok_short")
    check("CMD_TAP" not in ok_body,
          "OK-short handler does NOT emit TAP directly (single tap is deferred)")
    check("furi_timer_start" in ok_body,
          "OK-short handler arms the double-tap window timer instead")

    # ---- dynamic: push the EXACT literals parsed out of the C through the relay
    relay = R.Relay()
    link = R.EUSLink(on_event=relay.on_g2_event, on_page_live=relay.on_page_live, chunk=100)
    FakeG2(link)
    relay.g2 = link
    await link.open()
    check(link.page_live, "glasses session live (handshake + prelude)")

    fl = R.FlipperLink(relay.on_flipper_line)

    def feed(macro):                          # literal comes FROM THE C SOURCE
        lit = E[macro]                          # KeyError => C macro was renamed
        fl.feed((lit + "\n").encode("ascii"))

    # scroll (menu page -> menu selection moves)
    relay.page, relay.sel = 0, 0
    feed("CMD_SCROLL_DOWN")
    check(relay.sel == 1, "%s -> menu selection down" % E["CMD_SCROLL_DOWN"])
    feed("CMD_SCROLL_UP")
    check(relay.sel == 0, "%s -> menu selection up" % E["CMD_SCROLL_UP"])

    # tap activates the selected menu item ("Status") -> status page
    relay.page, relay.sel = 0, 0
    feed("CMD_TAP")
    await asyncio.sleep(0.25)
    check(relay.pages[relay.page] == "status",
          "%s -> activates item (status page shown)" % E["CMD_TAP"])

    # DEFECT-1 REGRESSION GUARD: a double tap emits ONLY DOUBLE_TAP (the C defers
    # the single tap), so it must produce EXACTLY one status ping and NO activate.
    relay.page, relay.sel = 0, 0
    n = len(relay.sayq)
    feed("CMD_DOUBLE_TAP")
    await asyncio.sleep(0.25)
    new = relay.sayq[n:]
    pings = [l for l in new if l.startswith("INFO g2 battery")]
    check(len(pings) == 1, "double tap -> EXACTLY one status ping (%d)" % len(pings))
    check(relay.page == relay.pages.index("menu") and relay.sel == 0,
          "double tap -> NO activate (still on menu, selection unchanged)")

    # a lone tap (what the deferred C emits) DOES activate
    relay.page, relay.sel = 0, 0
    feed("CMD_TAP")
    await asyncio.sleep(0.25)
    check(relay.pages[relay.page] == "status", "lone tap -> DOES activate")

    # long tap / status -> status ping (distinct from the tap/activate path)
    for macro in ("CMD_LONG_TAP", "CMD_STATUS"):
        relay.page = relay.pages.index("menu")
        n = len(relay.sayq)
        feed(macro)
        await asyncio.sleep(0.25)
        got = relay.sayq[n:]
        check(any(l.startswith("INFO g2 battery 77") for l in got),
              "%s -> G2 status ping (%s)" % (E[macro], got[-1] if got else "no reply"))

    # navigation
    relay.page = 1
    feed("CMD_NAV_HOME")
    check(relay.page == 2, "%s -> next page" % E["CMD_NAV_HOME"])
    feed("CMD_NAV_BACK")
    check(relay.page == 1, "%s -> previous page" % E["CMD_NAV_BACK"])

    # keepalive
    n = len(relay.sayq)
    feed("CMD_PING")
    check("PONG" in relay.sayq[n:], "PING -> PONG")

    # split-line reassembly (framing robustness)
    n = len(relay.sayq)
    fl.feed(b"PIN")
    fl.feed(b"G\n")
    check("PONG" in relay.sayq[n:], "FlipperLink reassembles a split line")

    # BYE is a recognised farewell: no echo, no render
    n = len(relay.sayq)
    feed("CMD_BYE")
    check(not any(l.startswith("EVT ") for l in relay.sayq[n:]),
          "BYE -> recognised (no EVT echo)")

    # EVERY emitted token (including BYE) must be recognised: sweep them all and
    # assert none produces the relay's "EVT <tok>" unrecognised echo.
    relay.sayq.clear()
    for macro in sorted(E):
        feed(macro)
    await asyncio.sleep(0.2)
    echoes = [l for l in relay.sayq if l.startswith("EVT ")]
    check(not echoes, "no emitted token (incl BYE) fell through to the relay EVT echo (%s)" % echoes)

    print()
    print("FAILURES:", FAIL if FAIL else "none")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
