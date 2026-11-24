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
