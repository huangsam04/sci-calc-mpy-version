"""Stopwatch screen — large timer, scrollable lap list (newest first)."""
import time
from ui.element import UIElement
from input.keyboard import get_key_label
from ui.residency import SETTLE_MORE, SETTLE_REDRAW
from ui.theme import SHELL_STOPWATCH, draw_footer, draw_page_shell

LAP_H = 9        # row height for lap entries
LAP_COUNT = 4    # visible lap rows
LAP_MAX = 99     # cap stored laps so a long session cannot exhaust RAM


class StopwatchScreen(UIElement):
    swap_key = "stopwatch"
    transition_title = "Stopwatch"

    def __init__(self, font):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self._running = False
        self._paused = False
        self._start_time = 0
        self._elapsed = 0
        self._laps = []          # list of (lap_number, elapsed_ms)
        self._lap_cursor = 0     # selected lap index (0 = newest)
        self._view_offset = 0    # first visible lap index
        self._last_action = 0
        self._last_key = ""
        self._next_lap_num = 1   # next lap number to assign
        self._lap_restore = None

    def activate(self):
        self._last_action = 0
        self._last_key = ""

    def snapshot_state(self):
        return {
            "running": bool(self._running),
            "paused": bool(self._paused),
            "elapsed": int(self._get_elapsed()),
            "start_time": int(self._start_time) if self._running else 0,
            "laps": [[int(number), int(elapsed)]
                     for number, elapsed in self._laps[:LAP_MAX]],
            "cursor": self._lap_cursor,
            "view": self._view_offset,
            "next_lap": self._next_lap_num,
        }

    def reset_state(self):
        self._reset()
        self._last_action = 0
        self._last_key = ""

    def activate_default(self):
        self._last_action = 0
        self._last_key = ""

    def restore_state(self, state):
        laps = state.get("laps", [])
        if not isinstance(laps, list) or len(laps) > LAP_MAX:
            raise ValueError("Invalid stopwatch snapshot")
        elapsed = max(0, int(state.get("elapsed", 0)))
        self._elapsed = elapsed
        self._running = bool(state.get("running", False))
        self._paused = bool(state.get("paused", False)) and not self._running
        if self._running:
            now = time.ticks_ms()
            saved_start = int(state.get(
                "start_time", time.ticks_add(now, -elapsed)))
            live_elapsed = time.ticks_diff(now, saved_start)
            if live_elapsed < elapsed:
                live_elapsed = elapsed
            self._elapsed = live_elapsed
            self._start_time = time.ticks_add(now, -live_elapsed)
        else:
            self._start_time = 0
        self._laps = []
        self._lap_restore = laps
        self._lap_cursor = max(0, int(state.get("cursor", 0)))
        self._view_offset = max(0, int(state.get("view", 0)))
        self._next_lap_num = max(1, int(state.get("next_lap", 1)))

    def settle_step(self):
        rows = self._lap_restore
        if rows is None:
            return 0
        index = len(self._laps)
        if index >= len(rows):
            self._lap_restore = None
            return 0
        row = rows[index]
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("Invalid stopwatch lap")
        self._laps.append((int(row[0]), int(row[1])))
        if len(self._laps) < len(rows):
            return SETTLE_REDRAW | SETTLE_MORE
        self._lap_restore = None
        return SETTLE_REDRAW

    def draw_transition_default(self, display):
        draw_page_shell(display, SHELL_STOPWATCH, self.font)

    # ── timer logic ─────────────────────────────────────────────

    def _start(self):
        if self._paused:
            self._start_time = time.ticks_add(time.ticks_ms(), -self._elapsed)
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
        self._lap_cursor = 0
        self._view_offset = 0
        self._next_lap_num = 1
        self._lap_restore = None

    def _lap(self):
        if self._running:
            elapsed = time.ticks_diff(time.ticks_ms(), self._start_time)
            self._laps.insert(0, (self._next_lap_num, elapsed))  # newest first
            self._next_lap_num += 1
            if len(self._laps) > LAP_MAX:
                del self._laps[LAP_MAX:]  # drop oldest beyond the cap

    def _get_elapsed(self):
        if self._running:
            return time.ticks_diff(time.ticks_ms(), self._start_time)
        return self._elapsed

    @staticmethod
    def _fmt(ms):
        ms = abs(ms)
        h = ms // 3600000
        m = (ms % 3600000) // 60000
        s = (ms % 60000) // 1000
        cs = (ms % 1000) // 10
        if h:
            return f"{h}:{m:02d}:{s:02d}:{cs:02d}"
        return f"{m:02d}:{s:02d}:{cs:02d}"

    # ── drawing ─────────────────────────────────────────────────

    def draw(self, display):
        elapsed = self._get_elapsed()
        time_str = self._fmt(elapsed)

        # Large centered timer (y=2) — raw=True avoids per-frame string cache alloc
        if self.font:
            tw = self.font.measure_text(time_str)
            tx = max(2, (self.width - tw) // 2)
            display.draw_text(tx, 2, time_str, self.font, gs=15, raw=True)
        else:
            display.draw_text8x8(60, 2, time_str, gs=15)

        # Divider
        display.draw_hline(0, 12, self.width, 15)

        # Lap list (y=14-51, 4 rows × 9px = 36px)
        lap_top = 14
        total = len(self._laps)
        if total:
            self._lap_cursor = max(0, min(self._lap_cursor, total - 1))
            # Scroll view to keep cursor visible
            if self._lap_cursor < self._view_offset:
                self._view_offset = self._lap_cursor
            elif self._lap_cursor >= self._view_offset + LAP_COUNT:
                self._view_offset = self._lap_cursor - LAP_COUNT + 1
            self._view_offset = max(0, min(self._view_offset, max(0, total - LAP_COUNT)))
        else:
            self._lap_cursor = 0
            self._view_offset = 0

        for i in range(self._view_offset, min(self._view_offset + LAP_COUNT, total)):
            row = i - self._view_offset
            y = lap_top + row * LAP_H
            n, t = self._laps[i]
            label = f"Lap{n}:  {self._fmt(t)}"
            is_selected = (i == self._lap_cursor)

            if is_selected:
                fh = self.font.height if self.font else 8
                display.fill_rectangle(2, y, self.width - 4, fh, 14)
                if self.font:
                    display.draw_text(4, y, label, self.font, invert=True, gs=14)
                else:
                    display.draw_text8x8(4, y, label, gs=0)
            else:
                if self.font:
                    display.draw_text(4, y, label, self.font, gs=15)
                else:
                    display.draw_text8x8(4, y, label, gs=15)

        if self._running:
            hint = "ENT pause"
            right = "DEL lap"
        elif self._paused:
            hint = "ENT resume"
            right = "DEL reset"
        else:
            hint = "ENT start"
            right = "DEL reset"
        draw_footer(display, hint, self.font, right)

    # ── input ───────────────────────────────────────────────────

    def update(self, kb, event=None):
        if event is None:
            return None

        r, c, shift = event
        label = get_key_label(r, c, shift)

        if label == "ESC":
            return "BACK"

        now = time.ticks_ms()
        if label == self._last_key and time.ticks_diff(now, self._last_action) < 200:
            return None
        self._last_action = now
        self._last_key = label

        # Navigation: scroll lap list
        if label in ("up", "8"):
            if self._laps and self._lap_cursor > 0:
                self._lap_cursor -= 1
        elif label in ("down", "2"):
            if self._lap_cursor < len(self._laps) - 1:
                self._lap_cursor += 1
        elif label == "ENT":
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
