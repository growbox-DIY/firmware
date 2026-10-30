# main.py - safety net for OTA updates (ota.py).
#
# Uploaded ONLY over cable and deliberately standalone (no import of ota.py,
# which may be broken too). After an update ota_state.json has
# "pending": every boot is counted, and if the new version does not confirm
# itself within MAX_BOOTS boots (import error, WDT, reset loop),
# files are restored from .bak.

import json
import os
import sys

import machine
import utime

STATE_FILE = "ota_state.json"
MAX_BOOTS = 3


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def _rollback(files):
    for name in files:
        try:
            os.stat(name + ".bak")
        except OSError:
            # file added in the update - there was no previous version
            try:
                os.remove(name)
            except OSError:
                pass
            continue

        try:
            os.remove(name)
        except OSError:
            pass

        os.rename(name + ".bak", name)


_state = _load_state()

if _state and _state.get("pending"):
    _state["boots"] = _state.get("boots", 0) + 1

    if _state["boots"] > MAX_BOOTS:
        print("OTA: update not confirmed -> rollback", _state.get("files"))

        try:
            _rollback(_state.get("files", []))
            os.remove(STATE_FILE)
        except Exception as e:
            sys.print_exception(e)

        utime.sleep_ms(500)
        machine.reset()

    print("OTA: pending update, boot", _state["boots"], "/", MAX_BOOTS)
    _save_state(_state)

try:
    import app  # noqa: F401 - app.py runs on import
except KeyboardInterrupt:
    raise
except Exception as e:
    # startup error (e.g. SyntaxError after an update): reset instead of
    # a dead REPL; with pending, further boots lead to a rollback.
    # 10 s margin so mpremote can break the reset loop.
    print("MAIN: app crashed at startup:", repr(e))
    sys.print_exception(e)
    utime.sleep(10)
    machine.reset()
