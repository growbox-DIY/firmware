# config_store.py - save and load config from flash
import json

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "auto": {
        "enabled": False,
        "floweringStartDate": None,
        # "YYYY-MM-DD HH:MM:SS" local time, set together with the date
        "floweringStartedAt": None,
        # light OFF: fan at dimmer.night.level (no auto)
        "nightFan": {"enabled": False},
    },
    "relayLight": {"state": 0},
    "relayFan": {"state": 0},
    "dimmer": {
        "enabled": True,
        "day": {"level": 50},
        "night": {"level": 30}
    },
    "lightSchedule": [
        {"on": "18:00", "off": "06:00"},
    ],
    "sensor": {
        "enabled": True,
        "interval": 10000
    },
    "display": {
        "enabled": False,
    },
    # TFT monitor (growbox-monitor) reads this from /api/config: disabled =
    # dark screen, brightness = daytime backlight (night has its own)
    "monitor": {
        "enabled": True,
        "brightness": 100,
    },
    # MANUAL (vege): fan level from temperature. Thresholds as VEG_TARGETS
    # in the app - light ON 24-26 C, light OFF 20-22 C.
    "fanAuto": {
        "enabled": False,
        "minLevel": 20,
        "maxLevel": 100,
        "day": {"min": 24, "max": 26},
        "night": {"min": 20, "max": 22}
    },
    # AUTO (flowering): same as fanAuto, but separate thresholds - the lamp runs
    # at approx. 80 % power instead of 40-50 % and heats much more.
    # At night with nightFan the speed does not drop below the humidity level.
    "fanAutoFlower": {
        "enabled": True,
        "minLevel": 40,
        "maxLevel": 100,
        "day": {"min": 23, "max": 26},
        "night": {"min": 20, "max": 22}
    },
    # humidifier on a mini ESP (humidifier.py), only with the light on:
    # turns on below "min", off from "max", separate MANUAL and AUTO. While
    # running, fan auto does not go above fan.maxLevel, unless the
    # temperature exceeds fan.tempLimit.
    "humidifier": {
        "peer": "10003baf0ae8",
        "manual": {"enabled": False, "min": 60, "max": 65},
        "auto": {"enabled": False, "min": 45, "max": 50},
        "fan": {"maxLevel": 40, "tempLimit": 27}
    },
    # feeding saved from the app (POST /api/feeding), ISO dates
    "feeding": {
        "lastFedAt": None,
        "count": 0,
        "history": []
    }
}


def _deep_merge(target, source):
    for key in source:
        if isinstance(source[key], dict):
            target[key] = target.get(key, {})
            _deep_merge(target[key], source[key])
        elif isinstance(source[key], list):
            target[key] = source[key]
        else:
            target[key] = source[key]


def load():
    try:
        with open(CONFIG_FILE, "r") as f:
            saved = json.load(f)

        # deep merge with DEFAULT_CONFIG
        import json as _json
        merged = _json.loads(_json.dumps(DEFAULT_CONFIG))
        _deep_merge(merged, saved)
        return merged

    except Exception:
        # Missing file or error - save the default config
        save(DEFAULT_CONFIG)
        import json as _json
        return _json.loads(_json.dumps(DEFAULT_CONFIG))


def save(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
    except Exception as e:
        print("Config save error:", e)
