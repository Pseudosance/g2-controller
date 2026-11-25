/*
 * g2_ring -- Flipper Zero fap that IMPERSONATES an Even Realities R1 ring.
 *
 * Topology we are faking:
 *
 *   Even G2 glasses (BLE *central*)  ---connects to--->  R1 ring (BLE *peripheral*)
 *
 * The glasses are the central; the ring is a peripheral that hosts a vendor GATT
 * service and pushes 11-byte gesture reports on a notify characteristic. The
 * Flipper's APPROVED BLE role is server-side/peripheral, so a fap CAN host that
 * service and advertise as a ring -- see flipper/BLE_HID_BLOCKER.md.
 *
 * The data plane (service + characteristics + notifications) uses only symbols
 * marked '+' in sdk_headers/f7_sdk/targets/f7/api_symbols.csv. The control plane
 * (advertised name + advertised service UUID) is reached by installing our own
 * FuriHalBleProfileTemplate through the APPROVED bt_profile_start(); every direct
 * gap_* symbol is DISABLED and is NOT called.
 *
 * Vendor service UUID bae80001-4f05-4503-8e65-3af1f7329d1f, written
 * little-endian (LSB first) over the air as:
 *     1f 9d 32 f7 f1 3a 65 8e 03 45 05 4f 01 00 e8 ba
 *
 * ===========================================================================
 *  BUTTON MAP  --  Flipper key  ->  R1 report TYPE byte        *** EDIT HERE ***
 * ===========================================================================
 *  Key  (gesture)          TYPE   R1 meaning              emitted?
 *  ---------------------   ----   ---------------------   --------------------
 *  SIDEWAYS LAYOUT: hold the Flipper with the RIGHT key on top.
 *  RIGHT (short)           0x04   scroll-top              yes
 *  LEFT  (short)           0x05   scroll-bottom           yes
 *  UP    (short)           0x02   double-tap (back)       yes
 *  DOWN  (short)           0x01   tap (select)            yes
 *  OK    (short)           00,08  hold + release (menu) yes (ring long-press = Menu)
 *  OK    (long)            0x00   long-press              yes (on Long event)
 *  OK    (release after    0x08   long-press-release      yes (on Release)
 *         a long press)
 *  BACK                    --     not a ring gesture       exit the app
 * ===========================================================================
 *
 *  Each gesture is an 11-byte report on bae80011:
 *     00 09 61 00 | TYPE | AUX | SPEED | TICK_LE32
 *  bytes 0..3 fixed, TYPE per the table above, AUX=SPEED=0, TICK is a 32-bit
 *  LITTLE-ENDIAN millisecond counter.
 *
 *  DEFERRED SINGLE TAP (why OK-short is not emitted on the first release):
 *  The Flipper has no native double-click InputEvent. A naive scheme that emits
 *  the single action on the first release makes EVERY double-tap also fire the
 *  single action. So the first OK release only ARMS a one-shot window; tap is
 *  emitted when it expires with no second release. This is the SAME pattern as
 *  flipper/apps/g2_controller/g2_controller.c (g2_ok_short) -- copied, not
 *  reinvented. Cost: every single tap is delayed by up to DOUBLE_TAP_WINDOW_MS.
 *
 *  THREADING: BLE callbacks arrive on the BLE thread. They never touch the GUI
 *  directly -- they only update the view model (with_view_model, which has its
 *  own lock) and post custom events through the view dispatcher. All
 *  ble_gatt_characteristic_update() calls happen on the GUI thread. Shared
 *  non-model state (the profile pointer, the report gap timer) is mutex-guarded.
 *
 * ===========================================================================
 *  API PARAMETERS -- matched against the SHIPPED services, decoded from the
 *  stock firmware.elf, never invented:
 * ===========================================================================
 *  ble_gatt_service_add(UUID_TYPE_128BIT, &su, PRIMARY_SERVICE=0x01, 12, &h)
 *    - Service_Type must be PRIMARY_SERVICE (0x01). 0x00 is not a defined type;
 *      aci_gatt_add_service forwards the byte to the coprocessor verbatim, and
 *      ble_svc_serial_start / ble_svc_battery_start both pass 0x01.
 *    - Max_Attribute_Records = 12: the value ble_svc_serial_start uses for its
 *      own four-characteristic 128-bit PRIMARY service (battery uses 8). 20 was
 *      over-sized for a finite, shared GATT attribute table.
 *    - Characteristics declare NO descriptor: every shipped notify char passes
 *      descriptor_params = NULL and the STM32WB auto-allocates the 0x2902 CCCD.
 *      No 0x2902 descriptor exists anywhere in firmware.elf.
 *    - security_permissions = ATTR_PERMISSION_NONE (0x00), a value that IS
 *      defined in ble_defs.h, so it is left as-is.
 *
 * ===========================================================================
 *  STACK FOOTPRINT -- the BLE-callback and profile-start paths run on threads
 *  with small stacks, and a MemManage fault on this Cortex-M host is the
 *  classic stack-overflow signature. Every large local on those paths
 *  (Service_UUID_t, and the hex/line/diag/name format buffers) is therefore
 *  static rather than stack. Every snprintf is bounded by the sizeof of its
 *  destination.
 * ===========================================================================
 */

#include <furi.h>
#include <furi_hal.h>
#include <furi_hal_version.h>
#include <furi_hal_bt.h>

#include <bt/bt_service/bt.h>

#include <gap.h>
#include <furi_ble/profile_interface.h>
#include <furi_ble/gatt.h>
#include <furi_ble/event_dispatcher.h>

#include <gui/gui.h>
#include <gui/view.h>
#include <gui/view_dispatcher.h>

#include <string.h>
#include <stdio.h>

#define TAG "G2Ring"

/* Glasses-direct mode (2026-11-24): host ONLY bae80010/bae80011. The G2 discovers
 * bae80010, bae80011 and bae80011's CCCD (openCFW g2_ring_disc_char_list); with the
 * phone-role pair present it wrote its CCCD enable to handle 19 (bae80012's value)
 * instead of 17, so the subscription never reached the stack. 0 = also host the
 * phone-role pair bae80012/bae80013 for the Even app path. */
#ifndef G2_GLASSES_ONLY
#define G2_GLASSES_ONLY 1
#endif

/* ---- gesture TYPE bytes (the ring's real vocabulary) -------------------- */
#define GESTURE_LONG_PRESS         0x00
#define GESTURE_TAP                0x01
#define GESTURE_DOUBLE_TAP         0x02
#define GESTURE_SCROLL_TOP         0x04
#define GESTURE_SCROLL_BOTTOM      0x05
#define GESTURE_LONG_PRESS_RELEASE 0x08

/* Deferred single-tap window (the one knob for tap feel). */
#define DOUBLE_TAP_WINDOW_MS 250
/* The Flipper GAP drops to slow (low-power) advertising 30 s after it starts,
 * and the glasses' reconnect scan misses it. Restarting advertising every
 * ADV_KICK_MS while no central is connected keeps it in the fast mode. */
#define ADV_KICK_MS 25000
/* OK = menu: hold, then release after MENU_HOLD_MS. */
#define MENU_HOLD_MS        800
/* The receiving side suppresses a report whose TICK advanced by < 100 ms, so we
 * must never fire two reports closer than this. */
#define G2_MIN_REPORT_GAP_MS 100

#define G2_REPORT_LEN 11  /* 00 09 61 00 TYPE AUX SPEED TICK_LE32 */
#define G2_REPORT_MAX 33  /* creation-time max characteristic value length */

/* ---- the R1 vendor UUIDs, little-endian (LSB first) --------------------- */
/* Full service UUID bae80001-4f05-4503-8e65-3af1f7329d1f expands to:
 *   1f 9d 32 f7 f1 3a 65 8e 03 45 05 4f 01 00 e8 ba
 * The characteristic UUIDs share the first 12 bytes; only the 13th byte
 * (0x10/0x11/0x12/0x13) differs. */
#define R1_UUID(byte13)                                                    \
    {                                                                      \
        0x1f, 0x9d, 0x32, 0xf7, 0xf1, 0x3a, 0x65, 0x8e, 0x03, 0x45, 0x05,  \
            0x4f, (byte13), 0x00, 0xe8, 0xba                              \
    }

static const uint8_t r1_uuid_service[16] = R1_UUID(0x01);        /* bae80001 */
static const uint8_t r1_uuid_write_legacy[16] = R1_UUID(0x10);   /* bae80010 */
static const uint8_t r1_uuid_notify_legacy[16] = R1_UUID(0x11);  /* bae80011 */
static const uint8_t r1_uuid_write_health[16] = R1_UUID(0x12);   /* bae80012 */
static const uint8_t r1_uuid_notify_health[16] = R1_UUID(0x13);  /* bae80013 */
static const uint8_t r1_uuid_pad[16] = R1_UUID(0xF0);            /* bae800f0: handle pad */

/* GATT property / type / access bit flags.
 *
 * Authoritative source (raw lines) --
 * sdk_headers/f7_sdk/lib/stm32wb_copro/wpan/ble/core/ble_defs.h:
 *   #define ATTR_ACCESS_READ_WRITE               0x03U
 *   #define CHAR_PROP_READ                       0x02U
 *   #define CHAR_PROP_WRITE_WITHOUT_RESP         0x04U
 *   #define CHAR_PROP_WRITE                      0x08U
 *   #define CHAR_PROP_NOTIFY                     0x10u
 *
 * 0x08 alone is Write WITH response. A peer that sends a Write Command (no
 * response) to a characteristic declaring only 0x08 has its write REJECTED by
 * the stack -- which on hardware is indistinguishable from "nothing happens".
 * The real R1 write chars read as prop=0x04 (write-without-response) and the
 * legacy pair-auth 00 35 88 00 arrives as a Write Command, so we declare BOTH
 * bits (0x04|0x08 = 0x0C) and the expected traffic is never rejected. */
#define GATT_PROP_WRITE_WITHOUT_RESP 0x04
#define GATT_PROP_WRITE              0x08
#define GATT_PROP_NOTIFY             0x10
#define GATT_PROP_WRITE_ANY          (GATT_PROP_WRITE_WITHOUT_RESP | GATT_PROP_WRITE) /* 0x0C */
#define UUID_TYPE_16BIT  0x01
#define UUID_TYPE_128BIT 0x02
/* Service_Type for ble_gatt_service_add. Authoritative source (raw lines) --
 * sdk_headers/f7_sdk/lib/stm32wb_copro/wpan/ble/core/ble_defs.h:
 *   #define PRIMARY_SERVICE                            0x01U
 *   #define SECONDARY_SERVICE                          0x02U
 * A vendor service is a PRIMARY service: shipped working services pass 0x01
 * (ble_svc_serial_start and ble_svc_battery_start both set r2=1 before the
 * call -- see the disassembly notes). ble_gatt_service_add forwards the
 * Service_Type byte verbatim to the STM32WB coprocessor (aci_gatt_add_service
 * copies it straight into the ACI command; it is NOT range-checked on the
 * Flipper side), so 0x00 -- neither PRIMARY nor SECONDARY -- is rejected at
 * runtime and ble_gatt_service_add returns false. */
#define GATT_SERVICE_PRIMARY 0x01
/* ATTR_ACCESS_READ_WRITE (0x03) comes from the SDK's ble_defs.h (quoted above). */

/* Attribute-write notification mask passed to the stack per characteristic.
 * Authoritative source (raw lines) --
 * sdk_headers/f7_sdk/lib/stm32wb_copro/wpan/ble/core/ble_defs.h:
 *   #define GATT_DONT_NOTIFY_EVENTS                       0x00U
 *   #define GATT_NOTIFY_ATTRIBUTE_WRITE                   0x01U
 *   #define GATT_NOTIFY_WRITE_REQ_AND_WAIT_FOR_APPL_RESP  0x02U
 *   #define GATT_NOTIFY_READ_REQ_AND_WAIT_FOR_APPL_RESP   0x04U
 *   #define GATT_NOTIFY_NOTIFICATION_COMPLETION           0x08U
 *
 * DECISION: the write chars use GATT_NOTIFY_ATTRIBUTE_WRITE (0x01) -- "tell the
 * app about the write", with NO obligation to answer -- and notify chars use
 * GATT_DONT_NOTIFY_EVENTS (0x00). We deliberately do NOT set
 * GATT_NOTIFY_WRITE_REQ_AND_WAIT_FOR_APPL_RESP (0x02): that bit makes the stack
 * WAIT for an application write-response, and our handler only observes and
 * returns BleEventAckFlowEnable -- it never issues a response -- so 0x02 would
 * leave the peer's write unacknowledged. Writes are observed via the raw event
 * dispatcher (ble_event_dispatcher_register_svc_handler), not by servicing a
 * wait. (0x04/0x08 are read-wait and notify-completion and are not needed.)
 *
 * These three names are #defined by ble_defs.h (quoted above); the code USES
 * them directly and does not redefine them. */

/* ---- notification report source ---------------------------------------- */
/* A notify characteristic with callback data is asked for its value when we
 * call ble_gatt_characteristic_update(). We stage the current report here and
 * the callback hands it back. Guarded by app->mutex around writes. */
static uint8_t g2_ring_out_buf[G2_REPORT_MAX];
/* FIX 2: initialise to the MAX, never 0. The creation-time invocation of the
 * data callback is what sets the characteristic's maximum value length. If the
 * stack calls it with a non-NULL `data` (the other reading of the contract in
 * gatt.h), the callback returns this staged length -- a 0 here would define an
 * unusable ZERO-length characteristic, which is fatal and silent. With the max
 * staged, BOTH readings yield a non-zero length at creation, and every emission
 * then stages the real (11- or 6-byte) length. */
static uint16_t g2_ring_out_len = G2_REPORT_MAX;

/* Static scratch backing the (fixed-data) write characteristics. */
static uint8_t g2_ring_write_scratch[64]; /* advStart is 29 bytes */

/* ---- app + model ------------------------------------------------------- */

#define APP_VIEW_MAIN 1
#define LOG_LINES 10
#define LOG_LINE_LEN 30

typedef struct G2RingApp G2RingApp;

typedef struct {
    char adv_name[20];
    /* The Flipper's OWN BLE address in human order ("AA:BB:CC:DD:EE:FF") -- the
     * address the glasses must be told to dial. Shown on screen and logged at
     * startup so the user can read it off the Flipper and pass it to
     * tools/ring_introduce.py without a separate BLE scanner. */
    char ble_mac[18];
    uint16_t cccd_legacy;
    uint16_t cccd_health;
    bool central_connected;
    bool glasses_role;
    bool pair_auth_seen;
    bool touch_seen;
    bool svc_error; /* GATT service/characteristic creation failed: persistent */
    char lines[LOG_LINES][LOG_LINE_LEN];
    uint8_t head;
    uint8_t count;
} G2RingModel;

/* The running GATT server lives here. `base` MUST be the first member. */
typedef struct {
    FuriHalBleProfileBase base;
    G2RingApp* app;

    uint16_t svc_handle;

    /* CCCD (0x2902) handle for each notify characteristic. The stack does not
     * report it (descriptor_handle stays 0 on the auto-allocated path), so it is
     * derived as the notify value handle + 1 -- the CCCD sits immediately after
     * its characteristic on the observed handle sequence (nl=0x15 -> 0x16). */
    uint16_t cccd_legacy;
    uint16_t cccd_health;

    BleGattCharacteristicParams p_pad;
    BleGattCharacteristicParams p_write_legacy;
    BleGattCharacteristicParams p_notify_legacy;
    BleGattCharacteristicParams p_write_health;
    BleGattCharacteristicParams p_notify_health;

    BleGattCharacteristicInstance ch_pad;
    BleGattCharacteristicInstance ch_write_legacy;
    BleGattCharacteristicInstance ch_notify_legacy;
    BleGattCharacteristicInstance ch_write_health;
    BleGattCharacteristicInstance ch_notify_health;

    GapSvcEventHandler* svc_handler;
    bool svc_ok; /* service + all characteristics created successfully */
} G2RingProfile;

struct G2RingApp {
    Gui* gui;
    ViewDispatcher* view_dispatcher;
    View* view;
    Bt* bt;

    FuriMutex* mutex;   /* guards profile + last_report_ms + out buffer */
    FuriTimer* tap_timer;
    FuriTimer* adv_timer; /* re-arms fast advertising while unconnected */

    G2RingProfile* profile;   /* set by profile start, cleared by profile stop */
    bool ok_tap_pending;      /* deferred single tap armed (GUI thread only) */
    uint8_t menu_step;        /* OK macro: 1 = hold next, 2 = release next */
    bool ok_long_active;      /* a long press is in progress (GUI thread only) */
    uint32_t last_report_ms;

    /* Link/role/health state, mirrored from the model so the GUI-thread emit
     * path can consult it under `mutex` without taking the model lock.
     * Written on the BLE thread via the g2_ring_set_* helpers below. */
    bool central_connected;
    bool glasses_role;
    bool svc_ok;
    bool saw_connection; /* a real central has connected at least once (BLE thread) */
};

enum {
    G2RingEventDeferredTap = 1,
    G2RingEventSendBattery,
    G2RingEventSendRsp,
    G2RingEventAdvKick,
};

/* Forward declaration: start() references the template, the template references
 * start(). */
static const FuriHalBleProfileTemplate g2_ring_profile_template;
static bool g2_ring_report_cb(const void* context, const uint8_t** data, uint16_t* data_len);

/* ---- helpers ----------------------------------------------------------- */

static uint32_t g2_ring_now_ms(void) {
    return (uint32_t)((uint64_t)furi_get_tick() * 1000ULL /
                      (uint64_t)furi_kernel_get_tick_frequency());
}

static void g2_ring_log(G2RingApp* app, const char* s) {
    with_view_model(
        app->view, G2RingModel * m,
        {
            snprintf(m->lines[m->head], LOG_LINE_LEN, "%s", s);
            m->head = (uint8_t)((m->head + 1) % LOG_LINES);
            if(m->count < LOG_LINES) m->count++;
        },
        true);
}

static void g2_ring_set_glasses(G2RingApp* app, bool on) {
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    app->glasses_role = on;
    furi_mutex_release(app->mutex);
    with_view_model(app->view, G2RingModel * m, { m->glasses_role = on; }, true);
}

static void g2_ring_set_link(G2RingApp* app, bool on) {
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    app->central_connected = on;
    furi_mutex_release(app->mutex);
    with_view_model(app->view, G2RingModel * m, { m->central_connected = on; }, true);
}

static void g2_ring_set_svc_error(G2RingApp* app) {
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    app->svc_ok = false;
    furi_mutex_release(app->mutex);
    with_view_model(app->view, G2RingModel * m, { m->svc_error = true; }, true);
}

static void g2_ring_set_pair_auth(G2RingApp* app) {
    with_view_model(app->view, G2RingModel * m, { m->pair_auth_seen = true; }, true);
}

static void g2_ring_set_touch(G2RingApp* app) {
    with_view_model(app->view, G2RingModel * m, { m->touch_seen = true; }, true);
}

static void hexify(char* out, size_t outlen, const uint8_t* b, size_t n) {
    size_t o = 0;
    if(outlen == 0) return;
    out[0] = '\0';
    for(size_t i = 0; i < n && (o + 3) < outlen; i++) {
        int w = snprintf(out + o, outlen - o, "%02X", b[i]);
        if(w <= 0) break;
        o += (size_t)w;
    }
}

/* ---- gesture emission (ALWAYS on the GUI thread) ----------------------- */

/* FIX 1: the emission gate. A report is sent ONLY when the profile exists, the
 * GATT service was created, a central is connected, AND this link holds the
 * ring's GLASSES role (its CCCD on bae80011 was enabled, i.e. a subscriber
 * exists). Anything else prints a DISTINCT line naming the reason -- never a
 * success-looking one -- so the screen tells "bytes left the radio" apart from
 * "nothing happened". The tx result is honest too: a rejected update reads
 * differently from a good one. */
static void g2_ring_emit(G2RingApp* app, uint8_t type, const char* tag) {
    uint32_t now = g2_ring_now_ms();
    char line[LOG_LINE_LEN];

    furi_mutex_acquire(app->mutex, FuriWaitForever);
    G2RingProfile* profile = app->profile;
    bool svc_ok = app->svc_ok;
    bool connected = app->central_connected;
    bool glasses = app->glasses_role;

    if(profile == NULL) {
        furi_mutex_release(app->mutex);
        snprintf(line, sizeof(line), "! no profile %02X", type);
    } else if(!svc_ok) {
        furi_mutex_release(app->mutex);
        snprintf(line, sizeof(line), "! svc broken %02X", type);
    } else if(!connected) {
        furi_mutex_release(app->mutex);
        snprintf(line, sizeof(line), "! no link %02X", type);
    } else if(!glasses) {
        furi_mutex_release(app->mutex);
        snprintf(line, sizeof(line), "! no gls role %02X", type);
    } else if((now - app->last_report_ms) < G2_MIN_REPORT_GAP_MS) {
        furi_mutex_release(app->mutex);
        snprintf(line, sizeof(line), "! <100ms %02X", type);
    } else {
        memset(g2_ring_out_buf, 0, sizeof(g2_ring_out_buf));
        g2_ring_out_buf[0] = 0x00;
        g2_ring_out_buf[1] = 0x09;
        g2_ring_out_buf[2] = 0x61;
        g2_ring_out_buf[3] = 0x00;
        g2_ring_out_buf[4] = type;
        g2_ring_out_buf[5] = 0x00; /* AUX */
        g2_ring_out_buf[6] = 0x00; /* SPEED */
        g2_ring_out_buf[7] = (uint8_t)(now);
        g2_ring_out_buf[8] = (uint8_t)(now >> 8);
        g2_ring_out_buf[9] = (uint8_t)(now >> 16);
        g2_ring_out_buf[10] = (uint8_t)(now >> 24);
        /* The notification length comes from g2_ring_report_cb's *data_len (see
         * its header), so stage the 11-byte length on EVERY emission or the
         * glasses would receive the 33-byte max. */
        g2_ring_out_len = G2_REPORT_LEN;
        bool sent = !ble_gatt_characteristic_update(
            profile->svc_handle, &profile->ch_notify_legacy, g2_ring_out_buf);
        if(sent) app->last_report_ms = now; /* only a real send arms the gap */
        furi_mutex_release(app->mutex);
        if(sent) {
            snprintf(line, sizeof(line), "> gst %02X %s SENT", type, tag);
        } else {
            snprintf(line, sizeof(line), "! tx REJECTED %02X", type);
        }
    }
    g2_ring_log(app, line);
    FURI_LOG_I(TAG, "%s", line);
}

/* Accept reply: the ring answers an accepted peer on bae80011 with a battery
 * report 00 09 8B 00 <pct> <charging>. */
static void g2_ring_send_battery(G2RingApp* app) {
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    G2RingProfile* profile = app->profile;
    bool sent = false;
    if(profile && app->svc_ok && app->central_connected && app->glasses_role) {
        memset(g2_ring_out_buf, 0, sizeof(g2_ring_out_buf));
        g2_ring_out_buf[0] = 0x00;
        g2_ring_out_buf[1] = 0x09;
        g2_ring_out_buf[2] = 0x8B;
        g2_ring_out_buf[3] = 0x00;
        g2_ring_out_buf[4] = 0x64; /* 100% */
        g2_ring_out_buf[5] = 0x00; /* not charging */
        g2_ring_out_len = 6;
        sent = !ble_gatt_characteristic_update(
            profile->svc_handle, &profile->ch_notify_legacy, g2_ring_out_buf);
        app->last_report_ms = g2_ring_now_ms();
    }
    furi_mutex_release(app->mutex);
    g2_ring_log(app, sent ? "> bat 0064 SENT" : "! bat not sent");
}

/* ---- deferred single tap (GUI thread) ---------------------------------- */

static void g2_ring_adv_timer_callback(void* context) {
    G2RingApp* app = context;
    view_dispatcher_send_custom_event(app->view_dispatcher, G2RingEventAdvKick);
}

/* GUI thread. Never while connected: stopping advertising then would drop the link. */
static void g2_ring_adv_kick(G2RingApp* app) {
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    bool idle = app->svc_ok && !app->central_connected;
    furi_mutex_release(app->mutex);
    if(!idle) return;
    furi_hal_bt_stop_advertising();
    furi_hal_bt_start_advertising();
    FURI_LOG_D(TAG, "adv kick (fast advertising re-armed)");
}

static void g2_ring_tap_timer_callback(void* context) {
    G2RingApp* app = context;
    /* FuriTimer thread: never touch the GUI/model here -- post an event. */
    view_dispatcher_send_custom_event(app->view_dispatcher, G2RingEventDeferredTap);
}

/* ---- R1 model-frame replies (built on BLE thread, sent on GUI thread) --- */

static uint32_t g2_ring_crc32c(const uint8_t* d, size_t n) {
    /* faceclaw RingProtocol.crc32: poly 0x1EDC6F41, MSB-first, init 0, no xorout. */
    uint32_t crc = 0;
    for(size_t i = 0; i < n; i++) {
        crc ^= (uint32_t)d[i] << 24;
        for(int b = 0; b < 8; b++) crc = (crc & 0x80000000u) ? (crc << 1) ^ 0x1EDC6F41u : (crc << 1);
    }
    return crc;
}

#define RSP_Q 8
#define RSP_MAX 24
static uint8_t g2_ring_rsp_q[RSP_Q][RSP_MAX];
static uint8_t g2_ring_rsp_len[RSP_Q];
static uint8_t g2_ring_rsp_legacy[RSP_Q]; /* 1 = send on bae80011, 0 = bae80013 */
static volatile uint8_t g2_ring_rsp_head, g2_ring_rsp_tail;

static uint16_t g2_ring_modbus(const uint8_t* m, size_t n) {
    /* openR1 firmware_checksum: CRC-16/MODBUS over the whole model with the
     * checksum bytes (model offsets 10,11) taken as zero. */
    uint16_t crc = 0xFFFF;
    for(size_t i = 0; i < n; i++) {
        crc ^= (i == 10 || i == 11) ? 0 : m[i];
        for(int b = 0; b < 8; b++) crc = (crc & 1) ? (uint16_t)((crc >> 1) ^ 0xA001) : (uint16_t)(crc >> 1);
    }
    return crc;
}

/* Ring -> phone reply, exactly as the decompiled R1 firmware builds it
 * (openCFW r1/src/r1_dispatch.c add_response + r1_protocol.c r1_model_encode +
 * r1_fragment_message, commit 832137ec):
 *   fragment: [0]=0 (last fragment) [1..4]=CRC-32C(model) LE
 *   model:    64 <module> 64 <serial LE16 = request serial> <status = 0x03|code<<2>
 *             <cmd> <subcmd> <len LE16 = 12 + payload> <CRC-16/MODBUS LE16> <payload>
 * Our 15-byte "header" is fragment(5) + model header(12)-2... i.e. model starts at 5. */
static void g2_ring_queue_frame(
    G2RingApp* app, uint8_t status, uint8_t module, const uint8_t serial[2],
    uint8_t cmd, uint8_t sub, const uint8_t* payload, size_t plen) {
    uint8_t next = (uint8_t)((g2_ring_rsp_head + 1) % RSP_Q);
    if(next == g2_ring_rsp_tail) {
        FURI_LOG_W(TAG, "rsp queue full");
        return;
    }
    if(plen > RSP_MAX - 17) plen = RSP_MAX - 17;
    uint8_t* f = g2_ring_rsp_q[g2_ring_rsp_head];
    memset(f, 0, RSP_MAX);
    uint8_t* m = &f[5];
    size_t mlen = 12 + plen;
    m[0] = 0x64; m[1] = module; m[2] = 0x64; m[3] = serial[0]; m[4] = serial[1];
    m[5] = status; m[6] = cmd; m[7] = sub;
    m[8] = (uint8_t)mlen; m[9] = (uint8_t)(mlen >> 8);
    if(plen) memcpy(&m[12], payload, plen);
    uint16_t ck = g2_ring_modbus(m, mlen);
    m[10] = (uint8_t)ck; m[11] = (uint8_t)(ck >> 8);
    uint32_t c = g2_ring_crc32c(m, mlen);
    f[0] = 0; f[1] = (uint8_t)c; f[2] = (uint8_t)(c >> 8); f[3] = (uint8_t)(c >> 16); f[4] = (uint8_t)(c >> 24);
    g2_ring_rsp_len[g2_ring_rsp_head] = (uint8_t)(5 + mlen);
    g2_ring_rsp_legacy[g2_ring_rsp_head] = 0;
    g2_ring_rsp_head = next;
    view_dispatcher_send_custom_event(app->view_dispatcher, G2RingEventSendRsp);
}

/* Queue a raw legacy-channel frame (ring -> glasses, notified on bae80011). */
static void g2_ring_queue_legacy(G2RingApp* app, const uint8_t* frame, size_t len) {
    uint8_t next = (uint8_t)((g2_ring_rsp_head + 1) % RSP_Q);
    if(next == g2_ring_rsp_tail || len > RSP_MAX) {
        FURI_LOG_W(TAG, "legacy queue full/too long");
        return;
    }
    memset(g2_ring_rsp_q[g2_ring_rsp_head], 0, RSP_MAX);
    memcpy(g2_ring_rsp_q[g2_ring_rsp_head], frame, len);
    g2_ring_rsp_len[g2_ring_rsp_head] = (uint8_t)len;
    g2_ring_rsp_legacy[g2_ring_rsp_head] = 1;
    g2_ring_rsp_head = next;
    view_dispatcher_send_custom_event(app->view_dispatcher, G2RingEventSendRsp);
}

/* GUI thread: drain queued replies as notifications on bae80013. */
static void g2_ring_send_rsps(G2RingApp* app) {
    while(g2_ring_rsp_tail != g2_ring_rsp_head) {
        uint8_t i = g2_ring_rsp_tail;
        furi_mutex_acquire(app->mutex, FuriWaitForever);
        G2RingProfile* profile = app->profile;
        bool sent = false;
        if(profile && app->svc_ok && app->central_connected) {
            memcpy(g2_ring_out_buf, g2_ring_rsp_q[i], g2_ring_rsp_len[i]);
            g2_ring_out_len = g2_ring_rsp_len[i];
            sent = ble_gatt_characteristic_update(
                profile->svc_handle,
                g2_ring_rsp_legacy[i] ? &profile->ch_notify_legacy : &profile->ch_notify_health,
                g2_ring_out_buf);
        }
        furi_mutex_release(app->mutex);
        static char hx[2 * RSP_MAX + 1];
        hexify(hx, sizeof(hx), g2_ring_rsp_q[i], g2_ring_rsp_len[i]);
        FURI_LOG_I(TAG, "TX k%02X ret=%d %s", g2_ring_rsp_q[i][10], sent, hx);
        static char line[LOG_LINE_LEN];
        snprintf(line, sizeof(line), "> rsp %02X%02X %s", g2_ring_rsp_q[i][11], g2_ring_rsp_q[i][12], sent ? "ok" : "FAIL");
        g2_ring_log(app, line);
        g2_ring_rsp_tail = (uint8_t)((i + 1) % RSP_Q);
    }
}

/* ---- incoming BLE traffic (BLE thread) --------------------------------- */

static void
    g2_ring_handle_write(G2RingApp* app, G2RingProfile* profile, uint16_t handle,
                         const uint8_t* val, size_t vlen) {
    /* Static scratch: this runs on the BLE stack's callback path, whose thread
     * stack is small; a MemManage there is the classic overflow signature. The BLE
     * path is single-threaded, so one shared buffer set is safe. */
    static char hex[27]; /* up to 12 bytes -> 24 chars + NUL */
    static char line[64];
    hexify(hex, sizeof(hex), val, vlen > 12 ? 12 : vlen);
    {
        static char full[2 * 64 + 1];
        hexify(full, sizeof(full), val, vlen > 64 ? 64 : vlen);
        FURI_LOG_I(TAG, "WR h=%u len=%u %s", handle, (unsigned)vlen, full);
    }

    /* (a) A write to the legacy notify CCCD (bae80011's 0x2902) means this link
     * has taken the ring's GLASSES role. That is where gestures go. On the
     * auto-allocated path descriptor_handle is 0, so the CCCD handle is the
     * notify value handle + 1 (computed at profile start as cccd_legacy); the
     * subscription arrives as an ATT write to exactly that handle. */
    if(handle == profile->cccd_legacy) {
        bool enabled = vlen >= 1 && (val[0] & 0x01) != 0;
        g2_ring_set_glasses(app, enabled);
        g2_ring_log(app, enabled ? "! GLASSES role ON" : "! glasses role off");
        if(enabled) {
            view_dispatcher_send_custom_event(app->view_dispatcher, G2RingEventSendBattery);
        }
        return;
    }
    if(handle == profile->cccd_health) {
        g2_ring_log(app, "! health CCCD wr");
        return;
    }

    /* Attr_Handle in ACI_GATT_ATTRIBUTE_MODIFIED is the VALUE handle = decl + 1
     * (ble_svc_serial compares against chars[Rx].handle + 1). */
    if(handle == profile->ch_write_legacy.handle + 1 ||
       handle == profile->ch_write_health.handle + 1) {
        /* (b) Legacy pair-auth bytes written raw on bae80010: accept them. */
        /* Legacy glasses commands on bae80010 (opcode at byte 2). Replies per the
         * G2's own parser RING_CmdPackageParse (openCFW display_ring_send_packet.c):
         * 0x85 -> ring echoes 85 with [3]=1,[4]=1 ("ring touch enable ok"), which
         * makes the glasses publish the ring link ready; 0x88 pair-auth -> battery
         * report 00 09 8B 00 <pct> <chg> (faceclaw: the ring's accept answer). */
        if(handle == profile->ch_write_legacy.handle + 1 && vlen >= 3) {
            if(val[2] == 0x85) {
                static const uint8_t touch_ok[] = {0x00, 0x09, 0x85, 0x01, 0x01, 0x00, 0x00, 0x00};
                g2_ring_queue_legacy(app, touch_ok, sizeof(touch_ok));
                /* Report the ring as worn (RING_CmdWearStatus 0x8C, [4]=1). */
                static const uint8_t worn[] = {0x00, 0x09, 0x8C, 0x00, 0x01, 0x00};
                g2_ring_queue_legacy(app, worn, sizeof(worn));
                g2_ring_set_touch(app);
                g2_ring_log(app, "! 85 touch-enable -> ok");
                return;
            }
            if(val[2] == 0x89) {
                /* The glasses repeat 0x89 every 2.5 s and drop the link after three
                 * misses. Answer with the ring heartbeat 0x94 ([4] not 0x20/0x40 ->
                 * "RING_GlassesHeartbeat recv ok" in RING_CmdPackageParse). */
                static const uint8_t hb[] = {0x00, 0x09, 0x94, 0x00, 0x00, 0x00, 0x00, 0x00};
                g2_ring_queue_legacy(app, hb, sizeof(hb));
                return;
            }
            if(val[2] == 0x88) {
                static const uint8_t bat[] = {0x00, 0x09, 0x8B, 0x00, 0x64, 0x00};
                g2_ring_queue_legacy(app, bat, sizeof(bat));
            }
        }
        if(vlen == 4 && val[0] == 0x00 && val[1] == 0x35 && val[2] == 0x88 && val[3] == 0x00) {
            g2_ring_set_pair_auth(app);
            g2_ring_log(app, "! pairAuth 00358800");
            return;
        }
        /* (c) R1 model frame (faceclaw RingProtocol.kt): FLAG(0) CRC32C_LE(1..4)
         * 0x64(5) CHAN(6) 0x64(7) SEQ(8) 0x00(9) KIND(10) CMD_HI(11) CMD_LO(12)
         * PLEN_LE(13..14) PAYLOAD(15..): payload[0..1] = checksum/nonce, then args.
         * (Earlier builds read CMD from 9/10 -- wrong.) */
        if(vlen >= 15 && val[5] == 0x64 && val[7] == 0x64) {
            uint8_t chan = val[6], seq = val[8], kind = val[10];
            uint8_t cmd_hi = val[11], cmd_lo = val[12];
            if(cmd_hi == 0x00 && cmd_lo == 0x08) {
                g2_ring_set_pair_auth(app);
            } else if(cmd_hi == 0x00 && cmd_lo == 0x07 && vlen >= 19 && val[17] == 2) {
                if(val[18]) g2_ring_set_touch(app);
            }
            snprintf(line, sizeof(line), "< c%u k%u %02X%02X s%u", chan, kind, cmd_hi, cmd_lo, seq);
            g2_ring_log(app, line);
            /* Answer REQ (0) and DATA/SET (2) with an "ok" RSP (kind 0x03) on the
             * same chan/seq/cmd, echoing the 2-byte nonce, like a real ring does
             * per faceclaw's model. ACKs (1) get no answer. */
            /* Answer requests (status bit0 == 0) like r1_dispatch does: success
             * (0x03); pairAuth (0x08) carries result byte 0; others no payload. */
            if((kind & 0x01) == 0) {
                static const uint8_t ok0[] = {0x00};
                bool pair = (cmd_hi == 0x00 && cmd_lo == 0x08);
                g2_ring_queue_frame(app, 0x03, chan, &val[8], cmd_hi, cmd_lo, pair ? ok0 : NULL, pair ? 1 : 0);
            }
            return;
        }
        snprintf(line, sizeof(line), "< wr %04X %s", handle, hex);
        g2_ring_log(app, line);
        return;
    }
    snprintf(line, sizeof(line), "< wr? %04X %s", handle, hex);
    g2_ring_log(app, line);
}

/* HCI UART packet: type(1) then type-specific header. ACL data (0x02):
 * handle(2) len(2) L2CAP{ len(2) cid(2) ATT{ op(1) handle(2) value.. } }. */
static void g2_ring_on_ble_event(G2RingApp* app, G2RingProfile* profile, const uint8_t* pkt) {
    /* The dispatcher hands us an hci_uart_pckt, exactly as ble_svc_serial's handler
     * reads it: type(0x04 event) | hci_event_pckt{evt, plen, data}. GATT writes arrive
     * as evt 0xFF (vendor) with ecode 0x0C01 (ACI_GATT_ATTRIBUTE_MODIFIED), whose
     * rp0 is Connection_Handle(2) Attr_Handle(2) Offset(2) Attr_Data_Length(2) Data.
     * (Earlier builds parsed ACL packets, type 0x02, which never reach this hook, so
     * every write -- including the app's pairAuth -- was silently dropped.) */
    if(pkt[0] != 0x04) return;
    /* Track the link from raw HCI events: the bt-service status callback did not
     * report the glasses' connection (screen said "! no link" while connected). */
    if(pkt[1] == 0x05) { /* Disconnection Complete */
        FURI_LOG_I(TAG, "HCI disconnect reason %02X", pkt[6]);
        g2_ring_set_link(app, false);
        g2_ring_set_glasses(app, false);
        return;
    }
    if(pkt[1] == 0x3E && (pkt[3] == 0x01 || pkt[3] == 0x0A)) { /* LE (Enhanced) Conn Complete */
        FURI_LOG_I(TAG, "HCI LE connect status %02X peer %02X:%02X:%02X:%02X:%02X:%02X", pkt[4],
                   pkt[13], pkt[12], pkt[11], pkt[10], pkt[9], pkt[8]);
        if(pkt[4] == 0x00) g2_ring_set_link(app, true);
        return;
    }
    if(pkt[1] != 0xFF) return;
    uint16_t ecode = (uint16_t)(pkt[3] | ((uint16_t)pkt[4] << 8));
    if(ecode != 0x0C01u) return;
    uint16_t handle = (uint16_t)(pkt[7] | ((uint16_t)pkt[8] << 8));
    uint16_t vlen = (uint16_t)(pkt[11] | ((uint16_t)pkt[12] << 8));
    if(vlen > 244) vlen = 244;
    /* attribute-modified reports the CHARACTERISTIC handle (decl); the value
     * handle is decl+1 for the ST stack. Accept either. */
    g2_ring_handle_write(app, profile, handle, &pkt[13], vlen);
}

static BleEventAckStatus g2_ring_svc_event_cb(void* event, void* context) {
    G2RingProfile* profile = context;
    if(profile && profile->app) {
        g2_ring_on_ble_event(profile->app, profile, (const uint8_t*)event);
    }
    /* Observation only. BleEventNotAck = "not handled, keep dispatching". Returning
     * BleEventAckFlowEnable claimed EVERY event as handled, so gap.c never saw the SMP
     * pairing events: the phone hit SMP_RSP_TIMEOUT and the Flipper log showed no
     * pairing activity at all (hardware run 2026-11-24). */
    return BleEventNotAck;
}

/* ---- chartacteristic setup + profile lifecycle ------------------------- */

static void g2_ring_fill_char(
    BleGattCharacteristicParams* p,
    const char* name,
    const uint8_t uuid[16],
    uint8_t props,
    BleGattCharacteristicDescriptorParams* desc) {
    memset(p, 0, sizeof(*p));
    p->name = name;
    p->descriptor_params = desc;
    p->uuid_type = UUID_TYPE_128BIT;
    memcpy(p->uuid.Char_UUID_128, uuid, 16);
    p->char_properties = props;
    p->security_permissions = 0x00;
    /* Write chars: GATT_NOTIFY_ATTRIBUTE_WRITE (0x01) -- notify the app about a
     * write, no wait. We do NOT use GATT_NOTIFY_WRITE_REQ_AND_WAIT_FOR_APPL_RESP
     * (0x02): it would make the stack wait for a write-response we never issue.
     * See the decision block where these are defined. */
    p->gatt_evt_mask =
        (props & GATT_PROP_NOTIFY) ? GATT_DONT_NOTIFY_EVENTS : GATT_NOTIFY_ATTRIBUTE_WRITE;
    p->is_variable = 1;
    if(props & GATT_PROP_NOTIFY) {
        p->data_prop_type = FlipperGattCharacteristicDataCallback;
        p->data.callback.fn = g2_ring_report_cb;
        p->data.callback.context = NULL;
    } else {
        p->data_prop_type = FlipperGattCharacteristicDataFixed;
        p->data.fixed.ptr = g2_ring_write_scratch;
        p->data.fixed.length = sizeof(g2_ring_write_scratch);
    }
}

static FuriHalBleProfileBase* g2_ring_profile_start(FuriHalBleProfileParams params) {
    G2RingApp* app = params;
    G2RingProfile* profile = malloc(sizeof(G2RingProfile));
    memset(profile, 0, sizeof(G2RingProfile));
    profile->base.config = &g2_ring_profile_template;
    profile->app = app;

    bool svc_ok = true;
    /* Static, NOT a stack local: keep large locals off the (small) BT-service
     * thread stack -- a MemManage on this host is the classic stack-overflow
     * signature. See the STACK FOOTPRINT note at the top of this file. */
    static Service_UUID_t su;
    memset(&su, 0, sizeof(su));
    memcpy(su.Service_UUID_128, r1_uuid_service, sizeof(r1_uuid_service));
    /* Service_Type = GATT_SERVICE_PRIMARY (0x01). The previous build passed
     * 0x00, which is not a defined Service_Type and made the coprocessor reject
     * the whole service add. */
    /* Max_Attribute_Records = 12, matching ble_svc_serial_start EXACTLY: the
     * shipped serial service also declares a 128-bit PRIMARY service with four
     * characteristics and passes 12 (disassembly: movs r2,#1 [Service_Type];
     * movs r3,#12 [Max_Attribute_Records] before the call). 20 was over-sized:
     * the STM32WB GATT server's attribute table is a finite shared resource, and
     * 20 records exceeds what any shipped service requests (serial=12, battery=8)
     * and what this four-characteristic service needs, so it was a needless
     * failure/overrun mode. See the API PARAMETERS note at the top of this file. */
    bool svc_add_ok = ble_gatt_service_add(
        UUID_TYPE_128BIT, &su, GATT_SERVICE_PRIMARY, 12, &profile->svc_handle);
    if(!svc_add_ok) {
        FURI_LOG_E(TAG, "ble_gatt_service_add failed");
        svc_ok = false;
    }

    /* NO explicit 0x2902 CCCD on any characteristic -- descriptor_params = NULL
     * on all four, exactly like every shipped service.
     *
     * EVIDENCE: (1) no BleGattCharacteristicDescriptorParams with uuid 0x2902
     * exists anywhere in firmware.elf (a grep of the whole binary for 0x2902 /
     * "Client Characteristic" returns nothing); (2) ble_svc_serial_chars and
     * ble_svc_battery_chars, decoded from .rodata, have descriptor_params (their
     * +4 word) == 0 for EVERY characteristic. The STM32WB radio auto-allocates the
     * CCCD for a notify/indicate characteristic when it is added, so declaring our
     * own is both redundant and harmful: ble_gatt_characteristic_init takes the
     * descriptor branch when descriptor_params != NULL and calls the descriptor's
     * data_callback.fn unconditionally, which we left NULL -- that indirect call
     * is the MemManage fault we hit on hardware. NULL skips that branch entirely. */
    g2_ring_fill_char(&profile->p_pad, "r1_pad", r1_uuid_pad, GATT_PROP_WRITE_ANY, NULL);
    g2_ring_fill_char(&profile->p_write_legacy, "r1_write_legacy", r1_uuid_write_legacy, GATT_PROP_WRITE_ANY, NULL);
    g2_ring_fill_char(&profile->p_notify_legacy, "r1_notify_legacy", r1_uuid_notify_legacy, GATT_PROP_NOTIFY, NULL);
    g2_ring_fill_char(&profile->p_write_health, "r1_write_health", r1_uuid_write_health, GATT_PROP_WRITE_ANY, NULL);
    g2_ring_fill_char(&profile->p_notify_health, "r1_notify_health", r1_uuid_notify_health, GATT_PROP_NOTIFY, NULL);

    /* Handle pad (2026-11-24): the G2 cached the ring's handles from its first
     * discovery (it keeps writing its CCCD enable to handle 19 and does not
     * rediscover). A 2-handle placeholder first shifts bae80010 to 15/16 and
     * bae80011 to 17/18 with its CCCD at 19, where the glasses write. */
    if(G2_GLASSES_ONLY) ble_gatt_characteristic_init(profile->svc_handle, &profile->p_pad, &profile->ch_pad);
    ble_gatt_characteristic_init(profile->svc_handle, &profile->p_write_legacy, &profile->ch_write_legacy);
    ble_gatt_characteristic_init(profile->svc_handle, &profile->p_notify_legacy, &profile->ch_notify_legacy);
#if !G2_GLASSES_ONLY
    ble_gatt_characteristic_init(profile->svc_handle, &profile->p_write_health, &profile->ch_write_health);
    ble_gatt_characteristic_init(profile->svc_handle, &profile->p_notify_health, &profile->ch_notify_health);
#endif

    /* FIX 3: characteristic creation returns void, so detect failure via the
     * resulting handles. A zero handle means the attribute was not created. */
    if(profile->ch_write_legacy.handle == 0 || profile->ch_notify_legacy.handle == 0 ||
       (!G2_GLASSES_ONLY &&
        (profile->ch_write_health.handle == 0 || profile->ch_notify_health.handle == 0))) {
        FURI_LOG_E(TAG, "characteristic init produced a zero handle");
        svc_ok = false;
    }
    profile->svc_ok = svc_ok;

    /* Derive the CCCD handle for each notify characteristic. ble_gatt_
     * characteristic_init leaves descriptor_handle = 0 on the auto-allocated path
     * (it fills instance.descriptor_handle only when descriptor_params != NULL).
     * The CCCD 0x2902 the STM32WB auto-allocates for a notify char sits
     * immediately after the value attribute, i.e. at (value handle + 1) -- the
     * observed sequence wl=0x13 nl=0x15 wh=0x18 nh=0x20 proves it. Prefer a
     * reported descriptor handle if one ever appears, else use value+1. */
    profile->cccd_legacy = profile->ch_notify_legacy.descriptor_handle
                               ? profile->ch_notify_legacy.descriptor_handle
                               : (uint16_t)(profile->ch_notify_legacy.handle + 2); /* decl+1 = value, decl+2 = CCCD (ST stack; ble_svc_serial uses .handle + 2) */
    profile->cccd_health = profile->ch_notify_health.descriptor_handle
                               ? profile->ch_notify_health.descriptor_handle
                               : (uint16_t)(profile->ch_notify_health.handle + 2);

    FURI_LOG_I(TAG, "layout pad=%u w10=%u n11=%u ccc11=%u", profile->ch_pad.handle,
               profile->ch_write_legacy.handle, profile->ch_notify_legacy.handle, profile->cccd_legacy);
    profile->svc_handler = ble_event_dispatcher_register_svc_handler(g2_ring_svc_event_cb, profile);

    if(app) {
        furi_mutex_acquire(app->mutex, FuriWaitForever);
        app->profile = profile;
        app->svc_ok = svc_ok;
        furi_mutex_release(app->mutex);
    }
    /* ---- fine-grained GATT bring-up diagnostics ------------------------
     * One line per stage so a hardware run NAMES the exact attribute the stack
     * refused instead of a single boolean. A zero handle marks the specific
     * characteristic/descriptor that failed. */
    if(app) {
        /* Static scratch: this runs on the profile-start path (small stack). */
        static char diag[LOG_LINE_LEN];
        snprintf(diag, sizeof(diag), "svcAdd=%d h=%u", (int)svc_add_ok, profile->svc_handle);
        g2_ring_log(app, diag);
        snprintf(
            diag,
            sizeof(diag),
            "wl%u nl%u dl%u",
            profile->ch_write_legacy.handle,
            profile->ch_notify_legacy.handle,
            profile->ch_notify_legacy.descriptor_handle);
        g2_ring_log(app, diag);
        snprintf(
            diag,
            sizeof(diag),
            "wh%u nh%u dh%u",
            profile->ch_write_health.handle,
            profile->ch_notify_health.handle,
            profile->ch_notify_health.descriptor_handle);
        g2_ring_log(app, diag);
        /* The computed CCCD handles. They must be on screen so the glasses-role
         * subscription is unambiguous on the next hardware run. */
        snprintf(
            diag, sizeof(diag), "ccd nl%02X nh%02X", profile->cccd_legacy, profile->cccd_health);
        g2_ring_log(app, diag);
        with_view_model(
            app->view,
            G2RingModel * m,
            {
                m->cccd_legacy = profile->cccd_legacy;
                m->cccd_health = profile->cccd_health;
            },
            true);
    }
    FURI_LOG_I(
        TAG,
        "R1 up: ok=%d add=%d svc=%u wl=%u nl=%u dl=%u wh=%u nh=%u dh=%u",
        (int)svc_ok,
        (int)svc_add_ok,
        profile->svc_handle,
        profile->ch_write_legacy.handle,
        profile->ch_notify_legacy.handle,
        profile->ch_notify_legacy.descriptor_handle,
        profile->ch_write_health.handle,
        profile->ch_notify_health.handle,
        profile->ch_notify_health.descriptor_handle);
    return (FuriHalBleProfileBase*)profile;
}

static void g2_ring_profile_stop(FuriHalBleProfileBase* base) {
    if(base == NULL) return;
    G2RingProfile* profile = (G2RingProfile*)base;
    if(profile->base.config != &g2_ring_profile_template) return; /* not ours */
    if(profile->app) {
        furi_mutex_acquire(profile->app->mutex, FuriWaitForever);
        if(profile->app->profile == profile) profile->app->profile = NULL;
        furi_mutex_release(profile->app->mutex);
    }
    if(profile->svc_handler) {
        ble_event_dispatcher_unregister_svc_handler(profile->svc_handler);
    }
#if !G2_GLASSES_ONLY
    ble_gatt_characteristic_delete(profile->svc_handle, &profile->ch_notify_health);
    ble_gatt_characteristic_delete(profile->svc_handle, &profile->ch_write_health);
#endif
    ble_gatt_characteristic_delete(profile->svc_handle, &profile->ch_notify_legacy);
    ble_gatt_characteristic_delete(profile->svc_handle, &profile->ch_write_legacy);
    if(G2_GLASSES_ONLY) ble_gatt_characteristic_delete(profile->svc_handle, &profile->ch_pad);
    ble_gatt_service_delete(profile->svc_handle);
    free(profile);
}

/* Advertise as a real ring.
 *
 * The advertised NAME tail and the manufacturer-data MAC are built from ONE array
 * (`human`), so they can never disagree -- a peer that maps name<->address (e.g.
 * MentraOS) rejects a device whose advertised MAC does not end in the name's tail.
 *
 * Manufacturer-data structure (from the community + MentraOS R1.kt/R1.swift):
 *   [company id, 2 bytes][public MAC, 6 bytes, human order]
 * gap_init() emits cfg->mfg_data verbatim as the scan response's sole Manufacturer
 * Specific Data AD structure: length byte = mfg_data_len+1, type 0xFF, then the bytes
 * (verified in firmware.elf: gap_init reads cfg+43 mfg_data_len, cfg+20 mfg_data, and
 * gap_advertise_start passes them to hci_le_set_scan_response_data). The 2-byte
 * company id 0x45 0x52 ("ER" little-endian) is UNCONFIRMED against a real ring.
 *
 * The MAC is emitted in HUMAN order = reverse of the wire (little-endian) order that
 * furi_hal_version_get_ble_mac() returns and that gap_init writes verbatim into
 * CONFIG_DATA_PUBADDR. The name is "EVEN R1_" + the LAST THREE octets of that same
 * human-order MAC. (The exact on-air byte order of get_ble_mac() is NOT provable from
 * the headers; see the flagged risk in the report.) */
static void g2_ring_get_gap_config(GapConfig* cfg, FuriHalBleProfileParams params) {
    G2RingApp* app = params;
    memset(cfg, 0, sizeof(GapConfig));

    /* Advertising budget is 31 bytes: flags(3) + name AD(2+14) + 128-bit UUID AD(18)
     * = 37 overflowed it, so the radio refused set_discoverable and nothing was ever on
     * air (Windows/phone scans saw no Flipper while the G2 arms showed up). Advertise the
     * 16-bit Battery service instead (3+16+4 = 23 bytes); the bae80001 service is still
     * hosted in GATT and found by discovery after connect. */
    cfg->adv_service.UUID_Type = UUID_TYPE_16;
    cfg->adv_service.Service_UUID_16 = 0x180F;

    const uint8_t* mac = furi_hal_version_get_ble_mac();
    uint8_t human[6];
    for(size_t i = 0; i < 6; i++) human[i] = mac[5 - i];

    cfg->mfg_data[0] = 0x45; /* company id low  (UNCONFIRMED) */
    cfg->mfg_data[1] = 0x52; /* company id high ("ER" little-endian) */
    memcpy(&cfg->mfg_data[2], human, sizeof(human));
    cfg->mfg_data_len = 8;

    static char name[FURI_HAL_VERSION_DEVICE_NAME_LENGTH];
    snprintf(name, sizeof(name), "EVEN R1_%02X%02X%02X", human[3], human[4], human[5]);

    /* The Flipper's own BLE address in human order -- exactly the address
     * tools/ring_introduce.py hands the glasses as the ring MAC. Built from the
     * SAME `human` array as the advertised name tail, so they cannot disagree. */
    static char mac_str[18];
    snprintf(
        mac_str, sizeof(mac_str), "%02X:%02X:%02X:%02X:%02X:%02X",
        human[0], human[1], human[2], human[3], human[4], human[5]);
    /* Firmware convention (furi_hal_version device_name, serial profile): adv_name[0] is
     * the AD type byte, then the text. gap passes strlen(adv_name) bytes straight in as
     * the Local Name AD, so without the 0x09 prefix the AD type was 'E' (0x45). */
    cfg->adv_name[0] = AD_TYPE_COMPLETE_LOCAL_NAME;
    strncpy(&cfg->adv_name[1], name, sizeof(cfg->adv_name) - 2);
    cfg->adv_name[sizeof(cfg->adv_name) - 1] = '\0';

    cfg->bonding_mode = true;
    cfg->pairing_method = GapPairingNone; /* Just Works: a real R1 has no screen to show a PIN */

    /* Fields the shipped ble_profile_serial_get_config sets that a zeroed config
     * does NOT. Disassembled from firmware.elf at 0x0800152d: it memcpy()s the
     * same 80-byte .rodata template (0x080a1438) and then writes, field for
     * field, cfg+48 = 6 bytes of furi_hal_version_get_ble_mac() (the device MAC)
     * and cfg+54 = the device name. The template it copies already carries
     * appearance_char = 0x8600 (offset 44), bonding_mode = 1 (46),
     * pairing_method = 1 (47) and conn_param = {min 6, max 36} (offsets 72/74).
     * All three of these reach the radio: gap_init writes the MAC via
     * aci_hal_write_config_data(0x30, ...), the appearance via
     * aci_gatt_update_char_value (GAP Appearance char), and
     * gap_verify_connection_parameters reads conn_param at cfg+72/+74 for
     * aci_l2cap_connection_parameter_update_req. A zeroed config therefore
     * advertised a NULL public address (00:00:00:00:00:00) -- the prime suspect
     * for "advertising but never connectable". We now set all three, matching the
     * shipped profile exactly. */
    memcpy(cfg->mac_address, mac, sizeof(cfg->mac_address));
    /* Same value the shipped serial template carries; its meaning for a ring is
     * UNVERIFIED, but the one shipped reference sets it. */
    cfg->appearance_char = 0x8600;
    /* 6/36 (7.5ms..45ms), the shipped template's connection-param request;
     * 0 was below the spec minimum. */
    cfg->conn_param.conn_int_min = 6;
    cfg->conn_param.conn_int_max = 36;

    if(app) {
        with_view_model(
            app->view, G2RingModel * m,
            { snprintf(m->adv_name, sizeof(m->adv_name), "%s", name);
              snprintf(m->ble_mac, sizeof(m->ble_mac), "%s", mac_str); },
            true);
    }
    FURI_LOG_I(TAG, "advertising as '%s'", name);
    /* STARTUP LOG (serial): the ring's own BLE address, readable without a scanner. */
    FURI_LOG_I(TAG, "BLE MAC %s (ring address for tools/ring_introduce.py)", mac_str);
}

static const FuriHalBleProfileTemplate g2_ring_profile_template = {
    .start = g2_ring_profile_start,
    .stop = g2_ring_profile_stop,
    .get_gap_config = g2_ring_get_gap_config,
};

/* ---- notification report callback -------------------------------------- */
/* HOW THE NOTIFICATION LENGTH IS CONVEYED (authoritative, not guessed):
 *
 * sdk_headers/f7_sdk/targets/f7/ble_glue/furi_ble/gatt.h defines
 *   typedef bool (
 *       *cbBleGattCharacteristicData)(const void* context, const uint8_t** data, uint16_t* data_len);
 * with the comments:
 *   "Is called when characteristic is created to get max data length. Data ptr
 *    is NULL in this case; the result is passed to aci_gatt_add_char as
 *    Char_Value_Length"
 *   "For updates, called with a context - see flipper_gatt_characteristic_update"
 * and for ble_gatt_characteristic_update():
 *   "if source==NULL, uses the data from the characteristic: ... For
 *    callback-based data, callback.context is passed as the context if
 *    source==NULL".
 *
 * So for a CALLBACK-based notify char the value length is whatever this
 * callback writes to *data_len -- it is NOT taken from a struct field and NOT
 * from the size of `source`. The header's own discriminator between the two
 * calls is the `data` pointer: NULL at creation (report the maximum the
 * attribute may hold), non-NULL during an update (report this report's length
 * AND its bytes). We therefore STAGE BOTH on every emission:
 *   - g2_ring_out_buf  -- the report bytes (handed back via *data)
 *   - g2_ring_out_len  -- the report length (handed back via *data_len)
 * Both are set in g2_ring_emit()/g2_ring_send_battery() before the update call.
 * We also pass the staged buffer as `source`, so that if an implementation
 * instead treats `source` as the data pointer the bytes are identical anyway. */
static bool g2_ring_report_cb(const void* context, const uint8_t** data, uint16_t* data_len) {
    UNUSED(context);
    if(data == NULL) {
        /* creation-time length probe: declare the max value length */
        if(data_len) *data_len = G2_REPORT_MAX;
        return false;
    }
    /* update-time fetch: hand back the staged report bytes and its length */
    if(data_len) *data_len = g2_ring_out_len;
    *data = g2_ring_out_buf;
    return false; /* we retain ownership; the caller must not free */
}

/* ---- UI (GUI thread) --------------------------------------------------- */

static void g2_ring_draw_callback(Canvas* canvas, void* ctx) {
    G2RingModel* m = ctx;
    canvas_clear(canvas);
    canvas_set_color(canvas, ColorBlack);

    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 9, m->svc_error ? "G2 Ring *SVC ERR*" : "G2 Ring (R1 fake)");
    if(m->central_connected) {
        canvas_draw_disc(canvas, 122, 6, 3);
    } else {
        canvas_draw_circle(canvas, 122, 6, 3);
    }

    canvas_set_font(canvas, FontSecondary);
    char buf[32];
    if(m->svc_error) {
        /* FIX 3: persistent, explicit error state -- we must NOT claim to be a
         * usable ring when the GATT service was never created. */
        canvas_draw_str(canvas, 2, 19, "!! GATT create FAILED");
        canvas_draw_str(canvas, 2, 28, "NOT a usable ring");
    } else {
        snprintf(buf, sizeof(buf), "adv %s", m->adv_name[0] ? m->adv_name : "(none)");
        canvas_draw_str(canvas, 2, 19, buf);
        snprintf(
            buf,
            sizeof(buf),
            "con%c gls%c pair%c tch%c",
            m->central_connected ? 'Y' : '-',
            m->glasses_role ? 'Y' : '-',
            m->pair_auth_seen ? 'Y' : '-',
            m->touch_seen ? 'Y' : '-');
        canvas_draw_str(canvas, 2, 28, buf);
    }
    canvas_draw_line(canvas, 0, 31, 127, 31);
    /* The computed CCCD handles, always on screen so the glasses-role
     * subscription can be matched against an incoming ATT write by eye. */
    snprintf(buf, sizeof(buf), "ccd nl%02X nh%02X", m->cccd_legacy, m->cccd_health);
    canvas_draw_str(canvas, 2, 37, buf);
    /* THE FLIPPER'S OWN ADDRESS: the value the user reads off the screen and passes
     * to tools/ring_introduce.py as the ring MAC, in human order (as a phone shows
     * it). Always visible so no separate BLE scanner app is needed. */
    canvas_draw_str(canvas, 2, 44, m->ble_mac[0] ? m->ble_mac : "mac (unknown)");

    uint8_t n = m->count < 3 ? m->count : 3;
    for(uint8_t i = 0; i < n; i++) {
        uint8_t idx = (uint8_t)((m->head - n + i + LOG_LINES) % LOG_LINES);
        canvas_draw_str(canvas, 2, (int32_t)(50 + i * 6), m->lines[idx]);
    }
}

static bool g2_ring_input_callback(InputEvent* event, void* context) {
    G2RingApp* app = context;

    switch(event->type) {
    case InputTypeShort:
        switch(event->key) {
        /* Sideways layout (Flipper held with the RIGHT key on top). */
        case InputKeyRight:
            g2_ring_emit(app, GESTURE_SCROLL_TOP, "scroll-top");
            return true;
        case InputKeyLeft:
            g2_ring_emit(app, GESTURE_SCROLL_BOTTOM, "scroll-bot");
            return true;
        case InputKeyUp: /* back */
            g2_ring_emit(app, GESTURE_DOUBLE_TAP, "double-tap");
            return true;
        case InputKeyDown: /* select */
            g2_ring_emit(app, GESTURE_TAP, "tap");
            return true;
        case InputKeyOk: /* menu: hold, then release (ring long-press = Menu) */
            if(app->menu_step == 0) {
                g2_ring_emit(app, GESTURE_LONG_PRESS, "menu hold");
                app->menu_step = 2;
                furi_timer_start(app->tap_timer, furi_ms_to_ticks(MENU_HOLD_MS));
            }
            return true;
        default:
            break;
        }
        break;

    case InputTypeLong:
        if(event->key == InputKeyOk) {
            /* A long press supersedes any armed single tap. */
            furi_timer_stop(app->tap_timer);
            app->ok_tap_pending = false;
            app->ok_long_active = true;
            app->menu_step = 0;
            g2_ring_emit(app, GESTURE_LONG_PRESS, "long-press");
            return true;
        }
        break;

    case InputTypeRelease:
        if(event->key == InputKeyOk && app->ok_long_active) {
            app->ok_long_active = false;
            g2_ring_emit(app, GESTURE_LONG_PRESS_RELEASE, "long-rel");
            return true;
        }
        break;

    default:
        break;
    }
    return false;
}

static uint32_t g2_ring_exit_callback(void* context) {
    UNUSED(context);
    return VIEW_NONE;
}

/* Runs on the GUI thread. */
static bool g2_ring_custom_event_callback(void* context, uint32_t event) {
    G2RingApp* app = context;
    if(event == G2RingEventDeferredTap) {
        if(app->menu_step == 1) {
            g2_ring_emit(app, GESTURE_LONG_PRESS, "menu hold");
            app->menu_step = 2;
            furi_timer_start(app->tap_timer, furi_ms_to_ticks(MENU_HOLD_MS));
        } else if(app->menu_step == 2) {
            app->menu_step = 0;
            g2_ring_emit(app, GESTURE_LONG_PRESS_RELEASE, "menu rel");
        } else if(app->ok_tap_pending) { /* still armed -> a real single tap */
            app->ok_tap_pending = false;
            g2_ring_emit(app, GESTURE_TAP, "tap");
        }
        return true;
    }
    if(event == G2RingEventAdvKick) {
        g2_ring_adv_kick(app);
        return true;
    }
    if(event == G2RingEventSendRsp) {
        g2_ring_send_rsps(app);
        return true;
    }
    if(event == G2RingEventSendBattery) {
        g2_ring_send_battery(app);
        return true;
    }
    return false;
}

/* Runs on the BLE thread. */
static void g2_ring_bt_status_callback(BtStatus status, void* context) {
    G2RingApp* app = context;
    bool connected = (status == BtStatusConnected);
    g2_ring_set_link(app, connected);
    if(connected) {
        app->saw_connection = true;
        g2_ring_log(app, "! central connected");
        return;
    }
    g2_ring_set_glasses(app, false); /* the role is per-link */
    if(app->saw_connection) {
        /* A real link was up and has now dropped: this is a genuine loss. */
        app->saw_connection = false;
        g2_ring_log(app, "! central gone");
    } else {
        /* SPURIOUS-STARTUP GUARD. bt_service invokes this callback on every
         * status change, and gap.c emits GapEventTypeStartAdvertising when the
         * radio begins advertising -- i.e. a status change fires BEFORE any
         * central has ever connected. Printing "central gone" here claimed a link
         * was lost when none ever existed. Name the real status instead. */
        char b[LOG_LINE_LEN];
        snprintf(b, sizeof(b), "bt st%d", (int)status);
        g2_ring_log(app, b);
    }
}

/* ---- entry point ------------------------------------------------------- */

int32_t g2_ring_app(void* p) {
    UNUSED(p);
    G2RingApp* app = malloc(sizeof(G2RingApp));
    memset(app, 0, sizeof(G2RingApp));
    app->mutex = furi_mutex_alloc(FuriMutexTypeNormal);

    app->gui = furi_record_open(RECORD_GUI);
    app->view = view_alloc();
    view_allocate_model(app->view, ViewModelTypeLocking, sizeof(G2RingModel));
    view_set_context(app->view, app);
    view_set_draw_callback(app->view, g2_ring_draw_callback);
    view_set_input_callback(app->view, g2_ring_input_callback);
    view_set_previous_callback(app->view, g2_ring_exit_callback);

    app->view_dispatcher = view_dispatcher_alloc();
    view_dispatcher_set_event_callback_context(app->view_dispatcher, app);
    view_dispatcher_set_custom_event_callback(app->view_dispatcher, g2_ring_custom_event_callback);
    view_dispatcher_add_view(app->view_dispatcher, APP_VIEW_MAIN, app->view);
    view_dispatcher_attach_to_gui(app->view_dispatcher, app->gui, ViewDispatcherTypeFullscreen);
    view_dispatcher_switch_to_view(app->view_dispatcher, APP_VIEW_MAIN);

    app->tap_timer = furi_timer_alloc(g2_ring_tap_timer_callback, FuriTimerTypeOnce, app);
    app->adv_timer = furi_timer_alloc(g2_ring_adv_timer_callback, FuriTimerTypePeriodic, app);

    /* Take BLE over with our R1 impersonation profile. */
    app->bt = furi_record_open(RECORD_BT);
    bt_disconnect(app->bt);
    furi_delay_ms(100);
    bt_set_status_changed_callback(app->bt, g2_ring_bt_status_callback, app);

    FuriHalBleProfileBase* base = bt_profile_start(app->bt, &g2_ring_profile_template, app);
    bool svc_ok = false;
    if(base != NULL) {
        furi_mutex_acquire(app->mutex, FuriWaitForever);
        svc_ok = app->svc_ok;
        furi_mutex_release(app->mutex);
    }
    if(base == NULL) {
        FURI_LOG_E(TAG, "bt_profile_start failed; staying on the default profile");
        g2_ring_set_svc_error(app);
        g2_ring_log(app, "! profile start FAILED");
    } else if(!svc_ok) {
        FURI_LOG_E(TAG, "GATT service not created; not a usable ring");
        g2_ring_set_svc_error(app);
        g2_ring_log(app, "! GATT create FAILED");
    } else {
        g2_ring_log(app, "! advertising as R1");
        /* STARTUP LOG (screen): the Flipper's own BLE address, the ring MAC the user
         * passes to tools/ring_introduce.py. Set by g2_ring_get_gap_config(), which
         * has already run inside bt_profile_start() above. */
        char macline[LOG_LINE_LEN];
        bool have_mac = false;
        with_view_model(
            app->view, G2RingModel * m,
            {
                if(m->ble_mac[0]) {
                    snprintf(macline, sizeof(macline), "mac %s", m->ble_mac);
                    have_mac = true;
                }
            },
            false);
        if(have_mac) g2_ring_log(app, macline);
    }

    furi_timer_start(app->adv_timer, furi_ms_to_ticks(ADV_KICK_MS));
    view_dispatcher_run(app->view_dispatcher);

    /* Teardown: stop+free timers BEFORE freeing the dispatcher/app, drop the
     * pending tap, then hand BLE back to the default profile cleanly. */
    furi_timer_stop(app->tap_timer);
    furi_timer_free(app->tap_timer);
    furi_timer_stop(app->adv_timer);
    furi_timer_free(app->adv_timer);
    app->ok_tap_pending = false;
    app->ok_long_active = false;

    bt_set_status_changed_callback(app->bt, NULL, NULL);
    bt_disconnect(app->bt);
    furi_delay_ms(100);
    bt_profile_restore_default(app->bt); /* invokes our stop() -> frees profile */
    furi_record_close(RECORD_BT);

    view_dispatcher_remove_view(app->view_dispatcher, APP_VIEW_MAIN);
    view_dispatcher_free(app->view_dispatcher);
    view_free(app->view);
    furi_record_close(RECORD_GUI);
    furi_mutex_free(app->mutex);
    free(app);
    return 0;
}
