"""Set what a RING long press does on the G2 (the Even app's gesture setting).

  python set_ring_hold.py --glasses F0:58:C4:E4:EC:9B --format list --app 1
  python set_ring_hold.py --glasses F0:58:C4:E4:EC:9B --format shortcut --app 2

Sid 0x09 G2SettingPackage{1: commandId=1 DeviceReceiveInfo, 2: magic,
3: DeviceReceiveInfoFromAPP}. From the Even app's g2_setting.pb (blutter):
  list     -> field 10 APP_Send_Gesture_Control_List{1: [ {1 screenOn, 2 operationType
              (0 left, 1 right, 2 ring), 3 apptype (0 unable, 1 app_menu)} ]}
  shortcut -> field 13 APP_Send_Shortcut_Setting{2: ring{1: ShortcutApplicationType}}
              (0 none, 1 Even AI, 2 menu, 3 quick note, 4 quicklist, 5 notification,
               6 conversate, 7 translate, 8 teleprompt, 9 navigate, 10 terminal, 11 note)
"""
import argparse, asyncio, os, sys, time
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_here, "..", "..", "relay"))
import g2proto as g2
from g2_relay import EUSLink, BleakClient


def build(fmt, app, magic):
    if fmt == "list":
        items = [{1: s, 2: 2, 3: app} for s in (0, 1)]
        inner = {10: {1: items}}
    else:
        inner = {13: {2: {1: app, 2: 1}}}
    return g2.pb_encode({1: 1, 2: magic, 3: inner})


async def main(a):
    link = EUSLink()
    async with BleakClient(a.glasses, timeout=20) as client:
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
        magic = 201
        msg = build(a.format, a.app, magic)
        print(f"set ring hold ({a.format}, app={a.app}) ->", msg.hex(" "))
        try:
            sid, payload = await link._send(g2.SID["settings"], msg, timeout=4.0, magic=magic)
            print("  reply:", payload.hex(" "), g2.pb_decode(payload))
        except Exception as e:
            print(f"  no reply ({e})")
        await asyncio.sleep(a.wait)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--glasses", required=True)
    ap.add_argument("--format", choices=["list", "shortcut"], default="list")
    ap.add_argument("--app", type=int, default=1)
    ap.add_argument("--wait", type=int, default=3)
    asyncio.run(main(ap.parse_args()))
