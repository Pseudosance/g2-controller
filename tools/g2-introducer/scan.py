import asyncio
from bleak import BleakScanner
async def m():
    d = await BleakScanner.discover(timeout=10, return_adv=True)
    for a,(dev,adv) in d.items():
        if adv.local_name and ("EVEN" in adv.local_name.upper()): print(a, adv.local_name, adv.rssi)
asyncio.run(m())
