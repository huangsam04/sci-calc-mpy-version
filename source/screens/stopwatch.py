"""Stopwatch screen."""
import time
from ui.element import UIElement
from input.keyboard import get_key_label


class StopwatchScreen(UIElement):
    def __init__(self, font):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self._running = False
        self._paused = False
        self._start_time = 0
        self._elapsed = 0
        self._laps = []
        self._last_action = 0      # ponytail: cooldown timer for debounce

    def activate(self):
        self._last_action = 0

    def _start(self):
        if self._paused:
            self._start_time = time.ticks_ms() - self._elapsed
            self._paused = False
        else:
            self._start_time = time.ticks_ms()
        self._running = True

    def _pause(self):
        if self._running:
            self._elapsed = time.ticks_diff(time.ticks_ms(), self._start_time)
            self._running = False
            self._paused = True

    def _reset(self):
        self._running = False
        self._paused = False
        self._elapsed = 0
        self._laps = []

    def _lap(self):
        if self._running:
            elapsed = time.ticks_diff(time.ticks_ms(), self._start_time)
            self._laps.append((len(self._laps) + 1, elapsed))

    def _get_elapsed(self):
        if self._running:
            return time.ticks_diff(time.ticks_ms(), self._start_time)
        return self._elapsed

    def _fmt(self, ms):
        neg = ms < 0
        ms = abs(ms)
        total_s = ms // 1000
        cs = (ms % 1000) // 10
        h = total_s // 3600
        m = (total_s % 3600) // 60
        s = total_s % 60
        sign = "-" if neg else ""
        return f"{sign}{h:02d}:{m:02d}:{s:02d}:{cs:02d}"

    def draw(self, display):
        if self.font:
            display.draw_text(2, 0, "Stopwatch", self.font, gs=15)
        else:
            display.draw_text8x8(2, 0, "Stopwatch", gs=15)
        display.draw_hline(0, 11, 210, 15)

        elapsed = self._get_elapsed()
        time_str = self._fmt(elapsed)
        if self.font:
            display.draw_text(60, 16, time_str, self.font, gs=15)
        else:
            display.draw_text8x8(60, 16, time_str, gs=15)

        visible = min(len(self._laps), 3)
        lap_start = 28
        for i in range(visible):
            idx = len(self._laps) - visible + i
            if 0 <= idx < len(self._laps):
                n, t = self._laps[idx]
                s = f"Lap{n}: {self._fmt(t)}"
                if self.font:
                    display.draw_text(5, lap_start + i * 10, s, self.font, gs=15)
                else:
                    display.draw_text8x8(5, lap_start + i * 10, s, gs=15)

        if self._running:
            hint = "ENT:Pause  DEL:Lap"
        else:
            hint = "ENT:Start  DEL:Reset"
        if self.font:
            display.draw_text(2, 54, hint, self.font, gs=15)
        else:
            display.draw_text8x8(2, 54, hint, gs=15)

    def update(self, kb):
        key = kb.get_rising_edge()
        if key is None:
            return None

        r, c = key
        label = get_key_label(r, c, kb.is_pressed(4, 0))

        # ESC: always go back (no long-hold needed for stopwatch)
        if label == "ESC":
            return "BACK"

        # ponytail: 200ms cooldown to prevent contact-bounce double-trigger
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_action) < 200:
            return None
        self._last_action = now

        if label == "ENT":
            if self._running:
                self._pause()
            else:
                self._start()
        elif label == "DEL":
            if self._running:
                self._lap()
            else:
                self._reset()

        return None
