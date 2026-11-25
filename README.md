# g2-controller — drive Even Realities G2 glasses from a Flipper Zero

Goal: let Mitchell drive the Even Realities G2 glasses' on-screen content with
Flipper Zero buttons instead of the Even **R1 ring**. The gestures to reproduce
are the ones the R1 emits: scroll up, scroll down, tap, double tap, and hold.

Two things exist and two do not. Read "The hard blocker" first — it decides which
architecture below is reachable from a stock Flipper.

---

## THE HARD BLOCKER (read before anything else)

A stock Flipper `.fap` **cannot** talk to the glasses directly **as a central**
(the R1 ring emulator sidesteps this by being a peripheral that the glasses dial;
see "The R1 ring emulator" below):

1. **A stock fap cannot be a BLE central.** It cannot scan for, connect out to, or
   discover services on the G2 — no GATT-client API is exported to apps. Flipper
   apps are BLE *peripherals* only.
2. **The BLE HID profile is DISABLED.** The `ble_profile_hid*` symbols exist in the
   SDK header but are marked DISABLED in the firmware API and are absent from
   `firmware.elf`, so `ufbt` hard-fails any build that uses them. The Flipper
   cannot emulate a keyboard. See `flipper/BLE_HID_BLOCKER.md` for the evidence.
3. **The Even app ignores external keyboard input.** So even a keyboard emulator
   would not drive the glasses.

Consequence: the Flipper cannot connect out to the glasses, and cannot type into
the phone's Even app. Any design that needs the Flipper to be a BLE central or a
keyboard is dead. Do not rediscover this.

---

## What works: the RELAY (offline-validated)

The one working architecture keeps the Flipper a peripheral and puts the BLE
central role on a PC:

    Flipper Zero (BLE peripheral, Serial GATT, advertises "G2Ctrl")
        --BLE serial--> relay daemon (bleak) --BLE(EUS)--> Even G2 glasses

`relay/g2_relay.py` is a single process that is a BLE **central to both** devices:
it connects to the Flipper's Serial GATT service (text lines) and to the glasses'
EUS service (framed protobuf), and moves events between them. It runs on Windows
(bleak WinRT backend) or Linux (BlueZ).

### Line protocol (Flipper <-> relay, newline-terminated ASCII)

Flipper -> relay, one bare token per line:

    TAP | DOUBLE_TAP | LONG_TAP | SCROLL_UP | SCROLL_DOWN | NAV_BACK | NAV_HOME | STATUS | PING | BYE

relay -> Flipper:

    HELLO <ver> | PONG | LINK 0|1 | PAGE <desc> | INFO <text>

The relay reads only the first whitespace-delimited token of each line and
upper-cases it; anything after it is ignored, so the app sends one bare token per
line and no argument. **An unrecognised token is echoed back to the Flipper as
`EVT <TOKEN>` and does nothing else** (`Relay.on_flipper_line`). `PING` and `BYE`
are recognised explicitly, so they are not echoed.

### Framing (the real protocol — not the fiction)

The framing the relay speaks, and the one the golden tests pin, is:

    MAGIC (aa 21) | seq u8 | len u8 | total u8 | index u8 | sid u8 | flag u8 | payload + crc16

The CRC is CRC-16/CCITT-FALSE over the whole payload, appended payload-internal,
little-endian. Strings are UTF-8; ack replies echo the request magic in protobuf
field 2. This is implemented in `relay/g2proto.py` — a faithful port of the
vendored `vendor/even-g2-js/g2.js` — and validated byte-for-byte by
`tests/test_g2proto_golden.py`.

### EUS service/characteristic UUIDs (from relay/g2proto.py, matching g2.js)

    service candidates: 00002760-08c2-11e1-9073-0e8ac72e{0000,5450,6450,7450}
    write:              00002760-08c2-11e1-9073-0e8ac72e5401
    notify:             00002760-08c2-11e1-9073-0e8ac72e5402

---

## The Flipper app (flipper/apps/g2_controller/g2_controller.c)

Emits the relay's command vocabulary from pad presses. Gesture -> emitted token:

| Gesture          | Token           | Relay action                       |
|------------------|-----------------|------------------------------------|
| UP tap           | SCROLL_UP       | menu selection up                  |
| UP hold          | SCROLL_UP (xN)  | held-key auto-repeat               |
| DOWN tap         | SCROLL_DOWN     | menu selection down                |
| DOWN hold        | SCROLL_DOWN (xN)| held-key auto-repeat               |
| OK short         | TAP (deferred)  | activate the highlighted item      |
| OK double-tap    | DOUBLE_TAP      | status ping                        |
| OK long press    | LONG_TAP        | status ping                        |
| LEFT short       | NAV_BACK        | previous page                      |
| RIGHT short      | NAV_HOME        | next page                          |
| LEFT long        | STATUS          | status ping                        |
| BACK             | (none)          | exit the app                       |
| ping timer (2 s) | PING            | keepalive; relay answers PONG      |
| app exit         | BYE             | farewell (logged, not a gesture)   |

**Deferred single tap.** A lone OK release does NOT emit `TAP` immediately. The
first release only arms a one-shot window of `DOUBLE_TAP_WINDOW_MS` (250 ms);
`TAP` is emitted only if that window expires with no second press. A second OK
release inside the window becomes `DOUBLE_TAP` and NO `TAP` is emitted. So **a
double-tap deliberately does not also activate the highlighted item** — the menu
contains Clear and Re-link, where a stray `TAP` would wipe the display or tear
down the glasses session. Cost: every single tap is delayed by up to 250 ms; that
window is the one tap-feel knob. A tap still pending at app exit is dropped.

Held UP/DOWN use the app's own auto-repeat timer: one command on press, then a
500 ms grace before repeating every 100 ms (so a quick tap sends exactly one
command). This is separate from the firmware's own long-press timing.

---

## The R1 ring emulator — WORKING (flipper/apps/g2_ring)

The Flipper can't be a BLE central, but it doesn't need to be: the **R1 ring is a
peripheral that the glasses dial**. `flipper/apps/g2_ring` advertises as an R1 and
hosts the ring's GATT service. After a one-time PC introduction, the G2 connects to
it on its own and accepts scroll, select, back and menu, with the phone and the
Even app still connected. No relay needed.

**Read [docs/R1-EMULATION.md](docs/R1-EMULATION.md)**: protocol, GATT layout, the
introduction command, report types (including the `0x09` tap-and-hold that opens
the menu), settings, Flipper SDK gotchas and what's still open.

---

## Build and test

### Flipper app (.fap)

Build with `ufbt` from the app directory:

    cd flipper/apps/g2_controller && ufbt

Output: `flipper/apps/g2_controller/dist/g2_controller.fap`

(on this machine `ufbt` is `/home/CachedOut/.local/bin/ufbt`). Install by copying
that file — via **qFlipper**; the Flipper has no USB mass-storage mode — to
`apps/g2_controller.fap` on the SD card. No firmware flashing.

### Tests

Run all three from the repo root with `python3` (verified with Python 3.14.9):

    python3 tests/test_g2proto_golden.py    # g2proto must match g2.js byte-for-byte
    python3 tests/test_relay_offline.py     # relay end-to-end vs an in-memory fake G2
    python3 tests/test_wire_contract.py     # C tokens == relay grammar, both directions

`test_g2proto_golden.py` additionally needs `node` on PATH (verified with
v26.12.1). All three pass with no BLE and no hardware.

---

## Repository map

**Real / authoritative**
- `relay/g2proto.py` — the EUS protocol layer (framing, CRC, protobuf, handshake,
  events). Port of the vendored `g2.js`; golden-tested. This is the contract.
- `relay/g2_relay.py` — the relay daemon (central to both Flipper and glasses;
  line protocol; render loop).
- `flipper/apps/g2_controller/` — the Flipper app; emits the relay's tokens.
- `tests/` — golden (vs g2.js), offline relay, and wire-contract tests.
- `flipper/HARDWARE-CHECKLIST.md` — physical bring-up procedure.
- `flipper/BLE_HID_BLOCKER.md` — evidence for the hard blocker.

**Reference (read-only, not our code)**
- `vendor/even-g2-js/g2.js` — vendored Web Bluetooth reference; `g2proto.py`
  mirrors it. Its own header notes the protocol is unofficial and community
  reverse-engineered.

**Experimental**
- The R1-ring impersonation plan above — no code exists.

**Dead / do not reuse**
- `android/_wip/` — SUPERSEDED first-generation Android relay. Its protocol layer
  (`EusPackets.kt`) uses a FABRICATED frame and fabricated commands. Do not reuse;
  see its own README.
- `flipper/attic/` — earlier hallucinated work (a "wrong" Flipper app that invents
  a BLE-central flow, HID, a fake frame and auth handshake; a "wrong" manifest; a
  `.hallucinated` relay). Do not reuse.

Earlier documents described a fabricated protocol — a raw
`[FLAGS][LEN_HI][LEN_LO][CMD][PAYLOAD][XOR checksum]` frame with byte commands
`0x01 PING`, `0x20 SCROLL`, `0x21 TAP`, `0x22 LONG_TAP`, `0x23 DOUBLE_TAP`,
`0x24 NAV_BACK`, `0x25 NAV_HOME` and a `0x02 AUTH` challenge. **None of that
exists.** If you find it again anywhere in this project, it is the same
hallucination, not a spec.

---

## KNOWN UNKNOWNS (confirmable only on real hardware)

Nothing below has been verified against a physical G2 or Flipper.

1. **Relay -> glasses handshake and prelude payloads.** The 7 `sid=0x80` start-up
   messages and the verbatim `sid=0x01` launch prelude in `relay/g2proto.py` are
   byte-faithful to `g2.js` (golden-tested), but a real G2's *acceptance* of them
   is unverified — no BLE sniff has been captured.
2. **sid / magic matching.** `EUSLink.feed` matches a reply to a pending request by
   magic, with a fallback ("complete the oldest waiter on that sid") for
   settings/status replies that carry data in protobuf field 2. That fallback is
   safe only while requests are sequential; verify with a sniff.
3. **Auth challenge.** It is unknown whether a production G2 demands a
   challenge/response auth before accepting a session; `g2proto` implements no
   auth step. If the glasses silently ignore the prelude, auth is the first
   suspect.
4. **Unbound stand-in ring.** RESOLVED: the glasses accept the Flipper as a ring
   via RING_CONNECT_INFO; see docs/R1-EMULATION.md §5.
5. **Double-tap ergonomics.** The 250 ms window (also the latency added to every
   single tap) and the 500 ms / 100 ms scroll auto-repeat are chosen for feel and
   are unverified by hand.
6. **One central at a time.** The glasses likely accept only a single active BLE
   central, so while the relay runs the phone's Even app will lose (or fail to
   hold) the link.
7. **Flipper <-> relay GATT pairing** is a heuristic: the relay picks the first
   writable characteristic for TX and the first notifiable one for RX on the
   Serial profile.
8. **R1-ring UUIDs.** RESOLVED: verified on hardware and used by
   flipper/apps/g2_ring; see docs/R1-EMULATION.md §4.
