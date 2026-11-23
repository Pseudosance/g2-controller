"""Even Realities G2 "EUS" protocol — faithful Python port of vendor/even-g2-js g2.js.

Byte-for-byte verified against the vendored g2.js (tests/g2_golden.py cross-checks
crc16 + framing with Node and field-decodes payloads with g2.js `decode()`).

Frame: aa 21 | seq u8 | piece_len u8 | total u8 | index u8 (1-based) | sid u8 | flag u8 | piece
CRC-16/CCITT-FALSE over the whole payload, appended payload-internal, little-endian.
Strings are UTF-8. Ack replies echo the request magic in protobuf field 2.
"""
from __future__ import annotations
import struct

UUID_SUFFIX = "00002760-08c2-11e1-9073-0e8ac72e%s"
UUID_WRITE = UUID_SUFFIX % "5401"
UUID_NOTIFY = UUID_SUFFIX % "5402"
SERVICES = [UUID_SUFFIX % s for s in ("0000", "5450", "6450", "7450")]

SID = {"app": 0x01, "settings": 0x09, "state_change": 0x0D, "handshake": 0x80, "evenhub": 0xE0}
FLAG_REQUEST = 0x20
FLAG_NOTIFY = 0x01
FLAG_RESPONSE = 0x12  # observed in prelude ack byte1 (aa 12 ...)

MAGIC = b"\xaa\x21"

CMD = {"create": 0, "device_event": 2, "text_upgrade": 5, "rebuild": 7,
       "heartbeat": 12, "private_system_event": 14}

GESTURES = ["tap", "swipe-back", "swipe-forward", "double-tap", "foreground-enter",
            "foreground-exit", "abnormal-exit", "system-exit", "motion"]
SOURCES = ["unknown", "right temple", "ring", "left temple"]


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def frames(payload: bytes, *, seq: int, sid: int, flag: int = FLAG_REQUEST,
           chunk: int = 200) -> list[bytes]:
    """Split payload into aa21 frames; crc appended to payload, every piece shares seq."""
    crc = crc16_ccitt_false(payload)
    body = payload + struct.pack("<H", crc)
    total = max(1, -(-len(body) // chunk))
    out = []
    for i in range(total):
        piece = body[i * chunk:min(len(body), (i + 1) * chunk)]
        out.append(MAGIC + bytes([seq & 0xFF, len(piece), total, i + 1, sid, flag]) + piece)
    return out


class Reassembler:
    """Feed raw notification chunks; yields (sid, flag, payload) on completion."""

    def __init__(self):
        self._pending: dict[int, dict] = {}

    def push(self, raw: bytes):
        if len(raw) < 8 or raw[0] != 0xAA or raw[1] not in (0x12, 0x21):
            return None
        _, _, seq, length, total, index, sid, flag = raw[:8]
        chunk = raw[8:8 + length]
        if total <= 1:
            body = chunk
        else:
            if index == 1 or seq not in self._pending:
                self._pending[seq] = {"total": total, "parts": {}}
            p = self._pending[seq]
            p["parts"][index] = chunk
            if len(p["parts"]) < p["total"]:
                return None
            del self._pending[seq]
            body = b"".join(p["parts"].get(i + 1, b"") for i in range(p["total"]))
        return sid, flag, body[:max(0, len(body) - 2)]  # strip payload crc (not verified, mirrors g2.js)


# ---------------------------------------------------------------- protobuf-lite

def pb_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


class PB:
    """Minimal proto3 writer mirroring g2.js PB: zero scalars omitted, submessages always written."""

    def __init__(self):
        self.b = bytearray()

    def key(self, field, wire):
        self.b += pb_varint((field << 3) | wire)

    def uint(self, field, v):
        if v:
            self.key(field, 0)
            self.b += pb_varint(v)
        return self

    def raw(self, field, data: bytes):
        self.key(field, 2)
        self.b += pb_varint(len(data))
        self.b += data
        return self

    def string(self, field, s: str):
        if s:
            self.raw(field, s.encode("utf-8"))
        return self

    def message(self, field, fn):
        sub = PB()
        fn(sub)
        return self.raw(field, bytes(sub.b))


def pb_decode(buf) :
    """field -> list of values (int for varint, bytes for wire 2). None on malformed."""
    if not isinstance(buf, bytes):
        buf = bytes(buf)
    fields: dict[int, list] = {}
    i = 0

    def varint():
        nonlocal i
        v = 0
        s = 0
        while i < len(buf):
            c = buf[i]
            i += 1
            v |= (c & 0x7F) << s
            if not (c & 0x80):
                return v
            s += 7
        raise ValueError("truncated varint")

    try:
        while i < len(buf):
            k = varint()
            num, wire = k >> 3, k & 7
            if wire == 0:
                fields.setdefault(num, []).append(varint())
            elif wire == 2:
                n = varint()
                fields.setdefault(num, []).append(buf[i:i + n])
                i += n
            elif wire == 5:
                fields.setdefault(num, []).append(struct.unpack_from("<f", buf, i)[0])
                i += 4
            elif wire == 1:
                fields.setdefault(num, []).append(struct.unpack_from("<d", buf, i)[0])
                i += 8
            else:
                return None
    except (ValueError, struct.error, IndexError):
        return None
    return fields


class Decoded:
    """Typed view over pb_decode output, mirroring g2.js decode() helpers."""

    def __init__(self, fields):
        self.fields = fields or {}

    def has(self, f):
        return f in self.fields

    def uint(self, f):
        v = self.fields.get(f)
        return v[-1] if v and isinstance(v[-1], int) else 0

    def num(self, f):
        v = self.fields.get(f)
        return v[-1] if v and isinstance(v[-1], (int, float)) else 0

    def string(self, f):
        v = self.fields.get(f)
        return v[-1].decode("utf-8", "replace") if v and isinstance(v[-1], bytes) else ""

    def message(self, f):
        v = self.fields.get(f)
        if not v or not isinstance(v[-1], bytes):
            return None
        return Decoded(pb_decode(v[-1]))


# ---------------------------------------------------------------- message builders

def _hub(cmd: int, magic: int, fn=None) -> bytes:
    p = PB()
    p.uint(1, cmd).uint(2, magic)
    if fn:
        fn(p)
    return bytes(p.b)


def _text_object(m: PB, *, name, content, tid=1, x=0, y=0, width=576, height=288, capture=True):
    m.uint(1, x).uint(2, y).uint(3, width).uint(4, height).uint(9, tid)
    m.string(10, name).uint(11, 1 if capture else 0).string(12, content)


def _list_object(m: PB, *, name, items, tid=1, x=0, y=0, width=576, height=288):
    m.uint(1, x).uint(2, y).uint(3, width).uint(4, height).uint(9, tid).string(10, name)
    def inner(it: PB):
        it.uint(1, len(items)).uint(3, 1)
        for s in items:
            it.string(4, s)
    m.message(11, inner)
    m.uint(12, 1)


def msg_create(magic, name="g2web", items=("G2 web",)) -> bytes:
    return _hub(CMD["create"], magic, lambda w: w.message(3, lambda m: (
        m.uint(1, 1),
        m.message(2, lambda o: _list_object(o, name=name, items=list(items))),
        m.uint(5, 10000))))


def msg_rebuild_text(magic, *, name, content) -> bytes:
    return _hub(CMD["rebuild"], magic, lambda w: w.message(7, lambda m: (
        m.uint(1, 1), m.message(3, lambda o: _text_object(o, name=name, content=content)))))


def msg_rebuild_list(magic, *, name, items) -> bytes:
    return _hub(CMD["rebuild"], magic, lambda w: w.message(7, lambda m: (
        m.uint(1, 1), m.message(2, lambda o: _list_object(o, name=name, items=items)))))


def msg_text_upgrade(magic, name, content) -> bytes:
    return _hub(CMD["text_upgrade"], magic, lambda w: w.message(9, lambda m: (
        m.uint(1, 1).string(2, name).uint(4, len(content.encode("utf-8"))).string(5, content))))


def msg_heartbeat(magic) -> bytes:
    return _hub(CMD["heartbeat"], magic, lambda w: w.message(14, lambda m: None))


def msg_status_query(magic) -> bytes:
    return PB().uint(1, 2).uint(2, magic).message(4, lambda m: m.uint(1, 1)).b


def handshake_payloads(now_ms: int) -> list[tuple[int, bytes]]:
    """The sid=0x80 start-up exchange (7 messages). now_ms fixed for golden tests."""
    def clock(magic):
        return PB().uint(1, 128).uint(2, magic).message(128, lambda m: (
            m.uint(1, now_ms // 1000).uint(2, 0xFFFFFFFFFFFFFFE8))).b
    return [
        (0x00, bytes([0x08, 0x04, 0x10, 0x0C, 0x1A, 0x04, 0x08, 0x01, 0x10, 0x04])),
        (0x20, bytes([0x08, 0x05, 0x10, 0x0E, 0x22, 0x02, 0x08, 0x02])),
        (0x20, clock(0x0F)),
        (0x00, bytes([0x08, 0x04, 0x10, 0x10, 0x1A, 0x04, 0x08, 0x01, 0x10, 0x04])),
        (0x00, bytes([0x08, 0x04, 0x10, 0x11, 0x1A, 0x04, 0x08, 0x01, 0x10, 0x04])),
        (0x20, bytes([0x08, 0x05, 0x10, 0x12, 0x22, 0x02, 0x08, 0x01])),
        (0x20, clock(0x13)),
    ]


# PRELUDE: sid=0x01 app-launch frame, verbatim capture (already framed; ack echoes magic 156).
PRELUDE = bytes([
    0xAA, 0x21, 0x92, 0x13, 0x01, 0x01, 0x01, 0x20, 0x08, 0x02, 0x10, 0x9C, 0x01, 0x22, 0x0A, 0x1A,
    0x08, 0x12, 0x06, 0x12, 0x04, 0x08, 0x00, 0x10, 0x00, 0xA1, 0x42,
])


def decode_event(payload: bytes):
    """Async EvenHub event -> {type, source?, container?, index?, item?} or None."""
    pb = Decoded(pb_decode(payload))
    if pb is None or pb.uint(1) != CMD["device_event"]:
        return None
    dev = pb.message(13)
    if dev is None:
        return None
    lst, txt, sys = dev.message(1), dev.message(2), dev.message(3)
    if lst is not None:
        return {"type": GESTURES[lst.uint(5)] if lst.uint(5) < len(GESTURES) else None,
                "container": lst.string(2), "index": lst.uint(4), "item": lst.string(3)}
    if txt is not None:
        g = txt.uint(3)
        return {"type": GESTURES[g] if g < len(GESTURES) else None, "container": txt.string(2)}
    if sys is not None:
        g = sys.uint(1)
        ev = {"type": GESTURES[g] if g < len(GESTURES) else None,
              "source": SOURCES[sys.uint(2)] if sys.uint(2) < len(SOURCES) else "unknown"}
        imu = sys.message(3)
        if imu is not None:
            ev["motion"] = {"x": imu.num(1), "y": imu.num(2), "z": imu.num(3)}
        return ev
    return {"type": "tap", "source": "unknown"}


def parse_status(payload: bytes):
    """Settings status reply -> {battery, charging, firmware}."""
    r = Decoded(pb_decode(payload)).message(4)
    if r is None:
        return None
    return {"battery": r.uint(12), "charging": bool(r.uint(13)), "firmware": r.string(6)}


def pb_encode(fields):
    """Generic protobuf encoder: {field: bool|int|str|bytes|dict} (list repeats).

    Validated against g2.js's decoder via tests/golden_bridge.mjs op=decode_fields.
    """
    out = bytearray()
    for num in sorted(fields):
        for val in (fields[num] if isinstance(fields[num], list) else [fields[num]]):
            if isinstance(val, bool):
                out += pb_varint((num << 3) | 0) + pb_varint(1 if val else 0)
            elif isinstance(val, int):
                out += pb_varint((num << 3) | 0) + pb_varint(val)
            else:
                if isinstance(val, str):
                    val = val.encode("utf-8")
                elif isinstance(val, dict):
                    val = pb_encode(val)
                out += pb_varint((num << 3) | 2) + pb_varint(len(val)) + val
    return bytes(out)


def frame(payload, seq, sid, flag, mtu=255):
    return frames(payload, seq=seq, sid=sid, flag=flag, chunk=mtu)[0]
