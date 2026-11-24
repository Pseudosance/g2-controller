"""Ring gesture-contract test (SOURCE-LEVEL ONLY).

!! THIS IS A SOURCE-LEVEL CHECK, NOT A BEHAVIOURAL ONE. !!
It parses constants and byte assignments out of flipper/apps/g2_ring/g2_ring.c
and checks them against the documented R1 ring spec. It CANNOT prove the fap
runs, that the glasses connect, or that a byte ever leaves the antenna. Only a
hardware run can confirm that. Its job is to fail loudly if someone edits the
gesture TYPE table or the 11-byte report layout away from the spec.

Style mirrors tests/test_wire_contract.py (plain prints + check()).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
C_FILE = os.path.join(ROOT, "flipper", "apps", "g2_ring", "g2_ring.c")

# The fixed TYPE vocabulary recovered from shipped community code (see the
# even-realities-g2 skill and jimrandomh/faceclaw).
EXPECTED_TYPES = {
    "GESTURE_LONG_PRESS": 0x00,
    "GESTURE_TAP": 0x01,
    "GESTURE_DOUBLE_TAP": 0x02,
    "GESTURE_SCROLL_TOP": 0x04,
    "GESTURE_SCROLL_BOTTOM": 0x05,
    "GESTURE_LONG_PRESS_RELEASE": 0x08,
}

FAIL = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def read_src():
    return open(C_FILE, encoding="utf-8").read()


def c_function_body(src, signature_prefix):
    """Extract a C function body by brace matching (no braces in strings)."""
    i = src.index(signature_prefix)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return src[j:k + 1]


def main():
    src = read_src()

    # ---- 1. gesture TYPE table matches the spec --------------------------
    defined = {m.group(1): int(m.group(2), 16)
               for m in re.finditer(r"#define\s+(GESTURE_\w+)\s+0x([0-9A-Fa-f]+)", src)}
    print("parsed TYPE table:", {k: hex(v) for k, v in sorted(defined.items())})
    check(defined == EXPECTED_TYPES,
          "gesture TYPE constants match the spec exactly (%s)" % (defined,))

    # ---- 2. the 11-byte report: fixed prefix + TYPE + LE tick ------------
    check(re.search(r"#define\s+G2_REPORT_LEN\s+11\b", src) is not None,
          "report length is 11 bytes")

    emit = c_function_body(src, "static void g2_ring_emit")
    assigns = {int(m.group(1)): m.group(2).strip()
               for m in re.finditer(r"g2_ring_out_buf\[(\d+)\]\s*=\s*([^;]+);", emit)}
    print("emit buf assignments:", assigns)

    def as_hex(v):
        try:
            return int(v, 16)
        except ValueError:
            return None

    check(as_hex(assigns.get(0, "")) == 0x00, "report byte[0] == 0x00")
    check(as_hex(assigns.get(1, "")) == 0x09, "report byte[1] == 0x09")
    check(as_hex(assigns.get(2, "")) == 0x61, "report byte[2] == 0x61")
    check(as_hex(assigns.get(3, "")) == 0x00, "report byte[3] == 0x00")
    check("type" in assigns.get(4, ""),
          "report byte[4] is the gesture TYPE argument")
    check(as_hex(assigns.get(5, "")) == 0x00, "report byte[5] (AUX) == 0x00")
    check(as_hex(assigns.get(6, "")) == 0x00, "report byte[6] (SPEED) == 0x00")
    for idx, shift in ((7, None), (8, 8), (9, 16), (10, 24)):
        rhs = assigns.get(idx, "")
        ok = "now" in rhs and (shift is None or (">>" in rhs and str(shift) in rhs))
        check(ok, "report byte[%d] is TICK little-endian (rhs=%r)" % (idx, rhs))

    # ---- 3. DEFERRED single tap: OK-short must NOT emit TAP directly -----
    short = c_function_body(src, "static void g2_ring_ok_short")
    check("GESTURE_TAP" not in short,
          "OK-short handler does NOT emit TAP directly (single tap is deferred)")
    check("furi_timer_start" in short,
          "OK-short handler arms the double-tap window timer instead")
    custom = c_function_body(src, "static bool g2_ring_custom_event_callback")
    check("GESTURE_TAP" in custom,
          "deferred TAP is emitted from the dispatcher custom-event callback")

    # ---- 4. GATT UUID bytes + property flags -----------------------------
    check("0x1f, 0x9d, 0x32, 0xf7, 0xf1, 0x3a, 0x65, 0x8e, 0x03, 0x45, 0x05," in src
          and "(byte13), 0x00, 0xe8, 0xba" in src,
          "128-bit service UUID is written little-endian as 1f 9d 32 f7 ... 01 00 e8 ba")
    for b in ("0x10", "0x11", "0x12", "0x13"):
        check(("R1_UUID(%s)" % b) in src,
              "characteristic UUID bae800%s present" % b)
    check(re.search(r"#define\s+GATT_PROP_WRITE\s+0x08\b", src) is not None,
          "write property flag is 0x08")
    check(re.search(r"#define\s+GATT_PROP_NOTIFY\s+0x10\b", src) is not None,
          "notify property flag is 0x10")
    check(re.search(r"#define\s+GATT_PROP_WRITE_WITHOUT_RESP\s+0x04\b", src) is not None,
          "write-without-response property flag is 0x04")
    check(src.count("GATT_PROP_WRITE_ANY") >= 2,
          "both write characteristics declare the widened write property (0x04|0x08 = 0x0C)")
    # the notification length is conveyed by the data callback's *data_len, so it
    # must be staged on every gesture emission (not left at the 33-byte max).
    check("g2_ring_out_len = G2_REPORT_LEN" in src,
          "notification length is staged on every gesture emission")
    # Notify chars must NOT declare an explicit 0x2902 CCCD.
    # The STM32WB radio auto-allocates the CCCD for a notify/indicate char when
    # it is added; every shipped service (ble_svc_serial_chars / ble_svc_battery_
    # chars, decoded from .rodata) passes descriptor_params = NULL, and no
    # BleGattCharacteristicDescriptorParams with uuid 0x2902 exists anywhere in
    # firmware.elf. Declaring our own was actively harmful: on hardware,
    # ble_gatt_characteristic_init takes the descriptor branch when
    # descriptor_params != NULL and calls the descriptor's data_callback.fn --
    # which we left NULL -- and the Flipper took a MemManage fault. So every
    # characteristic must pass NULL as its descriptor. This assertion used to
    # demand the opposite; it now contradicts the header/radio instead of the code.
    fill_calls = re.findall(r"g2_ring_fill_char\(&(.*?)\);", src, re.S)
    notify_calls = [c for c in fill_calls if "p_notify" in c]
    check(len(notify_calls) == 2,
          "both notify characteristics are configured through g2_ring_fill_char")
    check(all(c.rstrip().endswith("NULL") for c in fill_calls),
          "every characteristic passes descriptor_params = NULL (no explicit 0x2902 CCCD)")
    check("uuid.Char_UUID_16 = 0x2902" not in src,
          "no explicit 0x2902 CCCD descriptor is declared")
    check("BleGattCharacteristicDescriptorParams d_cccd" not in src,
          "no explicit CCCD descriptor members remain in the profile struct")

    # ---- 5. the min-report-gap guard (>=100 ms) --------------------------
    check(re.search(r"#define\s+G2_MIN_REPORT_GAP_MS\s+100\b", src) is not None,
          "minimum inter-report gap is 100 ms (receiver drops <100 ms)")

    # ---- 6. FIX 1: emission is gated; failures print DISTINCT lines ------
    emit = c_function_body(src, "static void g2_ring_emit")
    for token in ("central_connected", "glasses_role", "svc_ok"):
        check(token in emit, "emission gate consults %s" % token)
    for lit in ('"> gst %02X %s SENT"', '"! no link %02X"', '"! no gls role %02X"',
                '"! tx REJECTED %02X"', '"! svc broken %02X"', '"! no profile %02X"'):
        check(lit in emit, "emit prints a distinct line %s" % lit)
    # a rejected update must read differently from a good send
    check('SENT' in emit and 'REJECTED' in emit,
          "tx result is honest (REJECTED reads differently from SENT)")

    # ---- 7. FIX 2: staged length initialised to the MAX (never 0) --------
    check(re.search(r"uint16_t\s+g2_ring_out_len\s*=\s*G2_REPORT_MAX\s*;", src) is not None,
          "staged notify length initialised to G2_REPORT_MAX (cannot define a zero-length char)")

    # ---- 8. FIX 3: service-creation failure is surfaced -------------------
    check("svc_error" in src, "model carries a persistent svc_error state")
    check("g2_ring_set_svc_error" in src, "a helper records the service failure")
    check(re.search(r'GATT create FAILED', src) is not None,
          "screen/log says GATT create FAILED when the service was not created")

    # ---- 9. FIX 4b: the attribute-write mask uses the notify-only bit ----
    check("GATT_NOTIFY_ATTRIBUTE_WRITE" in src,
          "write chars use GATT_NOTIFY_ATTRIBUTE_WRITE (0x01, notify without a wait)")
    # the WAIT_FOR_APPL_RESP bit must not be ASSIGNED to any gatt_evt_mask
    check(not re.search(r"gatt_evt_mask\s*=\s*[^;]*GATT_NOTIFY_WRITE_REQ_AND_WAIT_FOR_APPL_RESP",
                        src),
          "the WAIT_FOR_APPL_RESP (0x02) bit is deliberately NOT set")

    print()
    print("NOTE: source-level only -- it cannot prove the device works on hardware.")
    print("FAILURES:", FAIL if FAIL else "none")
    return 1 if FAIL else 0


sys.exit(main())
