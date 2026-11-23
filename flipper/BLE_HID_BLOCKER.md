# BLE HID remote for the Flipper — BLOCKED (verified)

**Status: NOT VIABLE from a stock Flipper `.fap`.** This file records the hard
evidence so the approach is not attempted again. No `g2_hid_remote` app was
written, because it cannot be built or run against the official firmware API.

## What was tested
Hypothesis: the Flipper, though unable to be a BLE *central* (GATT client), could
act as a BLE **HID keyboard peripheral**, letting the phone's Even app consume
arrow/Enter input with no relay. Peripherals are allowed, so this looked cheap.

## Verdict
The BLE HID profile is **not exported to apps** in this SDK/firmware. The header
exists, so code compiles, but the symbols are marked DISABLED in the firmware API
and are absent from `firmware.elf`, so `ufbt` hard-fails the build.

### Evidence
- Header present: `sdk_headers/f7_sdk/lib/ble_profile/extra_profiles/hid_profile.h`
  (declares `ble_profile_hid`, `ble_profile_hid_kb_press/release/release_all`,
  `ble_profile_hid_mouse_scroll/press/release/release_all`,
  `ble_profile_hid_consumer_key_*`).
- API status file `sdk_headers/f7_sdk/targets/f7/api_symbols.csv` marks every one
  of those symbols as `-` (DISABLED). In `scripts/fbt/sdk/cache.py`,
  `ApiEntryState.DISABLED = "-"`, and `get_functions()/get_variables()` filter
  disabled entries out of the exported API.
- `firmware.elf` (stock, 9.5 MB) contains `ble_profile_serial*` but **zero**
  `ble_profile_hid*` symbols -> the profile is not compiled into the firmware.
- `ufbt` build of a minimal probe fails at the `APPCHK` step (exit code 2):
  `app may not be runnable. Symbols not resolved using firmware's API:
  {'ble_profile_hid_mouse_scroll','ble_profile_hid_kb_press'}
  (in API, but disabled: {...})`

### What IS exported under ble_profile / ble_svc (usable by apps)
`ble_profile_serial*` (serial GATT peripheral), `ble_svc_serial_*`,
`ble_svc_battery_*`, `ble_svc_dev_info_*`, and raw server-side GATT helpers
`ble_gatt_service_add`, `ble_gatt_characteristic_init/update/delete`,
`ble_event_dispatcher_register_svc_handler`. `ble_svc_hid_*` is DISABLED too.
Note: `ble_gatt_*` are server-side (peripheral) only — still no central.

## Intended key mapping (design only; grounded in real HID constants)
Constants from `sdk_headers/f7_sdk/lib/libusb_stm32/inc/hid_usage_keyboard.h`,
`.../hid_usage_button.h`, `.../hid_usage_consumer.h`:

| Gesture            | Keyboard mode                 | Wheel mode                      |
|--------------------|-------------------------------|---------------------------------|
| UP tap / UP held   | `HID_KEYBOARD_UP_ARROW` 0x52  | `mouse_scroll(+1)` -> WHEEL +1  |
| DOWN tap / held    | `HID_KEYBOARD_DOWN_ARROW`0x51 | `mouse_scroll(-1)` -> WHEEL -1  |
| OK short           | `HID_KEYBOARD_RETURN` 0x28    | `mouse_press(HID_BUTTON_1)` 0x01|
| OK double-tap      | repeat `HID_KEYBOARD_RETURN`  | double click                    |
| OK long            | `HID_CONSUMER_AC_HOME` 0x223  | (hold)                          |
| LEFT               | `HID_KEYBOARD_LEFT_ARROW`0x50 | same                            |
| RIGHT              | `HID_KEYBOARD_RIGHT_ARROW`0x4F| same                            |
| BACK (hardware)    | exit app                      | exit app                        |

`ble_profile_hid_kb_press(profile, HID_KEYBOARD_UP_ARROW)` etc. would send these,
if the profile were exported.

## Viable next steps (not implemented here)
1. Serial relay (already in `flipper/apps/g2_controller/`) — needs a relay host.
2. Manual HOGP: build a HID-over-GATT service from the *approved* `ble_gatt_*` +
   `ble_event_dispatcher_*` APIs. Large custom job; unproven; requires custom logic.
3. USB HID via the *approved* `furi_hal_hid_*` API (keyboard over USB-C OTG) —
   a different transport; only relevant if the phone is a USB host and the Even
   app consumes hardware-keyboard events.
