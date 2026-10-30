# relay.py
from machine import Pin


class Relay:
    def __init__(self, pin, state=0, on_change=None):
        self._pin = Pin(pin, Pin.OUT)
        self._on_change = on_change
        self._state = 1 if state else 0

        # set physical state once, based on logical state
        self._pin.value(self._state)

    def on(self):
        if self._state == 1:
            return
        self._state = 1
        self._pin.value(1)
        if self._on_change:
            self._on_change(self._state)

    def off(self):
        if self._state == 0:
            return
        self._state = 0
        self._pin.value(0)
        if self._on_change:
            self._on_change(self._state)

    def toggle(self):
        print("Relay toggle pin: ", self._pin, " state: ", self._state, "")
        if self._state == 0:
            self.on()
        else:
            self.off()

    def set_state(self, state):
        self.on() if state else self.off()

    def get_state(self):
        return self._state

    def refresh(self):
        """
        Forces the logical state on the pin. Returns True if the pin had
        a different level (we only see the ESP pin, not the relay contact).
        """
        wrong = self._pin.value() != self._state
        self._pin.value(self._state)
        return wrong
