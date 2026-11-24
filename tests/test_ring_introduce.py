"""Ring-introduce wire-contract test -- SOURCE-LEVEL ONLY.

!! THIS IS A SOURCE-LEVEL CHECK, NOT A BEHAVIOURAL ONE. !!
It parses the wire constants out of tools/ring_introduce.py and checks that the
bytes relay/g2proto builds for RING_CONNECT_INFO match the values quoted from
MentraOS G2.kt. It CANNOT prove the glasses honour the command, that a byte
leaves the antenna, or that BLE works at all. Only a hardware run can show that.

The constants are PARSED OUT OF THE CLIENT SOURCE (the way tests/test_wire_contract.py
parses tokens out of the C app), so renaming a constant in the client fails here.

Style mirrors tests/test_wire_contract.py (plain prints + check()).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "relay"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import g2proto as g2
import ring_introduce as ri   # the client itself (imports are bleak-optional)

CLIENT = os.path.join(ROOT, "tools", "ring_introduce.py")

# Values quoted VERBATIM from MentraOS G2.kt (see the client docstring for the
# exact source lines). These are the ground truth this test locks to.
EXPECTED = {
    "SID_DEVICE_SETTINGS": 0x80,   # ServiceID.DEVICE_SETTINGS
    "CMD_RING_CONNECT_INFO": 6,    # DevCfgCommandId.RING_CONNECT_INFO(6)
    "FIELD_CMD": 1,                # DevCfgDataPackage.commandId
    "FIELD_MAGIC": 2,              # DevCfgDataPackage.magicRandom
    "FIELD_RING_INFO": 5,          # DevCfgDataPackage.ringInfo
    "RING_FIELD_CONNECT": 1,       # RingInfo.connectRing
    "RING_FIELD_MAC": 2,           # RingInfo.ringMac
    "RING_FIELD_NAME": 3,          # RingInfo.ringName
    "FRAME_FLAG_DEV_SETTINGS": 0x00,  # EvenBLETransport status byte (reserveFlag=false)
}

MAC = "80:E1:26:22:2F:F6"          # arbitrary; a NON-hardcoded MAC for the test only
MAGIC = 200

FAIL = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def parse_client_constants():
    """{NAME: int} for every `NAME = 0x.. / NAME = <int>` line in the client."""
    src = open(CLIENT, encoding="utf-8").read()
    out = {}
    for name in EXPECTED:
        m = re.search(r"^%s\s*=\s*(0x[0-9A-Fa-f]+|\d+)" % re.escape(name), src, re.M)
        if m:
            out[name] = int(m.group(1), 0)
    return out


def main():
    print("== source-level constants parsed out of tools/ring_introduce.py ==")
    parsed = parse_client_constants()
    print("parsed:", {k: (hex(v) if v > 9 else v) for k, v in sorted(parsed.items())})

    for name, want in EXPECTED.items():
        got = parsed.get(name)
        check(got == want, "%s == %s (MentraOS G2.kt) -- got %r"
              % (name, hex(want), got))

    # the client's own view of the module must agree with what it imports
    check(ri.SID_DEVICE_SETTINGS == 0x80, "client SID_DEVICE_SETTINGS is 0x80")
    check(ri.CMD_RING_CONNECT_INFO == g2.RING_CONNECT_INFO,
          "client cmd id matches g2proto.RING_CONNECT_INFO")

    print()
    print("== exact bytes for RING_CONNECT_INFO (connect=true) ==")
    mac = g2.parse_mac(MAC)
    payload = ri.build_ring_connect_info(MAGIC, True, mac)
    print("payload:", payload.hex(" "))
    # DevCfgDataPackage: 08 06 (cmd=6) | 10 c8 01 (magic=200) | 2a 0a (field5,len10)
    #   RingInfo: 08 01 (connect=true) 12 06 <mac>
    expect = bytes.fromhex("08 06 10 c8 01 2a 0a 08 01 12 06 80 e1 26 22 2f f6")
    check(payload == expect, "payload == hand-quoted MentraOS layout")

    # field numbers/types, decoded back
    dec = g2.pb_decode(payload)
    check(dec is not None and dec.get(1) == [6], "field 1 (commandId) == 6")
    check(dec.get(2) == [MAGIC], "field 2 (magicRandom) == %d" % MAGIC)
    ring = g2.pb_decode(dec[5][0])
    check(ring.get(1) == [1], "ringInfo field 1 (connectRing) == 1")
    check(ring.get(2)[0] == bytes.fromhex("80e126222ff6"),
          "ringInfo field 2 (ringMac) == the 6 MAC bytes in display order")
    check(3 not in ring, "ringInfo field 3 (ringName) omitted when empty")

    print()
    print("== connect=false writes the bool explicitly, like MentraOS writeBoolField ==")
    off = ri.build_ring_connect_info(5, False, mac)
    print("payload:", off.hex(" "))
    check(off == bytes.fromhex("08 06 10 05 2a 0a 08 00 12 06 80 e1 26 22 2f f6"),
          "connect=false -> 08 00 (field present, value 0)")

    print()
    print("== framing: sid 0x80, status byte 0x00, header aa 21 ==")
    frs = ri.framed(payload, seq=0x40)
    check(len(frs) == 1, "single frame for a 16-byte payload")
    f = frs[0]
    print("frame:", f.hex(" "))
    check(f[:2] == b"\xaa\x21", "header bytes are aa 21 (HEADER_BYTE + (2<<4)|1)")
    check(f[6] == 0x80, "frame byte 6 (serviceId) == 0x80 DEVICE_SETTINGS")
    check(f[7] == 0x00, "frame byte 7 (status) == 0x00 (reserveFlag=false)")
    check(f.endswith(bytes([g2.crc16_ccitt_false(payload) & 0xFF,
                            (g2.crc16_ccitt_false(payload) >> 8) & 0xFF])),
          "payload CRC16 appended little-endian")

    print()
    print("== no hardcoded MAC in the client ==")
    src = open(CLIENT, encoding="utf-8").read()
    sniff = re.findall(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b", src)
    # allowed only inside the "--ring" help example text
    help_examples = {"AA:BB:CC:DD:EE:FF"}
    leaked = [s for s in sniff if s not in help_examples]
    check(not leaked, "no MAC literal outside the help example (found %r)" % leaked)

    print()
    print("NOTE: source-level only -- it cannot prove the glasses do anything with it.")
    print("FAILURES:", FAIL if FAIL else "none")
    return 1 if FAIL else 0


sys.exit(main())
