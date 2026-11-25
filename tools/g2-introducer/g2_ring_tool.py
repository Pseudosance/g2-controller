#!/usr/bin/env python3
"""g2_ring_tool.py - standalone helper for a DIY ring with Even Realities G2 glasses.
Needs Python 3.9+ and bleak (pip install bleak). Turn your phone's Bluetooth off first.

  python g2_ring_tool.py scan
  python g2_ring_tool.py introduce --ring <RING ADDRESS> --glasses <RING-OWNER ARM> [--off]
  python g2_ring_tool.py set-ring-hold --glasses <RIGHT ARM> --app 2
"""
import argparse, asyncio, re, struct, sys, time
from bleak import BleakClient, BleakScanner

UUID_WRITE = "00002760-08c2-11e1-9073-0e8ac72e5401"
UUID_NOTIFY = "00002760-08c2-11e1-9073-0e8ac72e5402"
SID_SETTINGS = 0x09   # G2SettingPackage
SID_DEVICE = 0x80     # device settings / handshake

def crc16_ccitt_false(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def frames(payload, seq, sid, flag, chunk=200):
    # aa 21 | seq | piece_len | total | index (1-based) | sid | flag | piece ; CRC appended LE
    body = payload + struct.pack("<H", crc16_ccitt_false(payload))
    total = max(1, -(-len(body) // chunk))
    return [b"\xaa\x21" + bytes([seq & 0xFF, len(body[i*chunk:(i+1)*chunk]), total, i + 1, sid, flag])
            + body[i*chunk:(i+1)*chunk] for i in range(total)]

def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n: return bytes(out)

def pb(fields):
    # {field: int | bytes | dict | list} -> protobuf
    out = bytearray()
    for num, val in fields.items():
        for v in (val if isinstance(val, list) else [val]):
            if isinstance(v, int):
                out += varint(num << 3) + varint(int(v))
            else:
                v = pb(v) if isinstance(v, dict) else bytes(v)
                out += varint((num << 3) | 2) + varint(len(v)) + v
    return bytes(out)

def pb_decode(buf):
    out, i = {}, 0
    def rv():
        nonlocal i
        v = s = 0
        while True:
            b = buf[i]; i += 1; v |= (b & 0x7F) << s; s += 7
            if not b & 0x80: return v
    while i < len(buf):
        key = rv(); num, wt = key >> 3, key & 7
        if wt == 0: v = rv()
        elif wt == 2: n = rv(); v = bytes(buf[i:i+n]); i += n
        elif wt == 5: v = bytes(buf[i:i+4]); i += 4
        elif wt == 1: v = bytes(buf[i:i+8]); i += 8
        else: break
        out.setdefault(num, []).append(v)
    return out

class Link:
    def __init__(self, client):
        self.c, self.seq, self.waiters, self.parts = client, 0, {}, {}
        self.on_status = None
    def on_notify(self, _, data):
        raw = bytes(data)
        if len(raw) < 8 or raw[0] != 0xAA: return
        seq, ln, total, index, sid = raw[2], raw[3], raw[4], raw[5], raw[6]
        piece = raw[8:8 + ln]
        if total > 1:
            p = self.parts.setdefault(seq, {}); p[index] = piece
            if len(p) < total: return
            piece = b"".join(p[i + 1] for i in range(total)); del self.parts[seq]
        payload = piece[:-2]
        try: msg = pb_decode(payload)
        except IndexError: return
        if self.on_status and sid == SID_DEVICE and msg.get(5, [b""])[0]: self.on_status(msg[5][0])
        fut = self.waiters.pop((sid, msg.get(2, [None])[0]), None)
        if fut and not fut.done(): fut.set_result(payload)
    async def send(self, sid, payload, flag, magic=None, timeout=3.0):
        fut = None
        if magic is not None:
            fut = asyncio.get_running_loop().create_future(); self.waiters[(sid, magic)] = fut
        for f in frames(payload, self.seq, sid, flag):
            await self.c.write_gatt_char(UUID_WRITE, f, response=False)
        self.seq = (self.seq + 1) & 0xFF
        if fut is None: return None
        try: return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError: self.waiters.pop((sid, magic), None); return None
    async def handshake(self):
        # The Even app's start-up exchange. Not every message is acknowledged; that's normal.
        now = int(time.time())
        clock = lambda m: pb({1: 128, 2: m, 128: {1: now, 2: 0xFFFFFFFFFFFFFFE8}})
        for flag, payload, magic in [
            (0x00, bytes.fromhex("0804100c1a0408011004"), 0x0C), (0x20, bytes.fromhex("0805100e22020802"), 0x0E),
            (0x20, clock(0x0F), 0x0F), (0x00, bytes.fromhex("080410101a0408011004"), 0x10),
            (0x00, bytes.fromhex("080410111a0408011004"), 0x11), (0x20, bytes.fromhex("0805101222020801"), 0x12),
            (0x20, clock(0x13), 0x13)]:
            await self.send(SID_DEVICE, payload, flag, magic=magic, timeout=1.0)

async def connect(address):
    client = BleakClient(address, timeout=20); await client.connect()
    link = Link(client); await client.start_notify(UUID_NOTIFY, link.on_notify)
    await link.handshake()
    return client, link

async def cmd_scan(_):
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    for addr, (dev, adv) in sorted(found.items(), key=lambda x: -x[1][1].rssi):
        name = adv.local_name or dev.name or ""
        if re.search(r"even|g2|r1", name, re.I): print(f"  {addr}  {name}  rssi {adv.rssi}")

async def cmd_introduce(a):
    mac = bytes(int(p, 16) for p in re.split("[:-]", a.ring))
    if len(mac) != 6: sys.exit("--ring must look like 80:E1:26:AA:BB:CC")
    client, link = await connect(a.glasses)
    try:
        seen = asyncio.get_running_loop().create_future()
        def status(ring):
            info = pb_decode(ring)
            print("  ring status:", "CONNECTED" if info.get(1) == [1] and 4 not in info else f"not connected {info}")
            if info.get(1) == [1] and 4 not in info and not seen.done(): seen.set_result(True)
        link.on_status = status
        # DevCfg{1: 6 RING_CONNECT_INFO, 2: magic, 5: RingInfo{1: connect, 2: mac REVERSED}}
        msg = pb({1: 6, 2: 200, 5: {1: 0 if a.off else 1, 2: mac[::-1]}})
        reply = await link.send(SID_DEVICE, msg, 0x00, magic=200, timeout=4)
        print("ack:", reply.hex(" ") if reply else "none (is this the ring-owner arm?)")
        try: await asyncio.wait_for(seen, 15)
        except asyncio.TimeoutError: print("no CONNECTED yet (is the ring advertising?)")
    finally:
        await client.disconnect()

async def cmd_set_ring_hold(a):
    client, link = await connect(a.glasses)
    try:
        # G2SettingPackage{1: 1, 2: magic, 3: {13: {2: {1: app, 2: displayOffEnable}}}}
        msg = pb({1: 1, 2: 201, 3: {13: {2: {1: a.app, 2: 1}}}})
        reply = await link.send(SID_SETTINGS, msg, 0x20, magic=201, timeout=4)
        print("ack:", reply.hex(" ") if reply else "none (settings go to the RIGHT arm)")
    finally:
        await client.disconnect()

ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="cmd", required=True)
sub.add_parser("scan")
p = sub.add_parser("introduce"); p.add_argument("--ring", required=True)
p.add_argument("--glasses", required=True); p.add_argument("--off", action="store_true")
p = sub.add_parser("set-ring-hold"); p.add_argument("--glasses", required=True)
p.add_argument("--app", type=int, default=2)
a = ap.parse_args()
asyncio.run({"scan": cmd_scan, "introduce": cmd_introduce, "set-ring-hold": cmd_set_ring_hold}[a.cmd](a))
