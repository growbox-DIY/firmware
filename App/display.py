import framebuf
import uasyncio as asyncio
import utime
from machine import Pin, I2C

import esp_config
import ssd1306

_oled = None
_state_provider = None
_enabled = False
_button_led = None

# OTA update: instead of the dashboard refreshed every second, a static
# screen (RECEIVING / INSTALLING / VERIFYING). None = normal dashboard.
_update_phase = None
_update_ticks = 0
# upload cut off without discard (e.g. deploy.py died) - back to the dashboard
UPDATE_IDLE_MS = 120 * 1000

# Board startup: static screen with a sprout, stage (WIFI, CLOCK...) and progress
# bar. None = startup finished (finish_boot).
_boot_step = "STARTING"
_boot_progress = 0


def init():
    global _oled
    global _enabled
    global _button_led

    i2c = I2C(
        1,
        scl=Pin(esp_config.PINS["oled_scl"]),
        sda=Pin(esp_config.PINS["oled_sda"]),
        freq=400000
    )

    # without a display the constructor raises OSError (ENODEV) on the first
    # I2C command - startup must continue, _oled stays None
    try:
        _oled = ssd1306.SSD1306_I2C(
            128,
            64,
            i2c,
            addr=0x3C
        )
    except OSError as e:
        _oled = None
        print("Display: not found:", repr(e))

    _button_led = Pin(
        esp_config.PINS["lcd_button_led"],
        Pin.OUT,
        value=0
    )

    _enabled = True
    _button_led.value(1)

    _redraw()


def set_state_provider(provider):
    global _state_provider

    _state_provider = provider


def boot_step(step, progress):
    """Startup stage (max 16 chars) and progress 0-100; nothing after finish_boot."""
    global _boot_step
    global _boot_progress

    if _boot_step is None:
        return

    _boot_step = str(step)[:16]
    _boot_progress = max(0, min(100, progress))
    _redraw()


def finish_boot():
    global _boot_step

    _boot_step = None
    _redraw()


def text(message, x=0, y=0, clear=True):
    # disabled LCD stays dark, dashboard takes priority
    if _oled is None or not _enabled:
        return

    if clear:
        _oled.fill(0)

    _oled.text(
        str(message),
        x,
        y
    )

    _oled.show()


def clear():
    if _oled is None:
        return

    _oled.fill(0)


def clear_buffer():
    if _oled is None:
        return

    _oled.fill(0)


def show():
    if _oled is None:
        return

    _oled.show()


def is_enabled():
    return _enabled


def set_enabled(enabled):
    global _enabled

    _enabled = enabled

    if _button_led is not None:
        _button_led.value(
            1 if enabled else 0
        )

    _redraw()


def _redraw():
    # order: board startup, OTA update, dashboard
    if _oled is None:
        return

    if not _enabled:
        _oled.fill(0)
        _oled.show()
    elif _boot_step:
        _draw_boot()
    elif _update_phase:
        _draw_update()
    else:
        render_dashboard()


def _draw_centered(message, y):
    _oled.text(message, (128 - 8 * len(message)) // 2, y)


def _draw_boot():
    o = _oled
    o.fill(0)

    # icon: sprout in a pot
    o.fill_rect(63, 7, 3, 19, 1)
    o.ellipse(53, 14, 10, 3, 1, True)
    o.ellipse(75, 9, 10, 3, 1, True)
    o.fill_rect(51, 25, 26, 3, 1)

    for i in range(6):
        o.hline(54 + i // 2, 28 + i, 20 - (i // 2) * 2, 1)

    _draw_centered("BOX PANEL", 40)
    _draw_centered(_boot_step, 52)

    # progress bar at the bottom
    o.rect(0, 61, 128, 3, 1)
    o.fill_rect(0, 61, 128 * _boot_progress // 100, 3, 1)
    o.show()


def _draw_update():
    o = _oled
    o.fill(0)

    # icon: down arrow into a tray
    o.fill_rect(62, 2, 5, 14, 1)

    for i in range(9):
        o.hline(56 + i, 16 + i, 17 - 2 * i, 1)

    o.fill_rect(48, 22, 3, 10, 1)
    o.fill_rect(77, 22, 3, 10, 1)
    o.fill_rect(48, 30, 32, 3, 1)

    _draw_centered("UPDATING", 40)
    _draw_centered(_update_phase, 52)
    o.show()


def show_update(phase):
    """Static update screen; called on every OTA step."""
    global _update_phase
    global _update_ticks

    _update_ticks = utime.ticks_ms()

    if phase == _update_phase:
        return

    _update_phase = phase

    # disabled LCD stays dark - light during the dark period
    _redraw()


def end_update():
    global _update_phase

    if _update_phase is None:
        return

    _update_phase = None
    _redraw()


# Dashboard 128x64:
#   [WiFi] MANUAL / AUTO W3        [SD] 21:34
#   ------------------------------------------
#   [therm] 23.5C           [drop] 45%         <- digits 2x
#   ------------------------------------------
#   [sun/moon] ON           [fan] 40%

_glyph = None


def _big_text(message, x, y):
    """Text in 8x8 font scaled 2x (16 px per char)."""
    global _glyph

    if _glyph is None:
        # one fixed buffer per char - no garbage every second
        _glyph = framebuf.FrameBuffer(bytearray(8), 8, 8, framebuf.MONO_HLSB)

    for ch in message:
        _glyph.fill(0)
        _glyph.text(ch, 0, 0, 1)

        for gy in range(8):
            for gx in range(8):
                if _glyph.pixel(gx, gy):
                    _oled.fill_rect(x + 2 * gx, y + 2 * gy, 2, 2, 1)

        x += 16


def _wifi_icon(x, y, connected, rssi):
    if not connected:
        _oled.line(x, y + 1, x + 6, y + 7, 1)
        _oled.line(x, y + 7, x + 6, y + 1, 1)
        return

    bars = 1

    if rssi is not None:
        bars = 4 if rssi >= -60 else 3 if rssi >= -70 else 2 if rssi >= -80 else 1

    for i in range(4):
        h = 2 * i + 2

        if i < bars:
            _oled.fill_rect(x + 3 * i, y + 8 - h, 2, h, 1)
        else:
            _oled.pixel(x + 3 * i, y + 7, 1)


def _sd_icon(x, y):
    _oled.rect(x, y + 1, 6, 7, 1)
    _oled.fill_rect(x, y, 4, 2, 1)


def _thermometer(x, y):
    _oled.rect(x + 1, y, 3, 11, 1)
    _oled.ellipse(x + 2, y + 12, 2, 2, 1, True)


def _drop(x, y):
    _oled.ellipse(x + 3, y + 10, 3, 3, 1, True)
    _oled.line(x + 3, y, x, y + 9, 1)
    _oled.line(x + 3, y, x + 6, y + 9, 1)
    _oled.line(x + 3, y + 1, x + 3, y + 8, 1)
    _oled.line(x + 2, y + 4, x + 2, y + 8, 1)
    _oled.line(x + 4, y + 4, x + 4, y + 8, 1)


def _sun(cx, cy):
    _oled.ellipse(cx, cy, 3, 3, 1, True)

    for dx, dy in ((0, -6), (0, 6), (-6, 0), (6, 0)):
        _oled.line(cx + dx // 2 + (dx > 0) - (dx < 0), cy + dy // 2 + (dy > 0) - (dy < 0), cx + dx, cy + dy, 1)

    for dx, dy in ((-4, -4), (4, -4), (-4, 4), (4, 4)):
        _oled.pixel(cx + dx, cy + dy, 1)
        _oled.pixel(cx + dx + (1 if dx > 0 else -1), cy + dy + (1 if dy > 0 else -1), 1)


def _moon(cx, cy):
    _oled.ellipse(cx, cy, 5, 5, 1, True)
    _oled.ellipse(cx + 3, cy - 2, 4, 4, 0, True)


def _fan(cx, cy, on):
    _oled.ellipse(cx, cy, 6, 6, 1)

    if on:
        # fan icon: four blades offset around the axis
        for dx, dy, rx, ry in (
                (-1, -3, 2, 1), (3, -1, 1, 2), (1, 3, 2, 1), (-3, 1, 1, 2)
        ):
            _oled.ellipse(cx + dx, cy + dy, rx, ry, 1, True)

        _oled.pixel(cx, cy, 1)
    else:
        _oled.pixel(cx, cy, 1)


def _flowering_week(start, now):
    try:
        year, month, day = [int(x) for x in start.split("-")]
        start_ts = utime.mktime((year, month, day, 0, 0, 0, 0, 0))
        week = (utime.mktime(now) - start_ts) // 86400 // 7 + 1
        return week if week > 0 else None
    except Exception:
        return None


def render_dashboard():
    if _oled is None or _state_provider is None:
        return

    if not _enabled or _update_phase or _boot_step:
        return

    state = _state_provider()
    now = utime.localtime()
    o = _oled
    o.fill(0)

    # status bar
    _wifi_icon(0, 0, state["wifi"], state.get("rssi"))

    mode = state["mode"]
    week = None

    if mode == "AUTO" and state.get("floweringStartDate"):
        week = _flowering_week(state["floweringStartDate"], now)

    o.text(mode + (" W%d" % week if week else ""), 14, 1)

    if state.get("sd"):
        _sd_icon(80, 0)

    o.text(
        "%02d%s%02d" % (now[3], ":" if now[5] % 2 == 0 else " ", now[4]),
        88, 1
    )
    o.hline(0, 11, 128, 1)

    # climate
    temperature = state.get("temperature")
    humidity = state.get("humidity")

    _thermometer(0, 16)

    if isinstance(temperature, (int, float)):
        whole = int(temperature)
        _big_text(str(whole), 8, 16)
        x = 8 + 16 * len(str(whole))
        # degrees at top, tenths at bottom (big-digit baseline)
        o.ellipse(x + 2, 18, 1, 1, 1)
        o.text("C", x + 5, 16)
        o.text(".%d" % (int(round(temperature * 10)) % 10), x, 24)
    else:
        _big_text("--", 8, 16)

    _drop(68, 16)

    if isinstance(humidity, (int, float)):
        text = str(int(round(humidity)))
        _big_text(text, 78, 16)
        o.text("%", 78 + 16 * len(text), 24)
    else:
        _big_text("--", 78, 16)

    o.hline(0, 38, 128, 1)

    # devices
    if state["light"]:
        _sun(7, 51)
        o.text("ON", 18, 48)
    else:
        _moon(7, 51)
        o.text("OFF", 18, 48)

    _fan(72, 51, state["fan"])
    o.text(
        ("%d%%" % state["fanLevel"]) if state["fan"] else "OFF",
        82, 48
    )

    o.show()


def refresh():
    render_dashboard()


def toggle(enabled):
    set_enabled(enabled)


async def display_updater():
    while True:
        try:
            if (
                    _update_phase == "RECEIVING"
                    and utime.ticks_diff(utime.ticks_ms(), _update_ticks)
                    > UPDATE_IDLE_MS
            ):
                print("Display: update idle -> dashboard")
                end_update()

            if _enabled:
                render_dashboard()

        except Exception as e:
            print(
                "Display loop error:",
                e
            )

        await asyncio.sleep_ms(1000)
