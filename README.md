# G2 Controller — Dual Implementation

Control Even Realities G2 glasses without the R1 ring.

## Project Structure

```
g2-controller/
├── android/           # Android app (Kotlin + Compose + BLE)
│   └── app/
└── flipper/           # Flipper Zero app (C, builds to .fap)
    └── apps/g2_controller/
```

---

## Android App

**Target:** Old Android phone (API 26+), one-handed thumb-zone UI

### Build

```bash
cd android
./gradlew assembleDebug
# Output: app/build/outputs/apk/debug/app-debug.apk
```

### Install

```bash
adb install app/build/outputs/apk/debug/app-debug.apk
```

### Use

1. Open app → "Scan for G2"
2. Put G2 in pairing mode (hold button 3s)
3. Select G2 from list
4. Controls appear — large buttons at bottom for thumb:
   - **Scroll ↑ / ↓** — scroll content
   - **Tap / Long Tap / 2× Tap** — click variants
   - **Back / Home** — navigation
5. App runs in foreground service (persistent notification) — survives screen-off

### Key Files

| File | Purpose |
|------|---------|
| `ble/EusPackets.kt` | EUS protocol framing (ported from even-g2-js) |
| `ble/EusClient.kt` | Low-level GATT client |
| `ble/BleManager.kt` | Scan, connect, reconnect, state |
| `service/G2ControllerService.kt` | Foreground service |
| `ui/ControllerScreen.kt` | One-handed Compose UI |
| `ui/ControllerViewModel.kt` | State + commands |

### Protocol Reference

EUS Service: `00002760-08c2-11e1-9073-0e8ac72e5450`
- TX (write): `00002760-08c2-11e1-9073-0e8ac72e5401`
- RX (notify): `00002760-08c2-11e1-9073-0e8ac72e5402`

Commands implemented:
- `0x20` Scroll (payload: direction + lines)
- `0x21` Tap
- `0x22` Long tap
- `0x23` Double tap
- `0x24` Nav back
- `0x25` Nav home

---

## Flipper Zero App

**Target:** Flipper Zero (any firmware), physical 5-way button control

### Build (with ufbt)

```bash
# Option 1: In flipperzero-firmware repo
cp -r flipper/apps/g2_controller /path/to/flipperzero-firmware/applications_user/
cd /path/to/flipperzero-firmware
./fbt fap_g2_controller

# Option 2: Standalone with ufbt
cd flipper/apps/g2_controller
ufbt
# Output: dist/g2_controller.fap
```

### Install

1. Copy `g2_controller.fap` to Flipper SD card: `SD:/apps/g2_controller.fap`
2. On Flipper: Main Menu → Apps → G2 Controller

### Use

| Button | Action |
|--------|--------|
| **UP** | Scroll up |
| **DOWN** | Scroll down |
| **OK (short)** | Tap |
| **OK (hold 1s)** | Long tap |
| **OK (double-click)** | Double tap |
| **LEFT** | Nav back |
| **RIGHT** | Nav home |
| **BACK** | Exit app |

**No firmware flashing** — just a `.fap` file on SD card. Delete anytime.

### Key Files

| File | Purpose |
|------|---------|
| `application.fam` | App manifest |
| `g2_controller.c` | Main app (BLE + input + UI) |
| `CMakeLists.txt` | Build config |

---

## EUS Protocol Notes

Frame format (from reverse engineering):
```
[FLAG:1][LEN_HI:1][LEN_LO:1][CMD:1][PAYLOAD:N][CHECKSUM:1]
Checksum = XOR of all preceding bytes
```

Known commands (from `mrahtz/even-g2-js` and `aluminumio/even`):
- `0x01` Ping
- `0x02` Auth (challenge-response)
- `0x10` Display text
- `0x11` Display image
- `0x12` Clear
- `0x20` Scroll (dir: 0=up, 1=down; lines)
- `0x21` Tap
- `0x22` Long tap
- `0x23` Double tap
- `0x24` Nav back
- `0x25` Nav home

---

## Next Steps

1. **Android:** Fill in `EusClient.write()` queue processor, test with real G2
2. **Flipper:** Complete BLE service discovery + characteristic handles
3. **Both:** Implement auth handshake if G2 requires it (check `even-g2-js` for current flow)

---

## References

- `mrahtz/even-g2-js` — Web Bluetooth reference implementation
- `aluminumio/even` — Swift implementation (UUID source)
- `jimrandomh/faceclaw` — Unofficial Android app (protocol details)
