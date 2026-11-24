# G2 Controller — physical bring-up checklist

Exact procedure to drive Even G2 glasses from a Flipper Zero through the relay.
Everything that CAN be checked off-hardware is below; anything that can only be
proven on real glasses/Flipper is listed under **KNOWN UNKNOWNS**.

Topology:

    Flipper Zero (BLE peripheral, Serial GATT)  --BLE-->  PC relay (BLE central)
                                                              |
                                                              +--BLE(EUS)--> Even G2

The Flipper is **only** a peripheral here. It advertises the Serial GATT service
and streams newline-terminated command lines; the relay is the sole BLE central
and owns the G2 link.

---

## 0. Before you start

1. Build the `.fap` (produces `flipper/apps/g2_controller/dist/g2_controller.fap`):

   ```bash
   cd flipper/apps/g2_controller && /home/CachedOut/.local/bin/ufbt
   ```

2. Know your relay host. Linux or Windows both work; `bleak` must be installed:

   ```bash
   cd relay && pip install bleak            # or: python -m pip install bleak
   ```

## 1. Install the app on the Flipper (qFlipper — there is NO USB mass storage)

The Flipper has no USB-drive mode. You copy files with **qFlipper**, not your OS
file explorer.

3. Connect the Flipper to the PC with a USB-C **data** cable (not charge-only).
4. Open **qFlipper** and wait for it to connect.
5. Go to the **File Manager** tab (left sidebar). You are browsing the microSD.
6. Create/enter the folder `apps/` at the root of the SD card.
7. Click **Upload** (the `+` / up-arrow button) and send
   `dist/g2_controller.fap` into `apps/`. The file must end up at `apps/g2_controller.fap`.
8. Safely eject / close qFlipper.

## 2. Run the app on the Flipper

9. On the Flipper: **Main Menu → Apps → Bluetooth → G2 Controller** (or
   Main Menu → Apps → G2 Controller).
10. The screen should immediately read:

    ```
    G2 Relay
    PC relay: waiting...
    Glasses: not linked
    -----------------------------------------
    no page shown
    U/D scroll  L/R page
    OK tap  2x/L-hold stat
    ```

11. The empty circle at the top-right means "no relay yet" — expected until
    step 15. The Flipper is now advertising the Serial GATT service, named
    `G2Ctrl`.

## 3. Start the relay

12. On the relay host:

    ```bash
    cd relay && python g2_relay.py -v          # auto-detects both devices by name
    # or pin addresses:
    # python g2_relay.py --flipper <FLIPPER_MAC> --glasses <G2_MAC>
    ```

13. The relay scans for the Flipper (name matches `g2ctrl`/`flipper`) and for the
    glasses (name matches `even g2` / `g2_`). It connects to each as a central.
14. **Glasses side:** make sure the G2 is powered/awake. The relay runs the EUS
    handshake and launch prelude, creates the page, then starts a 5 s heartbeat.

## 4. Confirm the link (watch the Flipper screen)

15. When the relay attaches to the Flipper, the Flipper sends `PING` every 2 s
    and the relay answers `PONG`. When `PONG` arrives the header changes to
    **`PC relay: linked`** with a filled dot (top-right). *(No filled dot after
    ~7 s ⇒ relay isn't attached or the Flipper↔relay GATT write/notify pair
    isn't matched — see KNOWN UNKNOWNS 6.)*
16. When the relay finishes the G2 session it emits `LINK 1` and the line
    becomes **`Glasses: linked`**. The glasses should show the relay menu
    ("Status / Battery / Time / Notes / Clear / Re-link"). The Flipper page line
    then shows `PAGE menu 1/6`.
17. If the relay can't hold the glasses it sends `LINK 0` → **`Glasses: not linked`**.
    *(Most common cause: the phone's Even app already owns the link — see
    KNOWN UNKNOWNS 5.)*

## 5. Button test (each press must move the glasses)

18. **UP** (tap) → glasses selection moves up; each tap sends exactly one
    `SCROLL_UP`. The Flipper blinks **blue** once per successful send.
19. **UP** (hold past ~0.5 s) → after the 500 ms auto-repeat grace the glasses scroll continuously
    (auto-repeat ~10 Hz). Release stops it.
20. **DOWN** (tap / hold) → the same, downwards.
21. **OK** (short) → one `TAP`: activates the highlighted menu item. NOTE: a lone
    OK is **deferred** — the app waits out the double-tap window (250 ms) to be
    sure you are not double-tapping before it emits `TAP`, so there is up to
    250 ms of latency between your tap and the activation. That delay is
    deliberate: without it a double-tap would ALSO activate the highlighted item
    (the menu includes Clear and Re-link, i.e. a stray tap can wipe the display
    or tear down the glasses session).
22. **OK** (double-tap: a second OK release within 250 ms) → `DOUBLE_TAP`: relay
    fetches glasses status and the Flipper's info line shows `INFO g2 battery …`.
    The Flipper blinks **blue** on a successful send (there is no green blink).
23. **OK** (long press) → `LONG_TAP`: same status ping. The hold threshold is the
    firmware's own `InputTypeLong` timing, which the app does **not** control —
    it is unrelated to the 500 ms scroll auto-repeat grace.
24. **LEFT** (short) → `NAV_BACK`: glasses go to the previous page.
25. **RIGHT** (short) → `NAV_HOME`: glasses go to the next page.
26. **LEFT** (long press) → `STATUS`: explicit status request (this is the
    button combination that replaced the old "OK-hold = STATUS").
27. **BACK** → exits the app cleanly (sends a best-effort `BYE`, restores the
    default BLE profile).

**A red blink on the Flipper means the line could not be written** to the BLE
link (relay gone) — the `last_tx` field shows the line prefixed with `!`.

### Gesture → wire token → relay action (authoritative)

| Gesture            | Flipper sends | Relay `on_flipper_line` does        |
|--------------------|---------------|-------------------------------------|
| UP tap             | `SCROLL_UP`   | menu selection up                   |
| UP hold            | `SCROLL_UP`×N | repeated selection up               |
| DOWN tap           | `SCROLL_DOWN` | menu selection down                 |
| DOWN hold          | `SCROLL_DOWN`×N | repeated selection down           |
| OK short           | `TAP`         | activate item (after 250 ms deferral) |
| OK double-tap      | `DOUBLE_TAP`  | quick status ping (single tap suppressed) |
| OK long press      | `LONG_TAP`    | status ping                         |
| LEFT short         | `NAV_BACK`    | previous page                       |
| RIGHT short        | `NAV_HOME`    | next page                           |
| LEFT long          | `STATUS`      | status ping                         |
| BACK               | (none)        | exit the app                        |
| ping timer (2 s)   | `PING`        | reply `PONG`                        |
| app exit           | `BYE`         | logged, ignored (no echo, no render)|

---

## KNOWN UNKNOWNS (confirmable only on real hardware)

These were NOT verified against a physical G2 / Flipper; they are the points to
watch when you first run the stack.

1. **Relay→G2 handshake payloads.** The 7 `sid=0x80` start-up messages and the
   verbatim `sid=0x01` launch prelude in `relay/g2proto.py` are byte-faithful to
   the vendored `g2.js` (golden-tested), but a **real G2's acceptance** of them
   is unverified — no BLE sniff has been captured.
2. **SID / magic matching.** `EUSLink.feed` completes a pending request when the
   reply magic matches; for settings/status replies that carry data in protobuf
   field 2 instead of echoing the magic, it falls back to "complete the oldest
   waiter on that sid" (safe only while requests are sequential). Verify with a
   BLE sniff.
3. **Auth challenge.** It is **unknown whether a production G2 demands a
   challenge/response auth** before accepting a session. `g2proto` implements no
   auth step. If the glasses silently ignore the prelude, auth is the first
   suspect.
4. **BLE service/characteristic selection on the Flipper link.** The relay
   picks the first writable characteristic for TX and the first notifiable one
   for RX from the Serial profile. This is a heuristic; confirm the chosen
   characteristic is the Serial profile's TX/RX on the real firmware.
5. **Only one central can hold the G2 link.** The G2 accepts a single active
   connection, so while the relay runs the **phone's Even app will be
   disconnected / fail to connect**. Expect your phone to lose the glasses and
   occasionally fight the relay for the link. When you want the phone back, stop
   the relay.
6. **Flipper ↔ relay GATT write/notify pairing.** `ble_profile_serial_tx`
   returns false (and the app blinks red) when the peer's notify buffer is full
   or the peer isn't subscribed. If `PC relay: linked` never lights but the BLE
   connection exists, the relay may not have subscribed to the Flipper's notify
   characteristic.
7. **Timing / tuning values.** The double-tap window is 250 ms (this is also the
   deferral latency added to every single tap); scroll auto-repeat uses a 500 ms
   initial grace, 100 ms period. All are chosen for feel and are safe to tune in
   `g2_controller.c`; real-handed ergonomics are unverified.
8. **`BYE` on exit** is a recognised token: the relay logs it and does nothing
   else (no `EVT` echo, no render). It is a farewell, not a gesture.


---

# RING flow — `g2_ring.fap` (the Flipper impersonates an Even R1 ring)

This is an INDEPENDENT path from the relay above. Instead of feeding a relay, the
Flipper *is* the ring: it advertises as an R1 and hosts the ring's vendor GATT
service. **The glasses are the BLE central and dial us.**

Topology:

    Even G2 glasses (BLE central)  --connects to-->  Flipper Zero (BLE peripheral)
                                                      advertising "EVEN R1_<hex6>" and
                                                      hosting bae80001-... with the
                                                      legacy (10/11) + health (12/13)
                                                      characteristics and the 0x2902 CCCD

## R0. Build

    cd flipper/apps/g2_ring && /home/CachedOut/.local/bin/ufbt
    # -> flipper/apps/g2_ring/dist/g2_ring.fap   (APPCHK must pass; exit 0)

## R1. Install via qFlipper (there is NO USB mass storage mode)

Identical mechanism to the relay app — qFlipper, not your OS file explorer:

1. Connect the Flipper with a USB-C **data** cable and open **qFlipper**.
2. **File Manager** tab (left sidebar) — you are browsing the microSD.
3. Enter `apps/` at the SD root.
4. **Upload** `dist/g2_ring.fap` into `apps/` → must land at `apps/g2_ring.fap`.
5. Eject / close qFlipper.

## R2. Run it and read the screen

6. On the Flipper: **Main Menu → Apps → Bluetooth → G2 Ring**.

   ```
   G2 Ring (R1 fake)                     (o)   <- hollow circle = no central;
   adv EVEN R1_XXXXXX                           filled disc    = a central is connected
   con- gls- pair- tch-
   ----------------------------------------------------------
   ccd nl16 nh21                                <- computed CCCD handles (bae80011/bae80013)
   < t02 0205...                                <- rolling log, newest line at the bottom
   ...
   ```

   * `adv EVEN R1_XXXXXX` — the advertised name; `XXXXXX` = the last three octets of
     the Flipper's BLE MAC. This is what an R1 looks like to a scanner.
   * `con` — a central is connected. `gls` — a link took the GLASSES role (enabled the
     CCCD on `bae80011`). `pair` — the legacy pair-auth traffic (`00 35 88 00` or a
     `pairAuth` frame) was seen. `tch` — a `touchSwitch` frame was seen.
   * `ccd nlXX nhXX` — the CCCD (0x2902) handles the app derives for the two notify
     characteristics as (notify value handle + 1), since the stack reports neither
     (descriptor_handle = 0 on the auto-allocated path). With the observed handles
     (nl=0x15, nh=0x20) these are `nl16` / `nh21`. `gls` flips to `Y` when an ATT write
     arrives at `nl16`; a write to `nh21` logs `! health CCCD wr`.
   * `! central gone` is printed ONLY after a real connection was first seen. The BT
     status callback fires on every status change — including Advertising at startup —
     so before any central has connected the log names the raw status (`bt st2` =
     advertising) instead of claiming a link was lost.
   * The log hex-logs EVERY BLE frame both ways, so a dead button is diagnosable here.
   * **LEFT** clears the log; **RIGHT** does nothing (not a ring gesture); **BACK** exits.

## R3. Add the Flipper as a ring in the Even app

7. On the phone, open **Even Realities**. Trigger its **ring pairing / add ring** flow
   (the app scans and matches the ring by NAME in software, so the `EVEN R1_` prefix is
   what it keys on — not the service UUID).
8. Watch the Flipper screen while the app scans/connects:
   * `con` flips to `Y` when the phone or glasses connects.
   * `gls` flips to `Y` only when a link enables the `bae80011` CCCD (the ring's glasses
     role) — that is the link gestures will flow down.
   * a `! pairAuth ...` / `! frame pairAuth` / `! touchSwitch open` line means the app
     reached the binding handshake.
9. If the app binds the ring, it hands it the glasses' address (`advStart`) and the
   glasses then connect **to the Flipper**. Expect `gls Y` and a `! frame advStart` line.

## R4. What to look for when NOTHING happens

The log is the whole point of the first run. **A button press that does not put
bytes on the radio prints an explicit `!` line naming the reason — never a
success-looking line.** What the screen prints for each failure mode:

| Press line                  | Meaning                                                        |
|-----------------------------|---------------------------------------------------------------|
| `> gst 04 scroll-top SENT`  | report written to the radio (good)                            |
| `! no profile 04`           | `bt_profile_start` did not install our profile                 |
| `! svc broken 04`           | GATT service/characteristic creation failed (screen `*SVC ERR*`)|
| `! no link 04`              | no central connected (`con-`)                                  |
| `! no gls role 04`          | connected but no link enabled the `bae80011` CCCD (no subscriber)|
| `! <100ms 04`               | a report went out <100 ms ago (receiver would drop this)       |
| `! tx REJECTED 04`          | `ble_gatt_characteristic_update` returned false                |

(`TT` is the gesture TYPE byte: `04`=scroll-top, `05`=scroll-bottom, `01`=tap,
`02`=double-tap, `00`=long-press, `08`=long-press-release.)

Startup / battery lines: `! advertising as R1`, `! GATT create FAILED`,
`! profile start FAILED`, `> bat 0064 SENT`, `! bat not sent`.

Symptoms that are NOT a button press:

* **Log stays empty and `con-`** → nothing connected. Confirm `adv EVEN R1_XXXXXX` is on
  screen and the phone lists "EVEN R1_...". See UNKNOWN R5/R6.
* **Screen shows `*SVC ERR*` / `!! GATT create FAILED`** → the service was never created,
  so the app is NOT a usable ring (it deliberately does not claim to be). See UNKNOWN R5.
* **Buttons log `> gst .. SENT` but the glasses do nothing** → bytes are leaving the radio
  but the glasses are not acting on them: wrong report length (UNKNOWN R2) or the glasses
  require a real bind (UNKNOWN R3/R4).
* **`< t02 ...` lines never appear** → our handler is not receiving the peer's ATT writes
  (the CCCD enable / pair-auth would show as `t02` ACL lines). See UNKNOWN R1.
* **`< t02 ...` lines appear but no `pair`/`gls`** → we ARE seeing traffic but our byte
  offsets for the ATT handle don't match — compare the raw hex against the expected
  `[type][handle2][len2][l2cap_len2][cid2][att_op][att_handle2][value...]` (UNKNOWN R1).

### Ring gesture → report bytes (authoritative)

| Button            | TYPE   | 11-byte report on `bae80011`                     |
|-------------------|--------|--------------------------------------------------|
| UP (short)        | 0x04   | `00 09 61 00 04 00 00 <TICK_LE32>` (scroll-top)  |
| DOWN (short)      | 0x05   | `00 09 61 00 05 00 00 <TICK_LE32>` (scroll-bottom)|
| OK (short)        | 0x01   | `00 09 61 00 01 00 00 <TICK_LE32>` (tap, deferred)|
| OK (double)       | 0x02   | `00 09 61 00 02 00 00 <TICK_LE32>` (double-tap)  |
| OK (long)         | 0x00   | `00 09 61 00 00 00 00 <TICK_LE32>` (long-press)  |
| OK (release)      | 0x08   | `00 09 61 00 08 00 00 <TICK_LE32>` (long-press-release)|
| LEFT              | —      | not a ring gesture: clears the log; no report    |
| RIGHT             | —      | not a ring gesture: unmapped; no report          |
| BACK              | —      | exits; restores the default BLE profile          |

`TICK_LE32` is a 32-bit little-endian millisecond counter; the receiver drops a report
whose tick advanced by < 100 ms, so the app never fires two reports closer than that.

## KNOWN UNKNOWNS (ring path — confirmable only on real hardware)

These are NOT verified against a physical G2 / Flipper / even app. They are the exact
points to watch on the first ring run, most-dangerous first.

R1. **Does the service event handler even receive the peer's ATT writes?** We parse the
    raw packets `ble_event_dispatcher_register_svc_handler` hands us as standard HCI
    ACL data (`[type=0x02][handle2][len2][l2cap_len2][cid2][att_op][att_handle2][value...]`)
    and treat ATT `0x12`/`0x52` writes. It is UNVERIFIED that ACL data is delivered to
    app handlers at all, and that these offsets are right for this stack. The on-screen
    raw `t02` hex log exists precisely so this can be read off hardware.

R2. **Is the notification length right?** `ble_gatt_characteristic_update` takes the
    value length from our data callback's `*data_len` (gatt.h: "For updates, called with
    a context"; length via the `cbBleGattCharacteristicData` out-param). We stage both the
    bytes and the 11-byte length on every emission, and also pass the staged buffer as
    `source`. If the implementation instead sends `G2_REPORT_MAX` (33) the glasses will
    reject the report — check the glasses' behaviour and the bytes on a BLE sniff.

R3. **Will the glasses dial a ring the app never registered?** The Even app is believed
    to hand the ring the glasses' address (`advStart`); a stand-in that was never
    registered may simply never be dialed. Unknown.

R4. **Is a BLE bond required before gestures flow?** There is an auth/bonding gate in the
    vendor app (`BleSecurityGate`, `pairAuth`). It may not sit on the glasses path (the
    glasses role is said to need no pairAuth) — but this is UNVERIFIED; if the link drops
    ~20 s after connecting, suspect a rejected/unbound peer.

R5. **Does `bt_service` accept an arbitrary app-supplied profile template at runtime?**
    The build only proves the symbols resolve. It is UNVERIFIED that
    `bt_profile_start(bt, &g2_ring_profile_template, app)` actually starts OUR profile and
    advertises with OUR `GapConfig`, rather than only accepting its built-in profiles.
    If `adv EVEN R1_...` never appears (and the screen logs `! profile start FAILED`),
    this is the culprit.

R6. **Discoverability of the advertised 128-bit service UUID.** The Even app scans with a
    NULL ScanFilter and matches the NAME in software, so the `EVEN R1_` prefix should be
    what admits us — but if the phone never lists the Flipper, the name pattern or a
    required manufacturer-data "ER" signature is the suspect.

R7. **Write-characteristic property bits.** The write chars declare `0x04|0x08` (write
    command AND write request). The real ring reads as `0x04`; if the glasses still get
    their writes rejected, try narrowing to `0x04` alone.
