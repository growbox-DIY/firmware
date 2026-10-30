# esp_config.py - pin configuration

PINS = {
    # 5V
    "relay_light": 23,
    "relay_fan": 22,

    # 5V
    "dimmer_pwm": 21,

    # 3.3V
    "sensor_sda": 32,
    "sensor_scl": 33,

    # 5V
    "oled_sda": 18,
    "oled_scl": 19,

    # SD card
    "sd_cs": 4,
    "sd_sck": 27,
    "sd_mosi": 26,
    "sd_miso": 25,

    # LCD button
    "lcd_button": 15,
    "lcd_button_led": 16,
}

DIMMER_DEFAULTS = {
    "enabled": True,
    "day_level": 60,
    "night_level": 30,
    # Calibration (2026-09-26, fan on the current module):
    # at 1 kHz: <=30% stalls, ~32% keeps spinning, ~35% starts from standstill,
    # 75% = full, >=80% unstable (speeds up and slows down).
    # 200 Hz and 5 kHz gave less stable speed.
    "freq": 1000,
    "min_pct": 33,   # level 0 in the app
    "max_pct": 75,   # level 100 in the app
    "kick_pct": 75,  # kick-start when the fan turns on
    "kick_ms": 2000,
}
