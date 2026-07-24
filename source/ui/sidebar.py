"""Fixed status sidebar for the SCI-CALC display."""
import time
from machine import ADC, Pin

from ui.theme import CONTENT_W, SIDEBAR_X, SIDEBAR_W


BAT_PIN = 36


class Sidebar:
    """Draw fixed status chrome and own its slowly-changing battery state."""

    def __init__(self, font=None, registry=None):
        self.font = font
        self.registry = registry
        self._adc = None
        self._bat_voltage = bytearray(b"?.?V")
        self._bat_next_read = 0
        self._last_angle = None

    def _update_battery(self, now):
        if (self._bat_next_read != 0
                and time.ticks_diff(now, self._bat_next_read) < 0):
            return False
        previous_tens = self._bat_voltage[0]
        previous_tenths = self._bat_voltage[2]
        try:
            if self._adc is None:
                self._adc = ADC(Pin(BAT_PIN))
                self._adc.atten(ADC.ATTN_11DB)
            raw = self._adc.read()
            tenths = min(99, (raw * 66 + 2047) // 4095)
            self._bat_voltage[0] = 48 + tenths // 10
            self._bat_voltage[1] = 46
            self._bat_voltage[2] = 48 + tenths % 10
            self._bat_voltage[3] = 86
        except Exception:
            self._bat_voltage[0] = 63
            self._bat_voltage[1] = 46
            self._bat_voltage[2] = 63
            self._bat_voltage[3] = 86
        self._bat_next_read = time.ticks_add(now, 500)
        return (self._bat_voltage[0] != previous_tens
                or self._bat_voltage[2] != previous_tenths)

    def refresh_needed(self, now=None):
        """Update slow status state and report whether sidebar pixels changed."""
        if now is None:
            now = time.ticks_ms()
        battery_changed = self._update_battery(now)
        angle = bool(self.registry is not None
                     and self.registry.angle_mode)
        angle_changed = angle != self._last_angle
        self._last_angle = angle
        return battery_changed or angle_changed

    def draw(self, display, refresh=True):
        """Erase and redraw every pixel outside the page content area."""
        changed = self.refresh_needed() if refresh else False
        angle = (b"DEG" if self.registry is not None
                 and self.registry.angle_mode else b"RAD")
        display.fill_rectangle(CONTENT_W, 0,
                               display.width - CONTENT_W, display.height, 0)

        display.draw_rectangle(SIDEBAR_X, 0, SIDEBAR_W, 63, 15)
        if self.font:
            display.draw_text_direct(
                SIDEBAR_X + 2, 2, b"BAT", self.font, gs=15)
            display.draw_text_direct(
                SIDEBAR_X + 2, 14, self._bat_voltage, self.font, gs=15)
            display.draw_hline(SIDEBAR_X + 2, 26, 28, 6)
            display.draw_text_direct(
                SIDEBAR_X + 2, 30, angle, self.font, gs=12)
        else:
            display.draw_text8x8(SIDEBAR_X + 2, 2, "BAT", gs=15)
            display.draw_text8x8(SIDEBAR_X + 2, 14,
                                 str(self._bat_voltage, "ascii"), gs=15)
            display.draw_text8x8(
                SIDEBAR_X + 2, 30, str(angle, "ascii"), gs=12)
        return changed
