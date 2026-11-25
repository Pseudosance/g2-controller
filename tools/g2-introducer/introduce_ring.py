"""Tell the G2 glasses to dial a ring by MAC -- the step the Even app does for a real R1.

  python introduce_ring.py --ring AA:BB:CC:DD:EE:FF          # connect (dial the Flipper)
  python introduce_ring.py --ring AA:BB:CC:DD:EE:FF --off    # tell glasses to drop it

--ring is the Flipper's address BYTE-REVERSED (Flipper 80:E1:26:22:2F:F6 -> F6:2F:22:26:E1:80).
--glasses must be the ring-owner arm (picked by dominant hand; the LEFT arm here).
The other arm ignores the command.
The glasses accept one phone at a time: force-stop the Even app first, and turn the
phone's Bluetooth off. Run on Windows (needs a real BLE radio; WSL has none).

Source of the command: MentraOS G2.kt connectController() -> RING_CONNECT_INFO (cmd 6)
on DEVICE_SETTINGS (sid 0x80). Verified on G2 fw 2.4.1.4: reply RingInfo{1:1} = connected.
"""
import argparse, asyncio, logging, os, re, sys, time
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_here, "..", "..", "relay"))
import g2proto as g2
from g2_relay import EUSLink, BleakClient, BleakScanner

log = logging.getLogger("introduce")


async def pick_glasses(wanted):
    if wanted:
        return wanted, wanted
    devs = await BleakScanner.discover(timeout=8.0)
    arms = [(d.address, d.name) for d in devs if d.name and re.search(r"even|g2", d.name, re.I)]
    for a, n in arms:
        print(f"  found: {n}  {a}")
    right = [x for x in arms if re.search(r"(^|[_ -])R([_ -]|$)", x[1])]
    if right:
        return right[0]
    if len(arms) == 1:
        return arms[0]
    sys.exit("Couldn't tell which arm is the right one; rerun with --glasses <address>.")


async def main(a):
    mac = g2.parse_mac(a.ring)
    addr, name = await pick_glasses(a.glasses)
    print(f"connecting to {name} ({addr}) ...")
    link = EUSLink()
    async with BleakClient(addr, timeout=20) as client:
        async def w(data):
            await client.write_gatt_char(g2.UUID_WRITE, data, response=False)
        link._write = w

        def on_notify(_, d):
            print("  rx", bytes(d).hex(" "))
            link.feed(d)
        await client.start_notify(g2.UUID_NOTIFY, on_notify)

        for flag, payload in g2.handshake_payloads(int(time.time() * 1000)):
            m = g2.pb_decode(payload)[2][0]
            try:
                await link._send(g2.SID["handshake"], payload, timeout=1.5, magic=m, flag=flag)
            except Exception as e:
                print(f"  handshake magic={m}: no ack ({e})")

        magic = 200
        msg = g2.msg_ring_connect_info(magic, not a.off, mac)
        print(("connect" if not a.off else "disconnect"), "ring", a.ring, "->", msg.hex(" "))
        try:
            sid, payload = await link._send(g2.SID["handshake"], msg, timeout=4.0, magic=magic, flag=0x00)
            print("  reply:", payload.hex(" "), g2.pb_decode(payload))
        except Exception as e:
            print(f"  no reply to RING_CONNECT_INFO ({e})")
        print(f"listening {a.wait}s for ring-status notifications (watch the Flipper's con/gls flags)...")
        await asyncio.sleep(a.wait)
    print("done; you can reopen the Even app now.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ring", required=True, help="Flipper BLE address, e.g. 80:E1:26:22:2F:F6")
    ap.add_argument("--glasses", help="right-arm G2 address (auto-detect if omitted)")
    ap.add_argument("--off", action="store_true", help="send connect=false instead")
    ap.add_argument("--wait", type=int, default=20)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(ap.parse_args()))
