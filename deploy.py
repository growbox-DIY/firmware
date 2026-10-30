#!/usr/bin/env python3
"""
ESP update over WiFi (OTA, see src/ota.py).

    ./deploy.py              # send changed files from src/ and restart the ESP
    ./deploy.py --dry-run    # only show what changed
    ./deploy.py sensor.py    # send selected files (even if unchanged)

Modules go as precompiled .mpy (mpy-cross matching the MicroPython version on
the ESP): the ESP does not compile sources at boot and keeps more RAM.
    pipx install mpy-cross==1.27.0.post2

Address: --url or the ESP_URL variable (default http://192.168.18.85).
Token: read from src/api_token.py (API_TOKEN).

main.py, boot.py and secrets.py do not go via OTA - cable only:
    mpremote connect /dev/cu.usbserial-0001 cp src/main.py :main.py
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT, "src")

PROTECTED = ("main.py", "boot.py", "secrets.py")
DEFAULT_URL = "http://192.168.18.85"

# .mpy format of MicroPython 1.27 on ESP32 (xtensawin)
MPY_CROSS = "mpy-cross"
MPY_VERSION = "mpy v6.3"
MPY_ARCH = "xtensawin"

# the new version confirms itself after 60 s of running (ota.CONFIRM_AFTER_S)
BOOT_TIMEOUT_S = 90
CONFIRM_AFTER_S = 60
CONFIRM_TIMEOUT_S = 150

# After a restart the ESP must not be flooded: requests run one at a time, and
# /api/update hashes all files with sha256. Frequent polling with a short
# timeout piled up connections in lwIP -> the ESP-IDF heap ran out,
# the ESP kept resetting and went back to the old version (rollback).
POLL_INTERVAL_S = 10
POLL_TIMEOUT_S = 20


def read_token():
    with open(os.path.join(APP_DIR, "api_token.py")) as f:
        match = re.search(r'^API_TOKEN\s*=\s*"([^"]+)"', f.read(), re.M)

    if not match:
        sys.exit("API_TOKEN not found in src/api_token.py (copy api_token_example.py)")

    return match.group(1)


class Esp:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def request(self, method, path, data=None, timeout=30):
        req = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={"Authorization": "Bearer " + self.token},
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError("%s %s -> HTTP %s %s" % (method, path, e.code, body))

    def status(self, timeout=30, names=None):
        path = "/api/update"

        # newer firmware hashes only these files, older ignores it
        if names:
            path += "?files=" + urllib.parse.quote(",".join(names), safe=",")

        return self.request("GET", path, timeout=timeout)

    def status_batched(self, names, batch=5):
        """
        Hashes in batches - all files at once exhausted the ESP-IDF heap.
        Files only on the ESP (no local copy) are not needed here.
        """
        result = None

        for i in range(0, len(names), batch):
            part = self.status(names=names[i:i + batch])

            if result is None:
                result = part
            else:
                result["files"] += part["files"]

        return result

    def upload(self, name, content):
        sha = hashlib.sha256(content).hexdigest()
        path = "/api/files/%s?sha256=%s" % (urllib.parse.quote(name), sha)

        return self.request("PUT", path, content, timeout=60)


def check_mpy_cross():
    try:
        version = subprocess.run(
            [MPY_CROSS, "--version"], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        sys.exit("mpy-cross missing: pipx install mpy-cross==1.27.0.post2")

    if MPY_VERSION not in version:
        sys.exit("mpy-cross emits a different format than %s: %s"
                 % (MPY_VERSION, version.strip()))


def local_files():
    """{"X.mpy": bytes} - modules from src/ compiled with mpy-cross."""
    files = {}

    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(os.listdir(APP_DIR)):
            if not name.endswith(".py") or name in PROTECTED:
                continue

            out = os.path.join(tmp, name[:-3] + ".mpy")
            # -s: only the file name in the .mpy, not the path on this computer -
            # same bytes (and hashes) on every machine
            result = subprocess.run(
                [MPY_CROSS, "-march=" + MPY_ARCH, "-s", name, "-o", out,
                 os.path.join(APP_DIR, name)],
                capture_output=True, text=True,
            )

            if result.returncode:
                sys.exit("mpy-cross %s:\n%s" % (name, result.stderr))

            with open(out, "rb") as f:
                files[os.path.basename(out)] = f.read()

    return files


def wait_for(fetch, timeout, done, label):
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            status = fetch()

            if done(status):
                return status
        except (OSError, RuntimeError, ValueError):
            pass  # ESP still booting

        print(".", end="", flush=True)
        time.sleep(POLL_INTERVAL_S)

    print()
    sys.exit("Timeout: %s" % label)


def main():
    parser = argparse.ArgumentParser(description="OTA update ESP")
    parser.add_argument("files", nargs="*", help="send only these files")
    parser.add_argument("--url", default=os.environ.get("ESP_URL", DEFAULT_URL))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-wait", action="store_true",
                        help="do not wait for restart and confirmation")
    args = parser.parse_args()

    check_mpy_cross()
    esp = Esp(args.url, read_token())
    files = local_files()

    print("ESP:", esp.url)

    # .py sources on the ESP too: they shadow .mpy, apply moves them to .bak
    sources = [name[:-4] + ".py" for name in files]

    try:
        status = esp.status_batched(
            list(files) + sources + list(PROTECTED)
        )
    except (OSError, RuntimeError) as e:
        sys.exit("No connection to the ESP: %s" % e)

    remote = {f["name"]: f["sha256"] for f in status["files"]}

    if status.get("state"):
        print("Warning: previous update not confirmed yet:",
              status["state"])

    if args.files:
        # sensor.py or sensor.mpy - it is sent compiled anyway
        wanted = [os.path.splitext(n)[0] + ".mpy" for n in args.files]
        unknown = [n for n, w in zip(args.files, wanted) if w not in files]

        if unknown:
            sys.exit("No such files in src/ (or they are protected): %s"
                     % ", ".join(unknown))

        changed = wanted
    else:
        changed = [
            name for name, content in files.items()
            if remote.get(name) != hashlib.sha256(content).hexdigest()
            or name[:-4] + ".py" in remote
        ]

    for name in PROTECTED:
        path = os.path.join(APP_DIR, name)

        if name in remote and os.path.exists(path):
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != remote[name]:
                    print("Warning: %s differs from the version on the ESP - "
                          "upload over cable (mpremote)" % name)

    if not changed:
        print("Everything up to date.")
        return

    print("To send:", ", ".join(changed))

    if args.dry_run:
        return

    if status.get("staged"):
        esp.request("POST", "/api/update/discard")

    for name in changed:
        print("  %-18s %6d B" % (name, len(files[name])), end=" ", flush=True)

        try:
            esp.upload(name, files[name])
        except (OSError, RuntimeError) as e:
            print("ERROR")
            esp.request("POST", "/api/update/discard")
            sys.exit("Upload aborted, nothing changed on the ESP: %s" % e)

        print("ok")

    esp.request("POST", "/api/update/apply")
    print("Applied, ESP is restarting")

    if args.no_wait:
        return

    time.sleep(POLL_INTERVAL_S)

    # light /api/status - only whether it is up and for how many seconds
    health = wait_for(
        lambda: esp.request("GET", "/api/status", timeout=POLL_TIMEOUT_S),
        BOOT_TIMEOUT_S, lambda s: True, "ESP did not come up",
    )
    print()

    wait_s = CONFIRM_AFTER_S + 5 - health.get("uptime_s", 0)

    if wait_s > 0:
        print("ESP is up, waiting %d s for confirmation" % wait_s)
        time.sleep(wait_s)

    # full /api/update only now, one at a time and with a long timeout
    status = wait_for(
        lambda: esp.status(timeout=60, names=changed),
        CONFIRM_TIMEOUT_S, lambda s: not s.get("state"),
        "the new version did not confirm",
    )

    # state is empty after a rollback too - file hashes decide
    remote = {f["name"]: f["sha256"] for f in status["files"]}
    wrong = [
        name for name in changed
        if remote.get(name) != hashlib.sha256(files[name]).hexdigest()
    ]

    if wrong:
        sys.exit("ESP went back to the previous version (rollback): %s"
                 % ", ".join(wrong))

    print("Done - update confirmed.")


if __name__ == "__main__":
    main()
