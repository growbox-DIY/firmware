import network
import uasyncio as asyncio

import display
import events
import secrets

# Known networks: (ssid, password). The TP-Link RE200 extender broadcasts the
# router network as <SSID>_EXT with the same password. secrets.py does not go via
# OTA, so without NETWORKS in secrets.py both networks are built here.
NETWORKS = getattr(secrets, "NETWORKS", None) or (
    (secrets.SSID, secrets.PASSWORD),
    (secrets.SSID + "_EXT", secrets.PASSWORD),
)

# Return to a better network, e.g. when the extender comes back after a failure
# while the ESP sits on the router: with weak signal, scan every ROAM_CHECK_S and
# switch when a known network is clearly stronger. A switch is a few seconds
# without WiFi, hence the margin - to avoid hopping between two similar networks.
ROAM_CHECK_S = 600
# extender broadcasts but does not let in: each attempt is ~25 s without WiFi, so
# after a failed one the interval doubles up to ROAM_CHECK_MAX_S
ROAM_CHECK_MAX_S = 3600
ROAM_WEAK_RSSI = -65
ROAM_MARGIN_DB = 10

wlan = None
joined_ssid = None
display.init()


def _new_wlan():
    station = network.WLAN(network.STA_IF)
    station.active(True)
    station.config(dhcp_hostname="esp32")

    # Disable WiFi power saving - with it the ESP32 loses/delays ACKs
    # and longer HTTP responses can stall.
    try:
        station.config(pm=station.PM_NONE)
    except (AttributeError, ValueError):
        pass  # older MicroPython has no PM_NONE

    return station


def _candidates(station):
    """Known networks in range, strongest first: [(ssid, password, bssid)].

    When the extender dies, it is not in the scan and the router network remains.
    Empty scan (error, momentary deafness) - try all in turn.
    """

    passwords = dict(NETWORKS)
    found = {}

    try:
        scan = station.scan()
    except OSError as e:
        print("WiFi scan error:", e)
        scan = []

    for entry in scan:
        try:
            ssid = entry[0].decode()
        except UnicodeError:
            continue

        rssi = entry[3]

        if ssid in passwords and (
            ssid not in found or rssi > found[ssid][1]
        ):
            found[ssid] = (entry[1], rssi)

    if not found:
        return [(ssid, password, None) for ssid, password in NETWORKS]

    ranked = sorted(found, key=lambda ssid: found[ssid][1], reverse=True)

    for ssid in ranked:
        print("WiFi in range:", ssid, found[ssid][1], "dBm")

    return [(ssid, passwords[ssid], found[ssid][0]) for ssid in ranked]


async def _join_one(station, ssid, password, bssid, timeout_s):
    print("Connecting to WiFi:", ssid)

    try:
        if bssid:
            # specific access point - the extender may broadcast the same
            # name as the router
            station.connect(ssid, password, bssid=bssid)
        else:
            station.connect(ssid, password)
    except OSError as e:
        print("WiFi connect error:", e)
        return False

    for second in range(timeout_s):
        if station.isconnected():
            return True

        if second in (4, 9, 14):
            print(
                "WiFi still connecting...",
                second + 1,
                "s"
            )

        await asyncio.sleep(1)

    return station.isconnected()


async def _join_wifi(station, timeout_s=20):
    """Join Wi-Fi without blocking the rest of the asyncio application."""

    global joined_ssid

    if station.isconnected():
        return True

    for ssid, password, bssid in _candidates(station):
        if await _join_one(station, ssid, password, bssid, timeout_s):
            joined_ssid = ssid
            events.log("wifi " + ssid)
            return True

        # before the next network - otherwise ESP-IDF keeps trying the previous one
        try:
            station.disconnect()
        except OSError:
            pass

    return False


def _better_network(station):
    """Name of a known network stronger than the current one by ROAM_MARGIN_DB, or None."""

    try:
        rssi = station.status("rssi")
    except (OSError, ValueError):
        return None

    if rssi >= ROAM_WEAK_RSSI:
        return None

    passwords = dict(NETWORKS)

    try:
        scan = station.scan()
    except OSError as e:
        print("WiFi scan error:", e)
        return None

    best = None
    best_rssi = rssi + ROAM_MARGIN_DB - 1

    for entry in scan:
        try:
            ssid = entry[0].decode()
        except UnicodeError:
            continue

        if ssid in passwords and entry[3] > best_rssi:
            best, best_rssi = ssid, entry[3]

    if best:
        print("WiFi roam:", rssi, "dBm ->", best, best_rssi, "dBm")

    return best


async def connect_wifi():
    global wlan

    station = _new_wlan()

    if await _join_wifi(station):
        wlan = station
        myIp = wlan.ifconfig()[0]
        print("Connection IP:", myIp)
        return wlan

    print(
        "WiFi unavailable at startup; "
        "the application will keep retrying"
    )
    return None


async def wifi_watchdog():
    global wlan

    failed_attempts = 0
    since_roam_check = 0
    roam_interval = ROAM_CHECK_S
    roaming = None

    while True:
        if wlan is not None and wlan.isconnected():
            since_roam_check += 15

            if since_roam_check >= roam_interval:
                since_roam_check = 0

                try:
                    better = _better_network(wlan)
                except Exception as e:
                    print("WiFi roam error:", e)
                    better = None

                if better:
                    # Disconnect only - ESP-IDF rejected reconnecting on the
                    # same interface (connected only after ~20 s). The path
                    # below starts with a new interface,
                    # and its scan picks the strongest network.
                    events.log("wifi roam to " + better)
                    roaming = better
                    wlan.disconnect()

                    await asyncio.sleep(1)

        if wlan is None or not wlan.isconnected():
            if not roaming:
                print("WiFi lost! Reconnecting...")
                # LCD: dashboard shows OFFLINE
                events.log("wifi lost")

            since_roam_check = 0

            try:
                if wlan is not None:
                    wlan.disconnect()
                    wlan.active(False)

                    await asyncio.sleep(1)

                wlan = _new_wlan()

                if await _join_wifi(wlan):
                    failed_attempts = 0

                    if roaming:
                        roam_interval = (
                            ROAM_CHECK_S if joined_ssid == roaming
                            else min(roam_interval * 2, ROAM_CHECK_MAX_S)
                        )

                    print(
                        "WiFi restored:",
                        wlan.ifconfig()[0]
                    )
                    events.log("wifi restored")

                else:
                    failed_attempts += 1

                    delay = min(
                        60,
                        5 * (2 ** min(failed_attempts - 1, 3)))

                    print(
                        "WiFi reconnect failed; retry in",
                        delay,
                        "s"
                    )

                    await asyncio.sleep(delay)

                roaming = None

            except Exception as e:
                roaming = None
                failed_attempts += 1

                print(
                    "WiFi reconnect error:",
                    e
                )

        await asyncio.sleep(15)


def wifi_is_connected():
    return wlan is not None and wlan.isconnected()
