"""Idle OLED power control with wake-key input isolation."""
import time


AWAKE = "awake"
SLEEPING = "sleeping"
WOKE = "woke"
LOCKED = "locked"


class DisplayPower:
    """Own the display sleep deadline and safe wake/release sequence."""

    def __init__(self, display, timeout_ms, now=None):
        self.display = display
        self.timeout_ms = max(0, int(timeout_ms))
        self.sleeping = False
        self._wake_locked = False
        self._last_activity = time.ticks_ms() if now is None else now

    def update(self, now, key_pressed):
        if self.sleeping:
            if not key_pressed:
                return SLEEPING
            self.display.wake()
            self.sleeping = False
            self._wake_locked = True
            self._last_activity = now
            return WOKE

        if self._wake_locked:
            if key_pressed:
                return LOCKED
            self._wake_locked = False
            self._last_activity = now
            return AWAKE

        if key_pressed:
            self._last_activity = now
            return AWAKE

        if (self.timeout_ms > 0
                and time.ticks_diff(now, self._last_activity) >= self.timeout_ms):
            self.display.sleep()
            self.sleeping = True
            return SLEEPING
        return AWAKE

    def reset(self, now=None):
        """Synchronize software and OLED state after exceptional recovery."""
        if self.sleeping:
            self.display.wake()
        self.sleeping = False
        self._wake_locked = False
        self._last_activity = time.ticks_ms() if now is None else now
