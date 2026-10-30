# ota.py - updating .py files over WiFi
#
# .py files or precompiled .mpy (deploy.py sends .mpy: without compiling
# on the ESP there is more RAM left). MicroPython loads X.py before X.mpy, so
# apply moves the other module version to .bak - rollback in main.py restores it.
#
# 1. PUT /api/files/<file>  -> write to <file>.new (streamed, with sha256)
# 2. POST /api/update/apply -> <file> -> <file>.bak, <file>.new -> <file>,
#                              write ota_state.json (pending) and reset
# 3. main.py counts boots with pending; after MAX_BOOTS without confirmation
#    restores .bak. The new version confirms itself (confirm_task)
#    once it has run for CONFIRM_AFTER_S seconds and the HTTP server is up.
#
# main.py, boot.py, secrets.py and config.json are not updated via OTA:
# a broken main.py or WiFi password = cable only.

import binascii
import gc
import hashlib
import json
import os

import uasyncio as asyncio

STATE_FILE = "ota_state.json"

PROTECTED = ("main.py", "boot.py", "secrets.py")

CONFIRM_AFTER_S = 60

_CHUNK = 1024
_IO_TIMEOUT = 15

# max size of one file - more is likely a mistake, not code
MAX_FILE_SIZE = 128 * 1024


# One fixed buffer for reading files and uploads. read() allocates a new buffer
# for every chunk - tens of KB of garbage per file inflate the GC heap
# at the cost of the ESP-IDF heap and WiFi stops sending (as in sd_logger).
_buf = None


def _buffer():
    global _buf

    if _buf is None:
        _buf = bytearray(_CHUNK)

    return _buf


class OtaError(Exception):
    pass


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _stem(name):
    """Module name for X.py / X.mpy, otherwise None."""
    for ext in (".py", ".mpy"):
        if name.endswith(ext):
            return name[:-len(ext)]

    return None


def valid_name(name):
    """Only .py / .mpy files in the root directory, excluding protected ones."""
    stem = _stem(name)

    if not stem or name in PROTECTED or stem + ".py" in PROTECTED:
        return False

    for c in stem:
        if not (c.isalpha() or c.isdigit() or c == "_"):
            return False

    return True


def _other(name):
    """Other module version: X.py <-> X.mpy."""
    stem = _stem(name)
    return stem + (".mpy" if name.endswith(".py") else ".py")


def _staged_names():
    return [
        n[:-4] for n in os.listdir()
        if n.endswith(".new") and valid_name(n[:-4])
    ]


def _sha256_file(path):
    h = hashlib.sha256()
    buf = _buffer()
    view = memoryview(buf)

    with open(path, "rb") as f:
        while True:
            n = f.readinto(buf)

            if not n:
                break

            h.update(view[:n])

    return binascii.hexlify(h.digest()).decode()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


async def status(names=None):
    """
    names: only these files (after an update the sent ones suffice). Hashing
    all files at once exhausted the ESP-IDF heap (reset "heap
    exhausted"), hence GC and yielding to the loop after each file.
    """
    gc.collect()

    files = []
    staged = []

    for name in sorted(os.listdir()):
        if name.endswith(".new") and valid_name(name[:-4]):
            staged.append(name[:-4])
        elif _stem(name) and (names is None or name in names):
            await asyncio.sleep_ms(0)
            files.append({
                "name": name,
                "size": os.stat(name)[6],
                "sha256": _sha256_file(name),
            })
            # the sha256 object (mbedtls) holds its context in the ESP-IDF heap
            # until collected - dozens of files at once exhausted it
            gc.collect()

    return {
        "state": load_state(),
        "staged": staged,
        "files": files,
    }


async def receive(name, reader, length, body, sha256):
    """
    Writes the request body to <name>.new. `body` = bytes already read
    together with the headers, the rest (up to `length`) is read from `reader`.
    """
    if not valid_name(name):
        raise OtaError("invalid name")

    if length <= 0 or length > MAX_FILE_SIZE:
        raise OtaError("invalid length")

    gc.collect()

    tmp = name + ".new"
    h = hashlib.sha256()
    received = 0
    buf = _buffer()
    view = memoryview(buf)

    try:
        with open(tmp, "wb") as f:
            if body:
                body = body[:length]
                f.write(body)
                h.update(body)
                received = len(body)

            while received < length:
                n = await asyncio.wait_for(
                    reader.readinto(view[:min(_CHUNK, length - received)]),
                    _IO_TIMEOUT
                )

                if not n:
                    raise OtaError("connection closed")

                f.write(view[:n])
                h.update(view[:n])
                received += n

        digest = binascii.hexlify(h.digest()).decode()

        if sha256 and digest != sha256.lower():
            raise OtaError("sha256 mismatch")

    except Exception:
        _remove(tmp)
        raise

    return digest


def discard():
    removed = _staged_names()

    for name in removed:
        _remove(name + ".new")

    return removed


def apply():
    """Swaps in .new files and saves pending state. The caller does the reset."""
    staged = _staged_names()

    if not staged:
        raise OtaError("nothing staged")

    # earlier unconfirmed update: its .bak is the last
    # working version - we don't overwrite it with an untested one
    previous = load_state()
    keep_bak = previous["files"] if previous and previous.get("pending") else []

    files = list(keep_bak)

    def set_aside(name):
        # old version to .bak (rollback); with a previous unconfirmed
        # update .bak already exists - the current file just goes away
        if name in files:
            _remove(name)
        elif _exists(name):
            _remove(name + ".bak")
            os.rename(name, name + ".bak")
            files.append(name)

    for name in staged:
        if name not in files:
            if _exists(name):
                set_aside(name)
            else:
                # new file - to be removed on rollback
                _remove(name + ".bak")
                files.append(name)
        else:
            _remove(name)

        # X.py would shadow the new X.mpy (and vice versa would leave a dead file)
        other = _other(name)

        if other not in staged and _exists(other):
            set_aside(other)

        os.rename(name + ".new", name)

    with open(STATE_FILE, "w") as f:
        json.dump({"pending": True, "boots": 0, "files": files}, f)

    return staged


def confirm():
    state = load_state()

    if state and state.get("pending"):
        _remove(STATE_FILE)
        print("OTA: update confirmed", state.get("files"))
        return True

    return False


async def confirm_task(is_ready):
    """After CONFIRM_AFTER_S s of running (and when is_ready()) confirms the update."""
    if not (load_state() or {}).get("pending"):
        return

    await asyncio.sleep(CONFIRM_AFTER_S)

    while not is_ready():
        await asyncio.sleep(5)

    confirm()
