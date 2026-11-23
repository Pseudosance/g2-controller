#include <furi.h>
#include <furi_hal.h>

#include <bt/bt_service/bt.h>
#include <profiles/serial_profile.h>

#include <gui/gui.h>
#include <gui/view.h>
#include <gui/view_dispatcher.h>
#include <notification/notification_messages.h>

#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define TAG "G2Controller"

#define APP_VIEW_MAIN 1

#define PING_PERIOD_MS 2000
#define RELAY_TIMEOUT_MS 7000
#define LINE_MAX 64

typedef struct {
    Gui* gui;
    ViewDispatcher* view_dispatcher;
    View* view;
    NotificationApp* notifications;

    Bt* bt;
    FuriHalBleProfileBase* serial_profile;
    bool central_connected; // PC/smartphone connected to Flipper over BLE

    FuriTimer* ping_timer;
    uint32_t last_pong_ms;
    bool relay_up;
    bool glasses_up;
    bool running;

    char rx_line[LINE_MAX];
    size_t rx_len;
} G2App;

typedef struct {
    bool relay_up;
    bool glasses_up;
    char page[32];
    char info[40];
    char evt[32];
    char last_tx[20];
} G2Model;

enum {
    G2EventRxLine = 1,
    G2EventTimerTick,
};

static uint32_t g2_now_ms(void) {
    return furi_get_tick() * 1000 / furi_kernel_get_tick_frequency();
}

static void g2_tx(G2App* app, const char* line) {
    char buf[LINE_MAX];
    int n = snprintf(buf, sizeof(buf), "%s\n", line);
    if(n <= 0 || (size_t)n >= sizeof(buf)) return;

    bool sent = false;
    if(app->serial_profile && app->central_connected) {
        for(int i = 0; i < 5 && !sent; i++) {
            sent = ble_profile_serial_tx(app->serial_profile, (uint8_t*)buf, (uint16_t)n);
            if(!sent) furi_delay_ms(10);
        }
    }

    with_view_model(
        app->view, G2Model * model,
        { snprintf(model->last_tx, sizeof(model->last_tx), sent ? "%s" : "!%s", line); },
        true);

    if(sent) {
        notification_message(app->notifications, &sequence_blink_blue_10);
    } else {
        notification_message(app->notifications, &sequence_blink_red_10);
    }
}

// Runs on the BT service thread: only touch the model + queue a custom event.
static void g2_handle_line(G2App* app, char* line) {
    if(line[0] == '\0') return;

    if(strcmp(line, "PONG") == 0) {
        app->last_pong_ms = g2_now_ms();
        app->relay_up = true;
    } else if(strcmp(line, "LINK 1") == 0) {
        app->glasses_up = true;
    } else if(strcmp(line, "LINK 0") == 0) {
        app->glasses_up = false;
    }

    with_view_model(
        app->view, G2Model * model,
        {
            model->relay_up = app->relay_up;
            model->glasses_up = app->glasses_up;
            if(strncmp(line, "PAGE ", 5) == 0) {
                snprintf(model->page, sizeof(model->page), "%s", line + 5);
            } else if(strncmp(line, "INFO ", 5) == 0) {
                snprintf(model->info, sizeof(model->info), "%s", line + 5);
            } else if(strncmp(line, "EVT ", 4) == 0) {
                snprintf(model->evt, sizeof(model->evt), "%s", line + 4);
            } else if(strncmp(line, "ERR ", 4) == 0) {
                snprintf(model->info, sizeof(model->info), "ERR %s", line + 4);
            }
        },
        true);
    view_dispatcher_send_custom_event(app->view_dispatcher, G2EventRxLine);
}

static uint16_t g2_serial_callback(SerialServiceEvent event, void* context) {
    G2App* app = context;
    uint16_t consumed = 0;

    switch(event.event) {
    case SerialServiceEventTypeDataReceived:
        consumed = event.data.size;
        for(uint16_t i = 0; i < event.data.size; i++) {
            char c = (char)event.data.buffer[i];
            if(c == '\r') continue;
            if(c == '\n') {
                app->rx_line[app->rx_len] = '\0';
                g2_handle_line(app, app->rx_line);
                app->rx_len = 0;
            } else if(app->rx_len < LINE_MAX - 1) {
                app->rx_line[app->rx_len++] = c;
            } else {
                app->rx_len = 0; // runaway line, drop buffer
            }
        }
        break;
    case SerialServiceEventTypeDataSent:
        break;
    case SerialServiceEventTypesBleResetRequest:
        consumed = 0;
        break;
    default:
        break;
    }
    return consumed;
}

static void g2_bt_status_callback(BtStatus status, void* context) {
    G2App* app = context;
    app->central_connected = (status == BtStatusConnected);
    if(!app->central_connected) {
        app->relay_up = false;
        app->rx_len = 0;
    }
    with_view_model(
        app->view, G2Model * model, { model->relay_up = app->relay_up; }, true);
    view_dispatcher_send_custom_event(app->view_dispatcher, G2EventRxLine);
}

static void g2_ping_timer_callback(void* context) {
    G2App* app = context;
    if(!app->running) return;

    if(app->central_connected) {
        char buf[8];
        int n = snprintf(buf, sizeof(buf), "PING\n");
        ble_profile_serial_tx(app->serial_profile, (uint8_t*)buf, (uint16_t)n);
    }

    bool relay_now = app->central_connected &&
                     (g2_now_ms() - app->last_pong_ms < RELAY_TIMEOUT_MS);
    if(relay_now != app->relay_up) {
        app->relay_up = relay_now;
        with_view_model(
            app->view, G2Model * model, { model->relay_up = relay_now; }, true);
    }
    view_dispatcher_send_custom_event(app->view_dispatcher, G2EventTimerTick);
}

static void g2_draw_callback(Canvas* canvas, void* ctx) {
    G2Model* model = ctx;
    canvas_clear(canvas);

    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 4, 2, "G2 Relay");

    canvas_set_font(canvas, FontSecondary);
    if(model->relay_up) {
        canvas_draw_disc(canvas, 118, 5, 3);
    } else {
        canvas_draw_circle(canvas, 118, 5, 3);
    }
    canvas_draw_str(canvas, 4, 14,
                    model->relay_up ? "PC relay: linked" : "PC relay: waiting...");
    canvas_draw_str(canvas, 4, 24,
                    model->glasses_up ? "Glasses: linked" : "Glasses: not linked");
    canvas_draw_line(canvas, 2, 32, 126, 32);
    canvas_draw_str(canvas, 4, 35, model->page[0] ? model->page : "no page shown");
    canvas_draw_str(canvas, 4, 45, model->info[0] ? model->info : "U/D page  R list");
    canvas_draw_str(canvas, 4, 55,
                    model->evt[0] ? model->evt : "OK send  OK-hold status");
}

static bool g2_input_callback(InputEvent* event, void* context) {
    G2App* app = context;

    if(event->type == InputTypeLong && event->key == InputKeyOk) {
        g2_tx(app, "STATUS");
        return true;
    }
    if(event->type == InputTypeShort || event->type == InputTypeRepeat) {
        switch(event->key) {
        case InputKeyUp:
            g2_tx(app, "PAGE prev");
            return true;
        case InputKeyDown:
            g2_tx(app, "PAGE next");
            return true;
        case InputKeyRight:
            g2_tx(app, "LIST");
            return true;
        case InputKeyOk:
            g2_tx(app, "SHOW");
            return true;
        case InputKeyBack:
            // fall through to view_dispatcher exit
            return false;
        default:
            break;
        }
    }
    return false;
}

static uint32_t g2_exit_callback(void* context) {
    UNUSED(context);
    return VIEW_NONE;
}

int32_t g2_controller_main(void* p) {
    UNUSED(p);
    G2App* app = malloc(sizeof(G2App));
    memset(app, 0, sizeof(G2App));

    app->gui = furi_record_open(RECORD_GUI);
    app->notifications = furi_record_open(RECORD_NOTIFICATION);
    app->bt = furi_record_open(RECORD_BT);

    app->view = view_alloc();
    view_allocate_model(app->view, ViewModelTypeLocking, sizeof(G2Model));
    view_set_context(app->view, app);
    view_set_draw_callback(app->view, g2_draw_callback);
    view_set_input_callback(app->view, g2_input_callback);
    view_set_previous_callback(app->view, g2_exit_callback);

    app->view_dispatcher = view_dispatcher_alloc();
    view_dispatcher_add_view(app->view_dispatcher, APP_VIEW_MAIN, app->view);
    view_dispatcher_attach_to_gui(app->view_dispatcher, app->gui,
                                  ViewDispatcherTypeFullscreen);
    view_dispatcher_switch_to_view(app->view_dispatcher, APP_VIEW_MAIN);

    // Switch BLE into the Serial GATT profile so the PC relay can connect.
    bt_disconnect(app->bt);
    furi_delay_ms(100);
    bt_set_status_changed_callback(app->bt, g2_bt_status_callback, app);
    app->serial_profile = bt_profile_start(app->bt, ble_profile_serial, NULL);
    if(app->serial_profile) {
        ble_profile_serial_set_event_callback(
            app->serial_profile, 512, g2_serial_callback, app);
    } else {
        FURI_LOG_E(TAG, "bt_profile_start(ble_profile_serial) failed");
    }

    app->running = true;
    app->ping_timer =
        furi_timer_alloc(g2_ping_timer_callback, FuriTimerTypePeriodic, app);
    furi_timer_start(app->ping_timer, furi_ms_to_ticks(PING_PERIOD_MS));
    app->last_pong_ms = g2_now_ms();

    view_dispatcher_run(app->view_dispatcher);

    app->running = false;
    furi_timer_stop(app->ping_timer);
    furi_timer_free(app->ping_timer);

    // Say bye (best effort), then hand BLE back to the default profile.
    g2_tx(app, "BYE");
    furi_delay_ms(50);
    bt_set_status_changed_callback(app->bt, NULL, NULL);
    bt_disconnect(app->bt);
    furi_delay_ms(100);
    bt_profile_restore_default(app->bt);

    view_dispatcher_remove_view(app->view_dispatcher, APP_VIEW_MAIN);
    view_dispatcher_free(app->view_dispatcher);
    view_free(app->view);

    furi_record_close(RECORD_BT);
    furi_record_close(RECORD_NOTIFICATION);
    furi_record_close(RECORD_GUI);
    free(app);
    return 0;
}
