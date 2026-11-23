"""Golden tests: relay/g2proto.py must match vendor/even-g2-js/g2.js byte-for-byte.

Runs Node (tests/golden_bridge.mjs) to compute reference crc16/frames and to decode
Python-built payloads with g2.js's own decoder. Requires `node` on PATH.
"""
import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "relay"))

import g2proto as g2  # noqa: E402

BRIDGE = os.path.join(HERE, "golden_bridge.mjs")


def node(req):
    p = subprocess.run(["node", BRIDGE], input=json.dumps(req).encode(),
                       capture_output=True, timeout=60)
    assert p.returncode == 0, p.stderr.decode()
    return json.loads(p.stdout)


def test_crc16():
    rnd = random.Random(42)
    hexes = [bytes(rnd.randrange(256) for _ in range(n)).hex() for n in (0, 1, 7, 64, 200, 991)]
    expected = node({"op": "crc", "hexes": hexes})
    got = [g2.crc16_ccitt_false(bytes.fromhex(h)) for h in hexes]
    assert got == expected, (got, expected)


def test_frames_match_js():
    rnd = random.Random(7)
    for n, chunk in ((5, 200), (20, 8), (199, 200), (202, 200), (500, 160), (1000, 40)):
        payload = bytes(rnd.randrange(256) for _ in range(n))
        expected = node({"op": "frames", "hex": payload.hex(), "seq": 0x42,
                         "sid": 0xE0, "flag": 0x20, "chunk": chunk})
        got = [f.hex() for f in g2.frames(payload, seq=0x42, sid=0xE0, flag=0x20, chunk=chunk)]
        assert got == expected, (n, chunk, got, expected)


def test_message_structures_match_js_decoder():
    payloads = {
        "create": g2.msg_create(101),
        "rebuild_text": g2.msg_rebuild_text(102, name="text-a", content="héllo 世界"),
        "rebuild_list": g2.msg_rebuild_list(103, name="list-b", items=["one", "two"]),
        "text_upgrade": g2.msg_text_upgrade(104, "text-a", "body"),
        "heartbeat": g2.msg_heartbeat(105),
        "status_query": bytes(g2.msg_status_query(106)),
    }
    js = node({"op": "decode", "payloads": {k: v.hex() for k, v in payloads.items()}})
    for k, v in payloads.items():
        mine = {str(f): [x.hex() if isinstance(x, bytes) else x for x in vs]
                for f, vs in g2.pb_decode(v).items()}
        assert js[k] is not None, k
        assert js[k]["fields"] == mine, (k, js[k]["fields"], mine)
    # deeper: text_upgrade must carry name in f2 and utf-8 length in f4, content in f5
    tu = node({"op": "decode_fields", "hex": payloads["text_upgrade"].hex()})
    body = "body".encode()
    assert tu["9"], payloads["text_upgrade"].hex()
    inner = node({"op": "decode_fields", "hex": tu["9"][0]})
    assert bytes.fromhex(inner["2"][0]) == b"text-a"
    assert inner["4"][0] == len(body)
    assert bytes.fromhex(inner["5"][0]) == body


def test_prelude_and_handshake_shapes():
    # PRELUDE is sent verbatim (never re-decoded by us; g2.js only "decodes" it thanks
    # to a Node Buffer-pool accident, so we pin the exact capture bytes instead).
    assert bytes(g2.PRELUDE).hex() == (
        "aa219213010101200802109c01220a1a081206120408001000a1" "42")
    fixed_now = 1_700_000_000_000
    hs = g2.handshake_payloads(fixed_now)
    assert len(hs) == 7
    assert [f for f, _ in hs] == [0x00, 0x20, 0x20, 0x00, 0x00, 0x20, 0x20]
    js2 = node({"op": "decode", "payloads": {str(i): p.hex() for i, (_, p) in enumerate(hs)}})
    for i, (_, p) in enumerate(hs):
        d = g2.Decoded(g2.pb_decode(p))
        assert js2[str(i)] is not None and d.fields, i
        assert js2[str(i)]["uint1"] == d.uint(1), i
        assert js2[str(i)]["uint2"] == d.uint(2), i
    # clock frames (indices 2, 6) carry magic 0x0f / 0x13 in field 2
    assert g2.Decoded(g2.pb_decode(hs[2][1])).uint(2) == 0x0F
    assert g2.Decoded(g2.pb_decode(hs[6][1])).uint(2) == 0x13


def test_reassembler_roundtrip_and_multiframe():
    payload = bytes(range(256)) * 2
    fr = g2.frames(payload, seq=0x55, sid=0xE0, flag=0x20, chunk=64)
    r = g2.Reassembler()
    out = None
    for i, f in enumerate(fr):
        out = r.push(f)
        if i < len(fr) - 1:
            assert out is None
    assert out is not None and out[2] == payload


def test_decode_event_shapes():
    # device_event(2) -> field 13 -> sys message(3){gesture=3(double-tap), source=2(ring)}
    payload = g2.PB().uint(1, 2).message(13, lambda m: m.message(
        3, lambda s: s.uint(1, 3).uint(2, 2))).b
    ev = g2.decode_event(payload)
    assert ev == {"type": "double-tap", "source": "ring"}, ev
    # list event: f13 -> f1 {container=f2, index=f4, gesture=f5}
    payload2 = g2.PB().uint(1, 2).message(13, lambda m: m.message(
        1, lambda s: s.string(2, "g2web").uint(4, 2).uint(5, 2))).b
    ev2 = g2.decode_event(payload2)
    assert ev2["type"] == "swipe-forward" and ev2["item"] == "" and ev2["index"] == 2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("all golden tests passed")
