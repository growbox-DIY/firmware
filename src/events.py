# events.py - short event log on flash (restarts, loop crashes,
# WiFi loss), read remotely via GET /api/status - without a cable there is
# no other insight into what happened.

import os

import utime

EVENTS_FILE = "events.log"

# when exceeded, the second half of the file is kept
MAX_SIZE = 4096


def log(message):
    try:
        t = utime.localtime()
        line = "%04d-%02d-%02d %02d:%02d:%02d %s\n" % (
            t[0], t[1], t[2], t[3], t[4], t[5], message
        )

        try:
            size = os.stat(EVENTS_FILE)[6]
        except OSError:
            size = 0

        if size > MAX_SIZE:
            with open(EVENTS_FILE) as f:
                f.seek(size // 2)
                f.readline()  # partial line
                rest = f.read()

            with open(EVENTS_FILE, "w") as f:
                f.write(rest)

        with open(EVENTS_FILE, "a") as f:
            f.write(line)

        print("EVENT:", message)

    except Exception as e:
        # the log must not break anything
        print("EVENT log error:", repr(e))


# file tail read by tail() - enough for ~12 lines of ~60 B
TAIL_BYTES = 1024


def tail(count=12):
    # only the file tail: the whole file (up to 4 KB) + split is ~10 KB garbage
    # per GET /api/status - the GC heap grew at the cost of the ESP-IDF heap
    # and it ended with a "heap exhausted" reset
    try:
        size = os.stat(EVENTS_FILE)[6]

        with open(EVENTS_FILE) as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
                f.readline()  # partial line

            lines = f.read().split("\n")
    except OSError:
        return []

    return [line for line in lines if line][-count:]
