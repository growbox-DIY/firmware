import gc
import json
import os

import uasyncio as asyncio
import utime
from machine import Pin, SPI

import esp_config
import sdcard
import sensor
from server import _should_light_be_on

SD_MOUNT_POINT = "/sd"

_LOG_FILE = "/sd/environment.csv"
_ARCHIVE_FILE = "/sd/environment.old.csv"

MAX_LOG_SIZE = 1024 * 1024

# SPI speed after card init. On read errors drop to 1000000 (or 400000 for testing).
SD_BAUDRATE = 4000000

DEFAULT_INTERVAL_SECONDS = 300
RECOVERY_INTERVAL_SECONDS = 30

_interval_seconds = DEFAULT_INTERVAL_SECONDS
_enabled = False

_spi = None
_cs = None
_sd = None


def _create_sd():
    global _spi
    global _cs
    global _sd

    _spi = SPI(
        2,
        baudrate=400000,
        polarity=0,
        phase=0,
        sck=Pin(esp_config.PINS["sd_sck"]),
        mosi=Pin(esp_config.PINS["sd_mosi"]),
        miso=Pin(esp_config.PINS["sd_miso"]),
    )

    _cs = Pin(
        esp_config.PINS["sd_cs"],
        Pin.OUT
    )

    _sd = sdcard.SDCard(
        _spi,
        _cs,
        SD_BAUDRATE
    )


def _unmount():
    try:
        os.umount(SD_MOUNT_POINT)
    except Exception:
        pass


def _mount():
    global _enabled

    try:
        _create_sd()

        os.mount(
            _sd,
            SD_MOUNT_POINT
        )

        # New file ONLY when it does not exist (ENOENT). Any other error
        # (e.g. transient I/O while mounting) must abort the mount,
        # not wipe the log with mode "w".
        try:
            size = os.stat(_LOG_FILE)[6]
        except OSError as e:
            if e.args and e.args[0] == 2:  # ENOENT
                with open(_LOG_FILE, "w") as f:
                    f.write(
                        "datetime,day_night,temperature_c,humidity_percent\n"
                    )
                size = 0
                print("SD: created new log")
            else:
                raise

        _enabled = True

        print("SD: OK, log size", size, "B")

        return True

    except Exception as e:
        _enabled = False

        print("SD: FAIL -> retry", repr(e))

        _unmount()

        return False


def init(interval_seconds=DEFAULT_INTERVAL_SECONDS):
    global _interval_seconds
    global _enabled

    _interval_seconds = interval_seconds
    _enabled = False

    _unmount()

    return _mount()


def _rotate_log():
    if _readers > 0:
        return

    try:
        size = os.stat(_LOG_FILE)[6]
    except OSError:
        return

    if size < MAX_LOG_SIZE:
        return

    try:
        os.remove(_ARCHIVE_FILE)
    except OSError:
        pass

    os.rename(
        _LOG_FILE,
        _ARCHIVE_FILE
    )

    with open(_LOG_FILE, "w") as f:
        f.write(
            "datetime,day_night,temperature_c,humidity_percent\n"
        )


def _handle_error(error):
    global _enabled
    global _spi
    global _cs
    global _sd

    _enabled = False

    print("SD: lost -> retry")

    _unmount()

    _spi = None
    _cs = None
    _sd = None


def start():
    global _enabled

    if not _enabled:
        return _mount()

    try:
        with open(_LOG_FILE, "a"):
            pass

        _enabled = True

        return True

    except Exception:
        return _mount()


_HEADER = b"datetime,day_night,temperature_c,humidity_percent\n"

# Block size when reading from the card (512 B = one sector, i.e. the proven CMD17 path in sdcard.py).
_READ_BLOCK = 512

# How many blocks to read before yielding to the asyncio loop.
_BLOCKS_PER_YIELD = 4

# Active reader count - log rotation waits until nobody is reading.
_readers = 0


def mem_info():
    """(free GC, free IDF, largest free IDF block) - diagnostics."""
    try:
        import esp32
        heaps = esp32.idf_heap_info(esp32.HEAP_DATA)
        idf_free = sum(h[1] for h in heaps)
        idf_largest = max(h[2] for h in heaps)
    except Exception:
        idf_free = idf_largest = -1

    return gc.mem_free(), idf_free, idf_largest


def _parse_row(line):
    """bytes -> (datetime, day_night, temperature, humidity) or None."""
    parts = line.strip().split(b",")

    if len(parts) != 4:
        return None

    try:
        temperature = float(parts[2])
        humidity = float(parts[3])
    except ValueError:
        return None

    # NaN / inf are not valid JSON (x - x gives NaN for both)
    if temperature - temperature != 0 or humidity - humidity != 0:
        return None

    return (
        parts[0].decode(),
        parts[1].decode(),
        temperature,
        humidity,
    )


def _qualifies(line, before, since=None):
    """Data row matching the query -> parsed tuple, otherwise None."""
    if len(line) < 19:
        return None

    # fast filter on bytes, no parsing
    if before is not None and line[:19] >= before:
        return None

    if since is not None and line[:19] <= since:
        return None

    return _parse_row(line)


def _is_data(line):
    # a data row starts with the year ("2026-..."), the header with "datetime"
    return len(line) >= 19 and 48 <= line[0] <= 57


async def _bisect(path, key, strict):
    """
    The log is append-only, so rows are sorted by time.
    Binary-searches the offset of the first row with datetime > key (strict)
    or >= key. Returns the file size when there is no such row,
    None when the file does not exist. A dozen or so reads instead of a full scan.
    """
    try:
        f = open(path, "rb")
    except OSError as e:
        if e.args and e.args[0] == 2:  # ENOENT
            return None
        raise

    def matches(line):
        return line[:19] > key if strict else line[:19] >= key

    try:
        f.seek(0, 2)
        size = f.tell()

        # skip header
        f.seek(0)
        f.readline()
        lo = f.tell()
        hi = size

        # invariant: the target row starts in [lo, hi]
        while hi - lo > _READ_BLOCK:
            mid = (lo + hi) // 2
            f.seek(mid)
            f.readline()  # partial line
            start = f.tell()

            if start >= hi:
                break

            line = f.readline()

            if _is_data(line) and matches(line):
                hi = start
            else:
                lo = start + len(line)

            await asyncio.sleep(0)

        # the rest linearly
        f.seek(lo)
        pos = lo

        while pos < hi:
            line = f.readline()

            if not line:
                break

            if _is_data(line) and matches(line):
                return pos

            pos += len(line)

        return hi

    finally:
        f.close()


async def _find_start(path, state, limit, before, end=None):
    """
    Pass 1: reads the file FROM THE END (or from offset `end`) and only COUNTS matching rows
    (keeps nothing in RAM). `state` = [found, path, offset, datetime]
    of the oldest row to send. Returns True when reading must continue
    (older file), False when limit + 1 rows were found.
    """
    try:
        f = open(path, "rb")
    except OSError as e:
        if e.args and e.args[0] == 2:  # ENOENT - a missing file is not a card error
            return True
        raise

    def hit(row, offset):
        state[0] += 1

        if state[0] <= limit:
            state[1] = path
            state[2] = offset
            state[3] = row[0]

        # +1 over the limit = we know there is more
        return state[0] > limit

    try:
        if end is None:
            f.seek(0, 2)
            pos = f.tell()
        else:
            pos = end

        rest = b""
        blocks = 0

        while pos > 0:
            step = _READ_BLOCK if pos >= _READ_BLOCK else pos
            pos -= step
            f.seek(pos)

            chunk = f.read(step) + rest
            lines = chunk.split(b"\n")

            # the first chunk may be a partial line - kept for later
            rest = lines[0]

            # offset of the start of the last line in the chunk
            offset = pos + len(chunk) - len(lines[-1])

            for i in range(len(lines) - 1, 0, -1):
                row = _qualifies(lines[i], before)

                if row is not None and hit(row, offset):
                    return False

                offset -= len(lines[i - 1]) + 1

            blocks += 1

            if blocks % _BLOCKS_PER_YIELD == 0:
                await asyncio.sleep(0)

        # the file's first line (header) is dropped in _parse_row
        row = _qualifies(rest, before)

        if row is not None and hit(row, 0):
            return False

        return True

    finally:
        f.close()


class _SdError(Exception):
    pass


async def _send_rows(path, offset, before, since, ctx, send):
    """
    Pass 2: reads the file from `offset` FORWARD and sends matching
    rows as JSON, chunk by chunk. `ctx` = [remaining, first,
    datetime of the last one sent]. Returns True when, after the limit
    ran out, another matching row turned up (i.e. there is more).
    Card errors -> _SdError; socket errors propagate unchanged.
    """
    try:
        f = open(path, "rb")
        f.seek(offset)
    except OSError as e:
        if e.args and e.args[0] == 2:  # ENOENT
            return False
        raise _SdError(e)
    except Exception as e:
        raise _SdError(e)

    async def flush(part):
        if not part:
            return

        chunk = ",".join(part)

        if not ctx[1]:
            chunk = "," + chunk

        ctx[1] = False

        await send(chunk.encode())

    try:
        rest = b""

        while True:
            try:
                block = f.read(_READ_BLOCK)
            except Exception as e:
                raise _SdError(e)

            if block:
                lines = (rest + block).split(b"\n")
                rest = lines.pop()
            else:
                lines = [rest]  # last line without "\n"

            part = []

            for line in lines:
                row = _qualifies(line, before, since)

                if row is None:
                    continue

                if ctx[0] <= 0:
                    await flush(part)
                    return True

                part.append(_ROW_JSON % row)
                ctx[0] -= 1
                ctx[2] = row[0]

            await flush(part)

            if not block:
                return False

    finally:
        f.close()


# Max rows per response (~0.6 s of streaming). Requests run one at a time
# (server._request_lock), and with 500 rows (~3 s) so many connections
# piled up in the queue that the ESP-IDF heap ran out. The client pages
# further using has_more / next_before / next_since.
MAX_ROWS = 100

_ROW_JSON = (
    '{"datetime":"%s","day_night":"%s",'
    '"temperature":%s,"humidity":%s}'
)

_JSON_HEAD = b'{"status":"ok","data":['


def _json_tail(has_more, next_before, next_since):
    # metadata at the end: has_more is known only after reading the rows
    return (
        '],"has_more":%s,"next_before":%s,"next_since":%s}'
        % (
            "true" if has_more else "false",
            json.dumps(next_before),
            json.dumps(next_since),
        )
    ).encode()


def _json_offline():
    return b'{"status":"offline","data":[' + _json_tail(False, None, None)


def _parse_key(value):
    """'YYYY-MM-DD HH:MM:SS' -> bytes, anything else -> None."""
    if not value or len(value) != 19:
        return None

    return value.encode()


async def _segments_before(limit, before, state):
    """`before` mode: the newest `limit` rows older than `before`."""
    log_end = await _bisect(_LOG_FILE, before, False) if before else None

    if await _find_start(_LOG_FILE, state, limit, before, log_end):
        arch_end = (
            await _bisect(_ARCHIVE_FILE, before, False) if before else None
        )
        await _find_start(_ARCHIVE_FILE, state, limit, before, arch_end)

    start_path, start_offset = state[1], state[2]

    if start_path == _ARCHIVE_FILE:
        return ((_ARCHIVE_FILE, start_offset), (_LOG_FILE, 0))

    if start_path == _LOG_FILE:
        return ((_LOG_FILE, start_offset),)

    return ()


async def _segments_since(since):
    """`since` mode: rows newer than `since`, oldest first."""
    log_start = await _bisect(_LOG_FILE, since, True)

    # does the current log have any row <= since? if not, start in the archive
    try:
        f = open(_LOG_FILE, "rb")
        try:
            header = len(f.readline())
        finally:
            f.close()
    except OSError:
        header = 0

    if log_start is None or log_start <= header:
        arch_start = await _bisect(_ARCHIVE_FILE, since, True)

        if arch_start is not None:
            return ((_ARCHIVE_FILE, arch_start), (_LOG_FILE, 0))

    return ((_LOG_FILE, log_start or 0),)


async def stream_environment_json(send, limit=MAX_ROWS, before=None, since=None):
    """
    Sends via async `send(bytes)` JSON
    {"status", "data": [...], "has_more", "next_before", "next_since"}
    from oldest to newest:
    - without `since`: the newest `limit` readings older than `before`
      (page back: next query with before=next_before),
    - with `since`: the oldest `limit` readings newer than `since`
      (incremental sync: next query with since=next_since).

    The start is found by binary search, so cost does not grow with log size.
    Does not build a list of rows in RAM: a large list inflates the GC heap
    at the cost of the ESP-IDF heap, and then WiFi/lwIP has no buffers
    and HTTP responses stall.
    """
    global _readers

    try:
        limit = int(limit)
    except ValueError:
        limit = MAX_ROWS

    limit = max(1, min(MAX_ROWS, limit))

    before = _parse_key(before)
    since = _parse_key(since)

    if not _enabled:
        print("ENV: SD offline")
        await send(_json_offline())
        return

    t0 = utime.ticks_ms()
    print(
        "ENV: read start, limit", limit, "before", before, "since", since,
        "mem", mem_info()
    )

    _readers += 1

    try:
        # pass 1: where to start
        state = [0, None, 0, None]

        try:
            if since is not None:
                segments = await _segments_since(since)
            else:
                segments = await _segments_before(limit, before, state)
        except Exception as e:
            print("ENV: SD error:", repr(e))
            _handle_error(e)
            await send(_json_offline())
            return

        await send(_JSON_HEAD)

        # pass 2: send from the oldest
        if since is not None:
            count = limit
        else:
            count = min(state[0], limit)

        ctx = [count, True, None]
        more = False

        for path, offset in segments:
            try:
                if await _send_rows(path, offset, before, since, ctx, send):
                    more = True
                    break
            except _SdError as e:
                # the response gets cut off - the client receives incomplete JSON
                err = e.args[0] if e.args else e
                print("ENV: SD error:", repr(err))
                _handle_error(err)
                return

        if since is not None:
            await send(_json_tail(more, None, ctx[2] if more else None))
        else:
            has_more = state[0] > limit
            await send(_json_tail(has_more, state[3] if has_more else None, None))

        print(
            "ENV: sent",
            count - ctx[0],
            "rows in",
            utime.ticks_diff(utime.ticks_ms(), t0),
            "ms, mem",
            mem_info()
        )

    finally:
        _readers -= 1


async def stream_csv(send):
    """
    Sends the whole log (archive + current) as CSV, chunk by chunk.
    `send` is an async function taking bytes.
    Socket errors (client disconnected) propagate and do NOT unmount the card.
    """
    global _readers

    if not _enabled:
        return False

    _readers += 1

    try:
        await send(_HEADER)

        for path in (_ARCHIVE_FILE, _LOG_FILE):
            try:
                f = open(path, "rb")
            except OSError as e:
                if e.args and e.args[0] == 2:  # ENOENT
                    continue
                _handle_error(e)
                return False

            try:
                try:
                    f.readline()  # skip the file header
                except Exception as e:
                    _handle_error(e)
                    return False

                while True:
                    try:
                        chunk = f.read(_READ_BLOCK)
                    except Exception as e:
                        _handle_error(e)
                        return False

                    if not chunk:
                        break

                    await send(chunk)
                    await asyncio.sleep(0.01)

            finally:
                f.close()

        return True

    finally:
        _readers -= 1


def is_enabled():
    return _enabled


def erase():
    global _enabled

    # don't delete a file someone is reading/downloading
    if _readers > 0:
        raise OSError("SD busy")

    try:
        if not _enabled:
            if not _mount():
                raise OSError("SD offline")

        try:
            os.remove(_LOG_FILE)
        except OSError:
            pass

        try:
            os.remove(_ARCHIVE_FILE)
        except OSError:
            pass

        with open(_LOG_FILE, "w") as f:
            f.write(
                "datetime,day_night,temperature_c,humidity_percent\n"
            )

        _enabled = True

    except Exception as e:
        _handle_error(e)
        raise


async def task():
    while True:
        if _enabled:
            try:
                now = utime.localtime()

                data = sensor.get()

                temperature = data.get("temperature")
                humidity = data.get("humidity")

                # year < 2025 = clock after restart without NTP; such a row
                # would break the log order (binary search, since)
                if (
                        temperature is not None
                        and humidity is not None
                        and now[0] >= 2025
                ):
                    day_night = (
                        "DAY"
                        if _should_light_be_on()
                        else "NIGHT"
                    )

                    timestamp = (
                            "%04d-%02d-%02d %02d:%02d:%02d"
                            % (
                                now[0],
                                now[1],
                                now[2],
                                now[3],
                                now[4],
                                now[5],
                            )
                    )

                    _rotate_log()

                    with open(_LOG_FILE, "a") as f:
                        f.write(
                            "%s,%s,%s,%s\n"
                            % (
                                timestamp,
                                day_night,
                                temperature,
                                humidity,
                            )
                        )

                await asyncio.sleep(
                    _interval_seconds
                )

            except Exception as e:
                _handle_error(e)

                await asyncio.sleep(
                    RECOVERY_INTERVAL_SECONDS
                )

        else:
            if _mount():
                pass

            await asyncio.sleep(
                RECOVERY_INTERVAL_SECONDS
            )
