# humidifier.py - humidifier on a mini ESP (ESP32-C3) via ESP-NOW
#
# The decision is made here (update() from the scheduler), the mini only switches
# the relay. Protocol:
#   main -> mini: b"HUM" + state (0/1), every LINK_INTERVAL_S and right after a change
#   mini -> main: b"HST" + actual relay state (reply to HUM)
#   mini -> main: b"HEL" - mini searches for the channel after losing the link
# Without HUM for a dozen or so seconds the mini turns the relay off itself.
#
# Separate thresholds for MANUAL and AUTO, in both modes only with the light
# on. Hysteresis: turns on below "min", off from "max".
import network
import uasyncio as asyncio
import utime

import sensor

LINK_INTERVAL_S = 5
# no reply from mini for longer = offline
ONLINE_TIMEOUT_MS = 15000
# relay does not click more often than this
MIN_SWITCH_S = 60
# older reading = sensor died, humidifier off
MAX_SENSOR_AGE_S = 120

_espnow = None
_peer = None

_want = False
_reported = None
_last_reply = None
_last_switch = None


def _mode_cfg(config):
    hum = config["humidifier"]
    return hum["auto"] if config["auto"]["enabled"] else hum["manual"]


def _decide(config, light_on):
    cfg = _mode_cfg(config)

    if not cfg["enabled"] or not light_on:
        return False

    data = sensor.get()
    humidity = data["humidity"]

    if humidity is None or utime.time() - data["ts"] > MAX_SENSOR_AGE_S:
        return False

    if humidity < cfg["min"]:
        return True

    if humidity >= cfg["max"]:
        return False

    # within the hysteresis band - no change
    return _want


def update(config, light_on):
    """From the scheduler: new target state, sent immediately on change."""
    global _want, _last_switch

    want = _decide(config, light_on)

    if want == _want:
        return

    # turning off due to mode/light/sensor is immediate, but humidity
    # fluctuation alone does not switch more often than MIN_SWITCH_S
    now = utime.time()
    hysteresis_only = _mode_cfg(config)["enabled"] and light_on

    if (
            hysteresis_only
            and _last_switch is not None
            and now - _last_switch < MIN_SWITCH_S
    ):
        return

    print("HUMIDIFIER:", sensor.get()["humidity"], "% ->", "ON" if want else "OFF")
    _want = want
    _last_switch = now
    _send()


def online():
    return (
            _last_reply is not None
            and utime.ticks_diff(utime.ticks_ms(), _last_reply) < ONLINE_TIMEOUT_MS
    )


def running():
    return online() and bool(_reported)


def status():
    return {
        "online": online(),
        "want": _want,
        "on": running(),
    }


def fan_cap(config, level, temperature):
    """Fan level while the humidifier runs: the exhaust does not blow out
    the moisture the humidifier adds. Temperature takes priority."""
    fan = config["humidifier"]["fan"]

    if not running():
        return level

    if temperature is not None and temperature > fan["tempLimit"]:
        return level

    return min(level, fan["maxLevel"])


def _init(config):
    global _espnow, _peer

    import espnow

    # ESP-NOW needs an active interface; wifi.py may be recreating it
    # right now - then retry on the next pass
    sta = network.WLAN(network.STA_IF)

    if not sta.active():
        sta.active(True)

    e = espnow.ESPNow()
    e.active(True)

    peer = bytes.fromhex(config["humidifier"]["peer"])

    try:
        e.add_peer(peer)
    except OSError:
        pass  # already added (ESP_ERR_ESPNOW_EXIST)

    _espnow = e
    _peer = peer
    print("HUMIDIFIER: ESP-NOW ready, peer", config["humidifier"]["peer"])


def _reset():
    global _espnow

    try:
        _espnow.active(False)
    except Exception:
        pass

    _espnow = None


def _send():
    if _espnow is None:
        return

    try:
        _espnow.send(_peer, b"HUM" + bytes([1 if _want else 0]), False)
    except OSError as e:
        # e.g. wifi.py restarted the interface - ESP-NOW from scratch
        print("HUMIDIFIER: send error:", e)
        _reset()


def _receive():
    global _reported, _last_reply

    while True:
        mac, msg = _espnow.recv(0)

        if not msg:
            return

        if mac != _peer:
            continue

        if msg[:3] == b"HST" and len(msg) == 4:
            if _reported != msg[3]:
                print("HUMIDIFIER: mini reports", "ON" if msg[3] else "OFF")

            _reported = msg[3]
            _last_reply = utime.ticks_ms()

        elif msg == b"HEL":
            # mini found our channel - send state now, no waiting
            _send()


async def link_task(config):
    while True:
        try:
            if _espnow is None:
                _init(config)

            _receive()
            _send()
        except OSError as e:
            print("HUMIDIFIER: link error:", e)
            _reset()

        await asyncio.sleep(LINK_INTERVAL_S)
