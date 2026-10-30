# fan_auto.py - fan level from the sensor
#
# MANUAL (vege), temperature. Low speed preferred: up to the middle of the band
# (sweet point) the fan stays at minLevel, from the middle to "max" + headroom
# it rises linearly to maxLevel, in steps of 10. Hysteresis: a new level
# only when the temperature moves that much since the last change - sensor
# noise at a step boundary does not jerk the fan.
#
# AUTO (flowering): the same curve with separate thresholds (fanAutoFlower),
# since the lamp heats more then - only with the light on. At night
# (nightFan) the speed is a fixed night level from the app (server.py); we don't
# tie it to humidity, because room air can be just as humid.
import utime

import humidifier
import sensor

HEADROOM = 1
STEP = 10
HYSTERESIS = 0.3
# older reading = sensor died, don't control blindly
MAX_SENSOR_AGE_S = 120

_last_temp = None


def active_key(config):
    """Auto settings key for the current mode."""
    if config.get("auto", {}).get("enabled", False):
        return "fanAutoFlower"
    return "fanAuto"


def _cfg(config):
    cfg = config.get(active_key(config))
    return cfg if cfg and cfg.get("enabled", False) else None


def enabled(config):
    return _cfg(config) is not None


def _ratio(value, low, high):
    return max(0, min(1, (value - low) / (high - low)))


def _step(level):
    return int(round(level / STEP) * STEP)


def _fresh(data):
    return utime.time() - data["ts"] <= MAX_SENSOR_AGE_S


def target(cfg, temperature, light_on):
    band = cfg["day"] if light_on else cfg["night"]

    ideal = (band["min"] + band["max"]) / 2
    ratio = _ratio(temperature, ideal, band["max"] + HEADROOM)

    level = cfg["minLevel"] + (cfg["maxLevel"] - cfg["minLevel"]) * ratio
    return _step(level)


def apply(config, light_on, dimmer, force=False):
    global _last_temp

    cfg = _cfg(config)

    # AUTO night: the fixed night level is set by server.apply_auto_logic
    if cfg is None or (config["auto"]["enabled"] and not light_on):
        return

    data = sensor.get()
    temperature = data["temperature"]

    if temperature is None or not _fresh(data):
        return

    level = humidifier.fan_cap(
        config,
        target(cfg, temperature, light_on),
        temperature
    )

    current = dimmer.get_level()

    if level == current:
        return

    # hysteresis only for an adjacent step - a jump (e.g. day/night change)
    # goes through immediately
    if (
            not force
            and abs(level - current) <= STEP
            and _last_temp is not None
            and abs(temperature - _last_temp) < HYSTERESIS
    ):
        return

    print("FAN AUTO:", temperature, "C ->", level, "%")
    _last_temp = temperature
    dimmer.set_level(level)
