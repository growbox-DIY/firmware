import gc
import sys

import machine
import uasyncio as asyncio
import utime

import button
import config_store
import display
import esp_config
import events
import humidifier
import ota
import sd_logger
import sensor
import server
from ntp import ntp_task, sync_ntp
from server import (
    clock_scheduler,
    get_display_state,
    scheduler_age_ms,
    start_server,
)
from wifi import connect_wifi, wifi_watchdog

# --- Watchdog ---

# Hardware watchdog: if not "fed" for this many ms,
# the ESP resets itself. Note: also active after Ctrl-C in the REPL
# (e.g. mpremote / PyCharm) - then the ESP restarts after this time.
WDT_ENABLED = True
WDT_TIMEOUT_MS = 60000

# Feed only when the day/night scheduler ran recently
# (a pass every 30 s). Stuck longer -> stop feeding -> reset.
SCHEDULER_MAX_AGE_MS = 120000

# Restart the asyncio loop without resetting the board; after this many crashes
# within the time window - full reset.
MAX_LOOP_CRASHES = 3
LOOP_CRASH_WINDOW_MS = 10 * 60 * 1000

# Safety net: when the GC heap grows at the cost of the ESP-IDF heap,
# the memory does not come back - WiFi/lwIP have no buffers and the server hangs,
# while the WDT does not see it (scheduler runs). This many checks (every 5 s) below
# the threshold = reset.
IDF_MIN_FREE = 8000
IDF_LOW_CHECKS = 3

_wdt = None


def toggle_lcd():
    enabled = not display.is_enabled()

    display.toggle(enabled)

    # config.json is the state the app sees - without saving, the button
    # turned the LCD off while the switch in the app still showed ON
    server.config["display"]["enabled"] = enabled
    config_store.save(server.config)

    print("Display (button):", "ON" if enabled else "OFF")


lcd_button = button.Button(
    pin=esp_config.PINS["lcd_button"],
    on_release=toggle_lcd,
)


async def watchdog_task():
    global _wdt

    # enabled only here: startup (WiFi, NTP, SD) may take longer
    if _wdt is None:
        _wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
        print("WDT: enabled,", WDT_TIMEOUT_MS, "ms")

    warned = False
    idf_low = 0

    while True:
        idf_free = sd_logger.mem_info()[1]

        # -1 = could not be read, not a reason to reset
        if 0 <= idf_free < IDF_MIN_FREE:
            idf_low += 1
            print("MEM: ESP-IDF heap low:", idf_free, "B")
        else:
            idf_low = 0

        if idf_low >= IDF_LOW_CHECKS:
            print("MEM: ESP-IDF heap exhausted -> machine.reset()")
            events.log("reset: ESP-IDF heap exhausted")
            utime.sleep_ms(500)
            machine.reset()

        if scheduler_age_ms() < SCHEDULER_MAX_AGE_MS:
            _wdt.feed()
            warned = False
        elif not warned:
            print("WDT: scheduler stalled, not feeding -> reset soon")
            warned = True

        await asyncio.sleep(5)


async def supervise(name, factory):
    """
    Runs a task and restarts it after a crash, so that an error in e.g. the logger
    or HTTP does not stop the rest (above all the scheduler).
    """
    delay = 5

    while True:
        started = utime.ticks_ms()

        try:
            await factory()
            print("TASK:", name, "ended")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print("TASK:", name, "crashed:", repr(e))
            sys.print_exception(e)

        # ran long without error -> short pause before restart again
        if utime.ticks_diff(utime.ticks_ms(), started) > 10 * 60 * 1000:
            delay = 5

        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


async def ota_confirm_task():
    # no supervise: after confirmation the task should just end
    try:
        await ota.confirm_task(lambda: server._server is not None)
    except Exception as e:
        print("OTA: confirm error:", repr(e))

    # confirmed (or there was no update) - back to the dashboard
    display.end_update()


async def run_tasks():
    # fan on from startup (state from config.json) - dimmer kick-start
    if server.fan_relay.get_state():
        server._dimmer.kick()

    tasks = [
        # most important first: scheduler and watchdog
        supervise("scheduler", clock_scheduler),
    ]

    if WDT_ENABLED:
        tasks.append(supervise("watchdog", watchdog_task))

    await asyncio.gather(
        *tasks,
        supervise("wifi", wifi_watchdog),
        supervise("ntp", ntp_task),
        supervise("server", start_server),
        supervise("display", display.display_updater),
        supervise("sensor", sensor.sensor_task),
        supervise("logger", sd_logger.task),
        supervise("humidifier", lambda: humidifier.link_task(server.config)),
        ota_confirm_task(),
    )


async def boot():
    # cause of the last restart (WDT, reset...). MicroPython shows brownout
    # as POWERON - only the bootloader log on UART tells them apart
    # ("rst:0xf (BROWNOUT_RST)").
    causes = {
        machine.PWRON_RESET: "POWERON",
        machine.HARD_RESET: "HARD",
        machine.WDT_RESET: "WDT",
        machine.DEEPSLEEP_RESET: "DEEPSLEEP",
        machine.SOFT_RESET: "SOFT",
    }
    cause = machine.reset_cause()
    print("BOOT: reset cause", causes.get(cause, cause))

    server.boot_info["reset_cause"] = causes.get(cause, str(cause))
    events.log("boot, reset cause " + server.boot_info["reset_cause"])

    display.init()

    display.set_state_provider(
        get_display_state
    )

    # disabled LCD stays dark from startup (init() always turns it on)
    display.toggle(
        bool(server.config["display"]["enabled"])
    )

    # new version after OTA waits for confirmation (ota_confirm_task)
    if (ota.load_state() or {}).get("pending"):
        display.show_update("VERIFYING")

    sensor.init()

    display.boot_step("WIFI", 15)
    wlan = await connect_wifi()

    if wlan:
        # show the address briefly - useful for the app and deploy.py
        display.boot_step(wlan.ifconfig()[0], 40)
        await asyncio.sleep(1)
    else:
        display.boot_step("NO WIFI", 40)

    display.boot_step("CLOCK", 55)

    if not await sync_ntp():
        display.boot_step("NO CLOCK", 70)

    display.boot_step("SD CARD", 75)
    sd_ok = sd_logger.init(300)

    display.boot_step("READY" if sd_ok else "NO SD CARD", 100)
    await asyncio.sleep(1)
    display.finish_boot()


def main():
    # a startup error (WiFi, SD, display) must not block the
    # scheduler - relays have their state from config.json since import
    try:
        asyncio.run(boot())
    except Exception as e:
        print("BOOT: error:", repr(e))
        sys.print_exception(e)
        asyncio.new_event_loop()

    crashes = []

    while True:
        try:
            asyncio.run(run_tasks())
        except KeyboardInterrupt:
            raise
        except Exception as e:
            # crash of the asyncio loop itself (e.g. OSError EIO in select)
            print("MAIN: event loop crashed:", repr(e))
            events.log("event loop crashed: " + repr(e))
            sys.print_exception(e)

        now = utime.ticks_ms()
        crashes = [
            t for t in crashes
            if utime.ticks_diff(now, t) < LOOP_CRASH_WINDOW_MS
        ]
        crashes.append(now)

        if len(crashes) >= MAX_LOOP_CRASHES:
            print("MAIN: too many crashes -> machine.reset()")
            events.log("reset: too many event loop crashes")
            utime.sleep_ms(500)
            machine.reset()

        print("MAIN: restarting event loop")
        asyncio.new_event_loop()

        # the old server holds port 80; after dropping the reference GC closes
        # its socket (verified on the ESP) and the new one can bind
        server._server = None
        gc.collect()

        utime.sleep_ms(1000)


main()
