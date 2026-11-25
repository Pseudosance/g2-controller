# Emulating the Even R1 ring on a Flipper Zero (G2 glasses)

This records everything learned getting a stock-firmware Flipper Zero to act as an
Even Realities R1 ring for the G2 glasses, so nobody has to rediscover it.

**Status (verified on hardware, 2026-11-24):** G2 firmware **2.4.1.4**, stock
Flipper firmware (API 88.2). The glasses connect to the Flipper themselves, and
scroll up/down, select (tap), back (double tap) and menu all work, **with the
phone and the Even app connected**. The Flipper reconnects on its own after an app
restart or when the glasses wake. The PC is needed **once**, to tell the glasses
the Flipper's address.

Code: `flipper/apps/g2_ring/g2_ring.c`. PC helpers: `tools/g2-introducer/`.

---

## 1. The architecture that works

    Even G2 glasses (BLE central) --connects to--> Flipper "EVEN R1_xxxxxx" (BLE peripheral)

- The R1 is a **peripheral**, and the glasses are the central that dials it. A stock
  `.fap` can be a peripheral with a custom GATT service, so no PC relay is needed.
- **Don't pair the "ring" through the Even app.** That path fails with error 920 and
  is still unsolved (see §8). The glasses accept a ring the app has never seen.
  Instead, a PC sends the glasses one command telling them the ring's MAC (§5).
  After that the glasses remember it and reconnect by themselves.
  (Credit: Brodie Friesen, who got an ESP32 R1 spoof working this way.)

## 2. Quick start

1. Build and install: `cd flipper/apps/g2_ring && ufbt`, then copy `dist/g2_ring.fap`
   to `/ext/apps/` (qFlipper, or `tools/g2-introducer/flipper_push.py` over USB).
2. Open **G2 Ring** on the Flipper. It advertises as `EVEN R1_` + the last three bytes
   of its MAC.
3. One-time introduction, from a PC with a real BLE radio (Windows plus bleak; WSL has
   no radio). **Turn the phone's Bluetooth off first**: the glasses stop advertising
   while the phone holds them.

       python tools/g2-introducer/introduce_ring.py --ring <FLIPPER MAC REVERSED> --glasses <OWNER ARM MAC>

   Both arguments are explained in §5. Run it within 30 s of opening the app (§3). A
   reply of `RingInfo{1:1}` means connected.
4. Turn the phone back on. From now on, just open the app; the glasses find it.

Button map (Flipper held sideways, RIGHT key on top):

| Key | Report type | G2 action |
|---|---|---|
| RIGHT | `0x04` | scroll up |
| LEFT | `0x05` | scroll down |
| UP | `0x02` double tap | back |
| DOWN | `0x01` tap | select |
| OK (tap or hold) | `0x09` tap-and-hold, then `0x08` release | menu |
| BACK | none | exit the app |

## 3. Advertising

- **Name:** `EVEN R1_XXYYZZ`. `adv_name[0]` must be the AD type byte
  `AD_TYPE_COMPLETE_LOCAL_NAME` (0x09), then the text, or nothing is visible.
- **Size:** the 31-byte advertisement can't fit the name plus the 128-bit service UUID.
  Advertise a 16-bit UUID (`0x180F`) instead. The glasses don't filter on the UUID:
  their scan matches the **target address only**.
- **Scan response:** manufacturer data = `45 52` + the MAC in display order. The company
  ID is a guess and isn't required.
- **Pairing:** `GapPairingNone` (Just Works) with bonding on. A real R1 has no screen.
- **Fast advertising must be kept up.** The Flipper GAP drops to low-power advertising
  30 s after it starts, and the glasses' reconnect scan misses it. The app calls
  `furi_hal_bt_stop_advertising()` then `furi_hal_bt_start_advertising()` every 25 s
  **while nothing is connected**. Never do this while connected: stopping advertising
  terminates the link.

## 4. GATT layout (and the cached-handle trap)

Vendor service `bae80001-4f05-4503-8e65-3af1f7329d1f`:

| Char | Role |
|---|---|
| `bae80010` | glasses → ring (write) |
| `bae80011` | ring → glasses (notify). All replies and gesture reports go here. |
| `bae80012` / `bae80013` | phone ↔ ring "model" channel. The glasses don't use it. |

The glasses discover `bae80010`, `bae80011` and `bae80011`'s CCCD once, then **cache the
handles**. They write the CCCD enable straight to **handle 19**. The app therefore
creates a 2-handle placeholder characteristic first, so the layout lands where a real
ring's does:

    pad 13/14, bae80010 decl 15 / value 16, bae80011 decl 17 / value 18, CCCD 19

Flipper ST-stack facts behind this:
- A characteristic's `.handle` is its **declaration** handle. The value is `handle+1` and
  the CCCD is `handle+2`.
- Never declare an explicit CCCD descriptor; the stack adds one.

## 5. Introducing the ring to the glasses (RING_CONNECT_INFO)

Sent over the glasses' EUS phone channel (`aa 21` framing), after the normal `sid 0x80`
handshake, on **sid 0x80 (DEVICE_SETTINGS), flag 0x00**:

    DevCfg { 1: 6 (RING_CONNECT_INFO), 2: magic, 5: RingInfo { 1: connect (bool), 2: mac[6] } }

Two non-obvious requirements:
- **The MAC goes in byte-reversed** (little-endian). For a Flipper at `80:E1:26:22:2F:F6`,
  send `F6:2F:22:26:E1:80`.
- **Send it to the ring-owner arm**, which is chosen by the dominant-hand setting (the
  **left** arm on the test glasses). The other arm ignores it.

Replies from the owner arm: `RingInfo{1:1}` = connected; `RingInfo{1:1, 4:8}` = not
connected / disconnected. The glasses store the MAC (settings record +12) and retry
it on "scenes" such as waking, wearing and disconnects, provided they can see the ring
advertising (§3).

## 6. The glasses ↔ ring protocol (bae80010 / bae80011)

The glasses dispatch on **byte 2** of every ring packet (`RING_CmdPackageParse`).

**Link bring-up, in order, with the replies the Flipper must send:**

| Glasses writes | Meaning | Ring replies (notify) |
|---|---|---|
| CCCD `01 00` (handle 19) | subscribe | `00 09 8B 00 64 00`: battery 100 %, not charging |
| `00 35 88 00` | pair/auth probe | battery again |
| `00 1A 85 01 00 AA AA AA` | enable touch | `00 09 85 01 01 00 00 00` (touch OK: `[3]=1,[4]=1` = ready), then `00 09 8C 00 01 00` (worn) |
| `00 1A 89 01 xx 00 00 00` | keepalive / status flags, every ~2.5 s | `00 09 94 00 00 00 00 00` heartbeat |
| `00 1A 94 01` | heartbeat, every ~10 s once stable | none needed |

**Without the `0x94` heartbeat reply, the glasses drop the link after three `0x89` writes
(about 7 s).** Other ring → glasses opcodes: `0x8A` interval ack, `0x96` "glasses MAC
invalid" (this unpairs the ring; never send it).

**Touch report** (11 bytes, on bae80011):

    00 09 61 00 | TYPE | P1 | P2 | TICK (u32 little-endian)

- Byte 3 must be `0`.
- `P1`/`P2` are a delta and a speed, used only for swipes.
- Any report except type 8 is **dropped if its tick is less than 100 after the
  previous one**, so space reports apart.

| TYPE | Glasses input event | Effect seen on fw 2.4.1.4 |
|---|---|---|
| `0x00` | LONG_PRESS | runs the **ring long-press shortcut** (§7). Does nothing while the phone is connected. |
| `0x01` | SINGLE_CLICK | select |
| `0x02` | DOUBLE_CLICK | back |
| `0x04` | DOWNROLL (event 5) | scroll up (moves the selection up) |
| `0x05` | UPROLL (event 4) | scroll down |
| `0x08` | RELEASE | ends a hold |
| **`0x09`** | **tap and hold** | **opens the menu directly, independent of settings** |

Type `0x09` is in neither the 2.2.x firmware decompile nor the R1 docs. It was found
by sending candidate types (`03, 06, 07, 09, …`) with the phone connected, after
Brodie mentioned that tap-and-hold had to be "its own event".

## 7. Settings: the ring long-press shortcut (and why not to rely on it)

What a ring **long press** (type `0x00`) does is a glasses setting, written by the Even
app on **sid 0x09** to the **right** arm (flag `0x20`):

    G2SettingPackage { 1: 1 (DeviceReceiveInfo), 2: magic,
      3: DeviceReceiveInfoFromAPP { 13: APP_Send_Shortcut_Setting {
           2: Shortcut_Ring_Setting { 1: application, 2: displayOffEnable } } } }

- `application` (ShortcutApplicationType): 0 none, 1 Even AI, 2 menu, 3 quick note,
  4 quicklist, 5 notification, 6 conversate, 7 translate, 8 teleprompt, 9 navigate,
  10 terminal, 11 note. `Shortcut_Glasses_Setting` is field 1, with target
  (1 left, 2 right) and application.
- The glasses **only acknowledge it when `displayOffEnable` is present**. The reply is
  `DeviceRespondToAPP` echoing the magic.
- Older firmware used field 10 instead (`APP_Send_Gesture_Control_List`, items
  `{screenOn, operationType 0/1/2 = left/right/ring, apptype 0/1 = unable/menu}`).
  It is silently ignored on 2.4.1.4.
- **The Even app resets this to "none" every time it connects**, because it has never
  paired a ring, and it has no ring option in its gesture screen. So long-press
  shortcuts only survive with the phone off. Use type `0x09` for the menu instead.

`tools/g2-introducer/set_ring_hold.py` sends this setting; it's useful with the phone
off.

## 8. Unsolved: pairing through the Even app (error 920)

The app can find `EVEN R1_xxxxxx` and bond, but fails with **error 920**, which means
no accepted ring reply. Known pieces, in case someone continues:

- It talks to the ring on `bae80012`/`bae80013` with "model" frames:
  - fragment `[0]=0`, `[1..4]` = CRC-32C (poly `0x1EDC6F41`, MSB-first, init 0) over the
    model, little-endian;
  - model = `64 module 64 serialLE status cmd subcmd lenLE(12+payload)`, then
    CRC-16/MODBUS with bytes 10–11 zeroed (little-endian), then the payload.
- Response `status = 0x03 | code<<2`. A pairAuth (`0x00`/`0x08`) reply with payload
  `[0x00]` still wasn't accepted.
- The app's flow is in the blutter output under `even_connect/ring1/` and
  `_completePairAuthConnect`, which hasn't been read yet.
- Getting this working would make the app show ring settings and stop wiping the
  shortcut.

## 9. Flipper SDK gotchas

- Use only `+` (approved) symbols from `api_symbols.csv`. All `gap_*` symbols, including
  `gap_get_state`, are off-limits.
  - Custom profile: `bt_profile_start()` with a `FuriHalBleProfileTemplate` and
    `GapConfig`.
  - Service: `ble_gatt_service_add` / `ble_gatt_characteristic_init`.
- `ble_gatt_characteristic_update()` returns **false on success** (it returns the error
  flag).
- A `ble_event_dispatcher` handler that only observes must return `BleEventNotAck`.
  Returning `BleEventAckFlowEnable` swallows GAP events and breaks pairing
  (SMP timeout).
- GATT writes arrive as vendor HCI events:

      0x04 0xFF plen | ecode 0x0C01 (ATTRIBUTE_MODIFIED) | conn(2) | attr_handle(2) | offset(2) | len(2) | data

  They are not ACL packets.
- The bt-service status callback didn't fire for the glasses' connection. Track the
  link from HCI events instead: `0x05` = disconnect; `0x3E` sub `0x01`/`0x0A` = LE connect.
- Do all `ble_gatt_characteristic_update` calls on the GUI thread; post custom events
  from BLE callbacks.
- `ViewDispatcherTypeWindow` hung the UI on an hourglass. Use Fullscreen.
- The Flipper CLI ends a command at `\r`. A trailing `\n` becomes data
  (`storage write_chunk` corrupts files).

## 10. Sources

- **openCFW** (community decompile of G2 and R1 firmware; G2 up to 2.2.x at the time):
  ring parser `display_ring_send_packet_children.c`, input manager
  `service_input_manager.c`, shortcut and gesture settings `setting_dispatch_type10_*`
  and `display_thread_handlers.c`.
- **Even app** (Flutter, Dart 3.11.5) decompiled with **blutter**:
  `even_connect/g2/proto/generated/g2_setting/g2_setting.pb*.dart`, plus enum values
  from `objs.txt`.
- **MentraOS** `G2.kt`: sid map (0x09 = G2 setting), settings sent to the right arm with
  flag 0x20, and the RING_CONNECT_INFO layout.
- **Brodie Friesen** (Discord): pair with the glasses, not the app; tap-and-hold is its
  own event.

## 11. Still open

- Whether the ring MAC on the glasses survives long sleeps and reboots. It survived
  app restarts and a minute off the face.
- Swipe `P1`/`P2` values (delta and speed). Zeros work.
- Battery reporting is fixed at 100 %. The Flipper's real level could be sent.
- What type `0x09` does on screens other than the dashboard.
- §8, Even-app pairing.
