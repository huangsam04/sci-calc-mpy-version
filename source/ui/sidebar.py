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
        self._bat_voltage = "?.?V"
        self._bat_next_read = 0

    def _update_battery(self, now):
        if (self._bat_next_read != 0
                and time.ticks_diff(now, self._bat_next_read) < 0):
            return
        try:
            if self._adc is None:
                self._adc = ADC(Pin(BAT_PIN))
                self._adc.atten(ADC.ATTN_11DB)
            raw = self._adc.read()
            voltage = raw / 4095.0 * 3.3 * 2.0
            self._bat_voltage = f"{voltage:.1f}V"
        except Exception:
            self._bat_voltage = "?.?V"
        self._bat_next_read = time.ticks_add(now, 500)

    def draw(self, display):
        """Erase and redraw every pixel outside the page content area."""
        display.fill_rectangle(CONTENT_W, 0,
                               display.width - CONTENT_W, display.height, 0)
        self._update_battery(time.ticks_ms())
        angle = ("DEG" if self.registry is not None
                 and self.registry.angle_mode else "RAD")

        display.draw_rectangle(SIDEBAR_X, 0, SIDEBAR_W, 63, 15)
        if self.font:
            display.draw_text(SIDEBAR_X + 2, 2, "BAT", self.font, gs=15)
            display.draw_text(SIDEBAR_X + 2, 14,
                              self._bat_voltage, self.font, gs=15)
            display.draw_hline(SIDEBAR_X + 2, 26, 28, 6)
            display.draw_text(SIDEBAR_X + 2, 30,
                              angle, self.font, gs=12)
        else:
            display.draw_text8x8(SIDEBAR_X + 2, 2, "BAT", gs=15)
            display.draw_text8x8(SIDEBAR_X + 2, 14,
                                 self._bat_voltage, gs=15)
            display.draw_text8x8(SIDEBAR_X + 2, 30, angle, gs=12)
