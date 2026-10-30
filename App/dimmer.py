# dimmer.py
import uasyncio as asyncio
from machine import Pin, PWM


def _pct_to_duty(pct):
    return int(pct * 1023 / 100)


class Dimmer:
    """
    Level 0..100 from the app -> PWM duty in the calibrated range
    [min_pct, max_pct]. Outside this range the fan stalls or (above
    ~80% duty) enters an unstable zone, so it is unreachable.
    The relay turns the fan off, not the dimmer.
    """

    def __init__(
            self,
            pin,
            enabled=True,
            level=0,
            freq=1000,
            min_pct=33,
            max_pct=75,
            kick_pct=75,
            kick_ms=2000,
            on_change=None,
    ):
        self._pwm = PWM(Pin(pin), freq=freq)
        self._enabled = enabled
        self._level = max(0, min(100, level))
        self._min_pct = min_pct
        self._max_pct = max_pct
        self._kick_pct = kick_pct
        self._kick_ms = kick_ms
        self._kicking = False
        self._on_change = on_change
        self._apply()

    def _duty(self):
        pct = self._min_pct + (self._max_pct - self._min_pct) * self._level / 100
        return _pct_to_duty(pct)

    def _apply(self):
        # during kick-start the level is applied after it finishes
        if self._kicking:
            return

        if not self._enabled:
            self._pwm.duty(0)
            return

        self._pwm.duty(self._duty())

    def set_level(self, level):
        if not isinstance(level, (int, float)):
            return
        level = max(0, min(100, level))
        if self._level == level:
            return
        self._level = level
        self._apply()
        if self._on_change:
            self._on_change(self._level)

    def kick(self):
        """
        Kick-start: briefly stronger, then the set level.
        From standstill the fan starts only from ~35% duty,
        but keeps spinning from ~32%.
        """
        if not self._enabled or self._kicking:
            return

        try:
            asyncio.create_task(self._kick())
        except Exception as e:
            print("Dimmer kick error:", e)

    async def _kick(self):
        self._kicking = True

        try:
            self._pwm.duty(_pct_to_duty(self._kick_pct))
            await asyncio.sleep_ms(self._kick_ms)
        finally:
            self._kicking = False
            self._apply()

    def set_raw(self, duty=None, freq=None):
        """
        Calibration: raw duty 0..1023 (and optionally frequency),
        bypassing the mapping. Does not change the stored level -
        the next set_level() with a different level overrides it.
        """
        if freq is not None:
            self._pwm.freq(freq)
        if duty is not None:
            self._pwm.duty(max(0, min(1023, duty)))
        return self._pwm.duty(), self._pwm.freq()

    def get_level(self):
        return self._level

    def enable(self):
        if self._enabled:
            return
        self._enabled = True
        self._apply()

    def disable(self):
        if not self._enabled:
            return
        self._enabled = False
        self._pwm.duty(0)

    def is_enabled(self):
        return self._enabled
