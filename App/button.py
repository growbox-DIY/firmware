# button.py
import micropython
import utime
from machine import Pin


class Button:
    def __init__(self, pin, on_release=None, debounce_ms=300):
        self._pin = Pin(pin, Pin.IN, Pin.PULL_UP)
        self._on_press = on_release
        self._debounce_ms = debounce_ms
        self._last_press = 0
        self._scheduled = False
        self._scheduled_callback = self._run_callback
        self._pin.irq(trigger=Pin.IRQ_FALLING, handler=self._handler)

    def _handler(self, pin):
        # This is a hardware interrupt.  Do not run application code here:
        # it may write flash, use I2C, or allocate memory.  Schedule it for
        # normal MicroPython execution instead.
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self._last_press) < self._debounce_ms:
            return
        self._last_press = now
        if self._scheduled:
            return
        self._scheduled = True
        try:
            micropython.schedule(self._scheduled_callback, 0)
        except RuntimeError:
            # The scheduler queue is temporarily full; ignore this press.
            self._scheduled = False

    def _run_callback(self, _):
        self._scheduled = False
        if self._on_press:
            self._on_press()
