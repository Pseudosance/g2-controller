#!/usr/bin/env python3
"""ring_introduce.py -- introduce the Flipper (impersonating an Even R1 ring) to the G2 glasses.

WHY THIS EXISTS
---------------
The G2 glasses never SCAN for a ring-shaped advertiser. The phone tells them an
address: the vendor app connects to the glasses over the Even UART Service (EUS)
control service and sends a RING_CONNECT_INFO message -- command id 6 -- carrying
`connectRing=true` plus the 6-byte ring MAC. Only then do the glasses dial that
ring address. Our Flipper fap (flipper/apps/g2_ring) already advertises and hosts
the R1 GATT service, but nothing ever tells the glasses to dial it. This client is
that missing step.

WHERE THE MESSAGE LAYOUT COMES FROM (quoted, not invented)
----------------------------------------------------------
MentraOS G2.kt, DevSettingsProto.ringConnectInfo(...):

    fun ringConnectInfo(
        magicRandom: Int, connect: Boolean, ringMac: ByteArray, ringName: String = ""
    ): ByteArray {
        val w = ProtobufWriter()
        w.writeInt32Field(1, DevCfgCommandId.RING_CONNECT_INFO.value) // commandId = RING_CONNECT_INFO (6)
        w.writeInt32Field(2, magicRandom)
        val ringW = ProtobufWriter()
        ringW.writeBoolField(1, connect)                 // connectRing
        ringW.writeBytesField(2, ringMac)                // ringMac (6 bytes)
        if (ringName.isNotEmpty()) {
            ringW.writeBytesField(3, ringName.toByteArray(Charsets.UTF_8)) // ringName
        }
        w.writeMessageField(5, ringW.toByteArray())      // ringInfo (field 5)
        return w.toByteArray()
    }

...and connectController() sends it with connect=true on DEVICE_SETTINGS (0x80),
RIGHT arm only (sendDevSettingsCommand defaults: left=false, right=true):

    val msg = DevSettingsProto.ringConnectInfo(sendManager.nextMagicRandom(), true, macData)
    sendDevSettingsCommand(msg)
    Bridge.log("G2: Sent RING_CONNECT_INFO for MAC $mac")

EvenBLETransport.buildPackets() sets the frame status byte to 0x20 only when
reserveFlag=true; sendDevSettingsCommand does NOT set reserveFlag, so the status
byte (frame byte 7) is 0x00. Frame header byte 1 = (DEST_GLASSES<<4)|SOURCE_PHONE
= (2<<4)|1 = 0x21, after HEADER_BYTE 0xAA.

The wire bytes are built by relay/g2proto.msg_ring_connect_info() -- the SAME
golden-tested protocol module the relay uses. This file only supplies the MAC,
the magic, the sid and the flag; it never re-implements framing.

HOW TO RUN
----------
Needs a machine with a REAL BLE radio (Windows/WinRT or Linux/BlueZ). WSL has no
radio -- run it from Windows or a Linux host with Bluetooth. The g2_ring fap must
be RUNNING on the Flipper (it must be advertising as the ring).

    python tools/ring_introduce.py --dry-run --ring AA:BB:CC:DD:EE:FF   # bytes only, no BLE
    python tools/ring_introduce.py --ring AA:BB:CC:DD:EE:FF             # connect the ring
    python tools/ring_introduce.py --ring AA:BB:CC:DD:EE:FF --disconnect
    python tools/ring_introduce.py --ring AA:BB:CC:DD:EE:FF --glasses XX:...:YY  # right-arm address

The MAC is the Flipper's own BLE address, shown on the g2_ring app's screen
("MAC AA:BB:CC:DD:EE:FF"); it is never hardcoded here.

Force-stop the Even app on the phone and turn the phone's Bluetooth OFF first --
the glasses accept one phone at a time, and the app will fight you for the link.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "relay"))
import g2proto as g2          # golden-tested EUS framing + message builders (reused, not rewritten)
import g2_relay as relay      # verified connect path + UUID handling; BleakClient/Scanner are None if bleak is absent

# ---------------------------------------------------------------------------
# Wire constants. Every value is quoted from MentraOS G2.kt (see module
# docstring). tests/test_ring_introduce.py parses these names out of this file
# and cross-checks them against the bytes g2proto builds -- rename one and it
# fails. This is a SOURCE-LEVEL lock; it cannot prove device behaviour.
# ---------------------------------------------------------------------------
SID_DEVICE_SETTINGS     = 0x80  # G2.kt ServiceID.DEVICE_SETTINGS (UX_DEVICE_SETTINGS_APP_ID)
CMD_RING_CONNECT_INFO   = 6     # G2.kt DevCfgCommandId.RING_CONNECT_INFO(6)
FIELD_CMD               = 1     # DevCfgDataPackage.commandId (int32)
FIELD_MAGIC             = 2     # DevCfgDataPackage.magicRandom (int32)
FIELD_RING_INFO         = 5     # DevCfgDataPackage.ringInfo (message)
RING_FIELD_CONNECT      = 1     # RingInfo.connectRing (bool)
RING_FIELD_MAC          = 2     # RingInfo.ringMac (bytes, 6)
RING_FIELD_NAME         = 3     # RingInfo.ringName (bytes; omitted when empty)
FRAME_FLAG_DEV_SETTINGS = 0x00  # EvenBLETransport status byte when reserveFlag=false

DEFAULT_MAGIC = 200             # magicRandom echoed back in the ack (field 2)
DEFAULT_SEQ   = 0x40            # frame syncId (byte 2); any value works for one command


def build_ring_connect_info(magic: int, connect: bool, mac: bytes) -> bytes:
    """DevCfgDataPackage{1:6, 2:magic, 5:RingInfo{1:connect, 2:mac}} via g2proto.

    Delegates the byte layout to g2proto so this client cannot drift from the
    frame format; asserts the constants above agree with what was built.
    """
    assert CMD_RING_CONNECT_INFO == g2.RING_CONNECT_INFO, "cmd id drifted from g2proto"
    return g2.msg_ring_connect_info(magic, connect, mac)


def framed(payload: bytes, seq: int) -> list[bytes]:
    """aa21 frames for this payload on the DEVICE_SETTINGS sid with the status byte 0x00."""
    return g2.frames(payload, seq=seq, sid=SID_DEVICE_SETTINGS, flag=FRAME_FLAG_DEV_SETTINGS)


def _hex(b: bytes) -> str:
    return b.hex(" ")


def do_dry_run(args, mac: bytes) -> int:
    """Print exactly what would go on the wire -- no BLE, no adapter needed."""
    connect = not args.disconnect
    payload = build_ring_connect_info(args.magic, connect, mac)
    frs = framed(payload, args.seq)
    print("dry-run: no BLE connection made")
    print(f"  ring MAC (display order) : {args.ring}")
    print(f"  connectRing              : {connect}")
    print(f"  magicRandom              : {args.magic} (0x{args.magic:02X})")
    print(f"  sid                      : 0x{SID_DEVICE_SETTINGS:02X} (DEVICE_SETTINGS, RIGHT arm)")
    print(f"  frame status byte        : 0x{FRAME_FLAG_DEV_SETTINGS:02X} (reserveFlag=false)")
    print(f"  protobuf payload ({len(payload)} bytes): {_hex(payload)}")
    for i, f in enumerate(frs):
        print(f"  frame[{i}] ({len(f)} bytes): {_hex(f)}")
    print(f"  CRC16/CCITT-FALSE (little-endian, payload-internal): "
          f"0x{g2.crc16_ccitt_false(payload):04X}")
    dec = g2.pb_decode(payload)
    print(f"  decoded: cmd={dec.get(FIELD_CMD)} magic={dec.get(FIELD_MAGIC)}")
    ring = g2.pb_decode(dec.get(FIELD_RING_INFO, [b""])[0])
    print(f"  decoded RingInfo: connect={ring.get(RING_FIELD_CONNECT)} "
          f"mac={ring.get(RING_FIELD_MAC, [b''])[0].hex(':')}")
    return 0


async def pick_glasses(wanted: str | None):
    """Resolve the RIGHT-arm glasses address (the side DEVICE_SETTINGS is sent to)."""
    if wanted:
        return wanted, wanted
    devs = await relay.BleakScanner.discover(timeout=8.0)
    found = [(d.address, (getattr(d, "name", "") or "")) for d in devs]
    arms = [(a, n) for a, n in found if relay.G2_NAME_RE.search(n) or "g2" in n.lower()]
    for a, n in arms:
        print(f"  found: {n or '(no name)'}  {a}")
    right = [x for x in arms if re.search(r"_R_|\bR\b", x[1])]
    if len(right) == 1:
        return right[0]
    if len(arms) == 1:
        return arms[0]
    if right:
        return right[0]
    sys.exit("Could not tell which arm is the RIGHT one; rerun with --glasses <address>.")


async def run_live(args, mac: bytes) -> int:
    if relay.BleakClient is None or relay.BleakScanner is None:
        sys.exit("bleak is not installed. `pip install bleak` and run on a host with a real "
                 "BLE radio (Windows/WinRT or Linux/BlueZ). WSL has NO radio.")
    connect = not args.disconnect
    payload = build_ring_connect_info(args.magic, connect, mac)
    frs = framed(payload, args.seq)

    addr, name = await pick_glasses(args.glasses)
    print(f"connecting to RIGHT arm {name or '(no name)'} ({addr}) ...")
    print(f"  RING_CONNECT_INFO payload ({len(payload)} bytes): {_hex(payload)}")

    link = relay.EUSLink()
    ack = {"sid": None, "payload": None}

    try:
        async with relay.BleakClient(addr, timeout=20) as client:
            async def w(data):
                print("  TX", _hex(bytes(data)))
                await client.write_gatt_char(g2.UUID_WRITE, data, response=False)
            link._write = w

            def on_notify(_, d):
                raw = bytes(d)
                print("  RX", _hex(raw))
                link.feed(raw)

            await client.start_notify(g2.UUID_NOTIFY, on_notify)

            print("session setup: handshake (sid 0x80) + prelude ...")
            await link.open()   # handshake_payloads() then PRELUDE, from g2proto (golden-tested)
            print("session live: handshake + prelude accepted by the glasses")

            print(f"sending RING_CONNECT_INFO (connectRing={connect}) on sid 0x"
                  f"{SID_DEVICE_SETTINGS:02X} ...")
            try:
                sid, rp = await link._send(
                    SID_DEVICE_SETTINGS, payload, timeout=args.timeout,
                    magic=args.magic, flag=FRAME_FLAG_DEV_SETTINGS)
                ack["sid"], ack["payload"] = sid, rp
            except TimeoutError:
                pass

            if ack["payload"] is None:
                print(f"\nNO ACK within {args.timeout:.0f}s.")
                print("  This does NOT itself mean failure: in real firmware the glasses dial the")
                print("  ring asynchronously and the ack is not required to act on it. Watch the")
                print("  Flipper's screen: the connection dot and 'con'/'gls' flags should flip.")
            else:
                dec = g2.pb_decode(ack["payload"])
                print(f"\nACK received ({len(ack['payload'])} bytes): {_hex(ack['payload'])}")
                print(f"  ack cmd={dec.get(FIELD_CMD)} magic={dec.get(FIELD_MAGIC)} "
                      f"ringInfo={dec.get(FIELD_RING_INFO)}")
                if dec.get(FIELD_CMD) == [CMD_RING_CONNECT_INFO]:
                    print("  -> glasses acknowledged RING_CONNECT_INFO (cmd 6)")
                ring = g2.pb_decode(dec.get(FIELD_RING_INFO, [b""])[0]) if dec.get(FIELD_RING_INFO) else None
                if ring and ring.get(RING_FIELD_CONNECT) == [1]:
                    print("  -> RingInfo.connectRing == 1: glasses report the ring as connected")

            if args.wait > 0:
                print(f"\nholding the session {args.wait}s so you can read the Flipper's screen ...")
                await asyncio.sleep(args.wait)
    except Exception as e:  # bleak raises BleakError/OSError when there is no radio
        low = str(e).lower()
        if any(k in low for k in ("bluetooth", "adapter", "radio", "winrt", "access is denied")):
            sys.exit(f"Bluetooth adapter problem: {e}\n"
                     "  Turn Bluetooth on, and run this from a host with a real BLE radio "
                     "(not WSL).")
        raise
    print("done; you can reopen the Even app now.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ring", required=True,
                    help="ring MAC in display order, AA:BB:CC:DD:EE:FF (the Flipper's "
                         "address, shown on the g2_ring screen). Never hardcoded.")
    ap.add_argument("--glasses", help="RIGHT-arm G2 BLE address (auto-detect if omitted)")
    ap.add_argument("--disconnect", action="store_true",
                    help="send connectRing=false instead of true")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the exact bytes and exit without touching BLE")
    ap.add_argument("--magic", type=int, default=DEFAULT_MAGIC,
                    help="magicRandom echo value, 1..255 (default %(default)s)")
    ap.add_argument("--seq", type=lambda s: int(s, 0), default=DEFAULT_SEQ,
                    help="frame syncId byte (default 0x40)")
    ap.add_argument("--timeout", type=float, default=4.0, help="seconds to wait for an ack")
    ap.add_argument("--wait", type=int, default=15, help="seconds to hold the session open")
    args = ap.parse_args()

    try:
        mac = g2.parse_mac(args.ring)
    except ValueError as e:
        sys.exit(f"bad --ring MAC: {e}")
    if len(mac) != 6:
        sys.exit("--ring must be 6 hex byte pairs")
    if not (1 <= args.magic <= 255):
        sys.exit("--magic must be 1..255 (0 would be dropped by proto3, unlike MentraOS)")

    if args.dry_run:
        return do_dry_run(args, mac)
    return asyncio.run(run_live(args, mac))


if __name__ == "__main__":
    sys.exit(main())
