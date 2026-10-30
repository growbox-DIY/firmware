# Growbox firmware

MicroPython firmware for an ESP32 that runs a small grow box, terrarium, or aquarium. It 
switches the light and fan, controls fan speed, logs environmental data to an SD card, 
and exposes everything through a small HTTP API.

The phone app lives in [app](https://github.com/growbox-DIY/app),
and a macOS desktop widget in `growbox-widget-macos`.

## Features

- **Light schedule** (Auto mode): the light switches on and off by the
  clock. The fan follows the light, with an optional night fan and a
  separate night speed.
- **Auto fan** (Manual / veg mode): the fan speed follows the
  temperature, with separate targets for lights on and lights off. It
  stays on the lowest speed up to the middle of the band. Readings are
  rounded to 10 % steps with hysteresis, so sensor noise does not make
  the fan hunt.
- **Auto fan in flowering** (Auto mode, lights on): the same curve with its
  own targets (`fanAutoFlower`), since the lamp runs hotter. With the
  lights off and the night fan on, the fan runs at the fixed night level
  from the app.
- **Flowering start time:** the exact moment flowering began is stored
  next to the date, so the app's chart can mark it.
- **Fan dimmer:** calibrated PWM range, plus a short kick-start so the
  fan spins up from standstill at low speeds.
- **Climate log:** temperature and humidity go to the SD card every
  5 min. It is readable as JSON pages, as an incremental sync or as CSV.
- **Watering log** stored on the device (`POST /api/feeding`), with undo.
- **OLED display** with a hardware button to switch it on and off:
  - boot screen with the current step (WiFi, IP address, clock, SD card)
    and a progress bar;
  - dashboard with large temperature and humidity, WiFi signal bars, SD
    card and light and fan state;
  - a static screen during OTA updates (receiving, installing,
    verifying).

  A display switched off stays dark during boot and updates.
- **OTA updates over WiFi** with automatic rollback. See below.
- **Self-healing**
  - Hardware watchdog.
  - Supervised tasks that restart after a crash.
  - Reset when the ESP-IDF heap runs low.
  - Relay pins re-asserted periodically.
  - Clean reset if the HTTP server cannot rebind its port.

## Hardware

![wiring](docs/wiring.png)

| Part                      | Pin(s)                          |
| ------------------------- | ------------------------------- |
| Light relay               | GPIO 23                         |
| Fan relay                 | GPIO 22                         |
| Fan dimmer (PWM, 1 kHz)   | GPIO 21                         |
| SHT4x sensor (I2C 0x44)   | SDA 32, SCL 33                  |
| SSD1306 OLED (I2C)        | SDA 18, SCL 19                  |
| SD card (SPI)             | CS 4, SCK 27, MOSI 26, MISO 25  |
| Display button + its LED  | GPIO 15, GPIO 16                |

Pins and dimmer calibration live in `src/esp_config.py`.

### Mains (230 V)

![mains wiring](docs/wiring-mains.png)

The relays switch the phase (L) of the lamp and the fan; the fan goes through the PWM dimmer.

## First install (USB)

1. Flash MicroPython 1.27. Download the `ESP32_GENERIC` image from
   [micropython.org](https://micropython.org/download/ESP32_GENERIC/):

   ```bash
   esptool.py --chip esp32 erase_flash
   esptool.py --chip esp32 write_flash -z 0x1000 ESP32_GENERIC-20251209-v1.27.0.bin
   ```

2. Create `src/secrets.py` with your WiFi credentials. The file is not
   committed.

   ```python
   SSID = "your-network"
   PASSWORD = "your-password"
   ```

   The board joins the strongest network in range out of `SSID` and
   `SSID + "_EXT"` (the default name of a TP-Link range extender), so it
   falls back to the router when the extender is off. While the signal
   is weak it rescans every 10 minutes and moves to a known network at
   least 10 dB stronger, so it returns to the extender once it is back. Other names go in
   an optional list:

   ```python
   NETWORKS = (("your-network", "your-password"),
               ("your-extender", "its-password"))
   ```

3. Create `src/api_token.py` from `src/api_token_example.py` with your
   own long random token. Every API call needs it, and the app, the
   widgets and the monitor use the same one. It is not committed either,
   but unlike `secrets.py` it goes to the board with the rest of the app.

4. Copy the app to the board:

   ```bash
   mpremote connect /dev/cu.usbserial-0001 cp src/*.py :
   ```

   The first `./deploy.py` afterwards replaces the sources with
   precompiled `.mpy` modules (see below).

`main.py`, `boot.py` and `secrets.py` can only be changed over USB. OTA
does not touch them, so a broken update can never lock you out.

## Updates over WiFi

```bash
./deploy.py              # send changed files from src/, restart, wait for confirm
./deploy.py --dry-run    # only show what changed
./deploy.py sensor.py    # send selected files even if unchanged
```

Modules go to the board precompiled with `mpy-cross`, so the ESP does not
compile sources at boot and keeps more RAM free. `mpy-cross` has to match
the MicroPython on the board (1.27):

```bash
pipx install mpy-cross==1.27.0.post2
```

How an update works:

1. Files are uploaded as `.new`, checked with sha256 and swapped in on
   restart. A leftover `X.py` would shadow the new `X.mpy`, so it is
   moved to `.bak` as well.
2. The new version confirms itself after 60 s of healthy running.
3. If it does not confirm within 3 boots, `main.py` restores the
   previous files.

`ESP_URL` changes the address (default `http://192.168.18.85`). The token
is read from `src/api_token.py`.

## HTTP API

Every call needs `Authorization: Bearer <API_TOKEN>`.

| Method | Path                            | What it does                                |
| ------ | ------------------------------- | ------------------------------------------- |
| GET    | `/api/config`                   | Config, sensor reading, mode, fan level, WiFi RSSI |
| GET    | `/api/status`                   | Uptime, memory, WiFi RSSI, recent events    |
| POST   | `/api/mode/toggle`              | Switch Auto / Manual                        |
| POST   | `/api/light/toggle`             | Light relay                                 |
| POST   | `/api/fan/toggle`               | Fan relay                                   |
| POST   | `/api/fan/level`                | `{"level": 0-100}` day fan speed            |
| POST   | `/api/fan/night-level`          | `{"level": 0-100}` night fan speed          |
| POST   | `/api/fan/auto/toggle`          | Auto fan by temperature (Manual mode)       |
| POST   | `/api/auto/night-fan/toggle`    | Keep the fan on at night (Auto mode)        |
| POST   | `/api/light-schedule`           | `[{"on": "18:00", "off": "06:00"}]`         |
| POST   | `/api/flowering/start-date`     | `{"date": "YYYY-MM-DD" \| null, "startedAt"?}` (time defaults to now) |
| POST   | `/api/humidifier`               | `{"mode": "manual"\|"auto", "enabled", "min", "max"}` and/or `{"fan": {"maxLevel", "tempLimit"}}` |
| POST   | `/api/feeding`                  | `{"date": ISO}` log a watering              |
| POST   | `/api/feeding/undo`             | `{"date": ISO}` remove it (no date = last)  |
| POST   | `/api/display/toggle`           | OLED on / off                               |
| POST   | `/api/monitor`                  | `{"enabled", "brightness": 5-100}` TFT monitor screen; `status.monitorOnline` in `/api/config` says whether it polled in the last minute |
| GET    | `/api/environment`              | Climate log: `limit`, `before`, `since`, `step` |
| GET    | `/api/environment.csv`          | Climate log as CSV                          |
| POST   | `/api/environment/erase`        | Clear the climate log                       |
| GET    | `/api/update`                   | OTA state and file checksums (`?files=a.mpy,b.mpy`) |
| PUT    | `/api/files/<name>?sha256=...`  | Stage a `.py` / `.mpy` file for OTA         |
| POST   | `/api/update/apply`             | Apply staged files and restart              |
| POST   | `/api/update/discard`           | Drop staged files                           |
| POST   | `/api/debug/dimmer`             | `{"duty": 0-1023, "freq"}` raw PWM, for calibration |

Requests are handled one at a time. The ESP has little RAM, and parallel
requests used to exhaust it.

## Layout

```
src/            firmware (copied to the board root)
  app.py        startup, task supervisor, watchdog
  server.py     HTTP API, relays, schedule
  fan_auto.py   fan speed from temperature (Manual and flowering by day)
  humidifier.py humidifier decision + ESP-NOW link to the mini ESP
  ota.py        OTA staging, apply, confirm (ota_http.py = its routes)
  sd_logger.py  climate log on the SD card
  ...
deploy.py       OTA client for your computer
```

## License

This project is licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/):
you may use, share and modify it for non-commercial purposes, with credit to
growbox-DIY. See LICENSE.
