"""Stopwatch screen — large timer, scrollable lap list (newest first)."""
import time
from ui.element import UIElement
from input.keyboard import get_key_label
from ui.motion import DAMAGE_FULL, DAMAGE_NONE, DAMAGE_PARTIAL
from ui.theme import (draw_footer_cached, draw_text, fit_text,
                      get_direct_text_draw, text_width)

LAP_H = 9        # row height for lap entries
LAP_COUNT = 4    # visible lap rows
LAP_MAX = 20     # bounded RAM snapshot while the heavy page is unloaded
STOPWATCH_FRAME_MS = 50
_TIMER_BAND_H = 13
_SHORT_TIME_X = 81
_LONG_TIME_X = 72
_EXTENDED_TIME_X = 69
_FOOTER_IDLE_HINT = "ENT start"
_FOOTER_RUNNING_HINT = "ENT pause"
_FOOTER_PAUSED_HINT = "ENT resume"
_FOOTER_LAP_RIGHT = "DEL lap"
_FOOTER_RESET_RIGHT = "DEL reset"

# a,b,c,d,e,f,g for the fixed built-in fallback digits.  This keeps the
# degraded-font timer path allocation-free instead of formatting a string
# every 50 ms.
_TIMER_DIGIT_SEGMENTS = (
    0x3F, 0x06, 0x5B, 0x4F, 0x66,
    0x6D, 0x7D, 0x07, 0x7F, 0x6F)


_StopwatchScenarioLease = None
_StopwatchPageScenarioLease = None


class StopwatchScreen(UIElement):
    transition_title = "Stopwatch"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("_clock", "_render", "_footer", "_runtime")

    def __init__(self, font, retained_state=None):
        if retained_state is None:
            self._clock = [
                font,
                False,
                [False, 0, 0, []],  # paused/start/elapsed/laps
                [0, 0, 1, 0],       # cursor/view/next number/revision
            ]
        else:
            self._clock = retained_state
            self._clock[0] = font
        # The running timer uses one of these fixed ASCII buffers directly;
        # it never constructs an f-string for a steady timer frame.
        self._render = (
            [bytearray(b"00:00:00"), bytearray(b"00:00:00:00"),
             bytearray(b"000:00:00:00")],
            [None, None, None, None],
            [None, None],
        )
        # Keep one status footer, rather than a cache keyed by every timer or
        # lap value.  The 20 fps timer path can then redraw its top band
        # without formatting or encoding footer text.
        self._footer = (
            ["", b"", "", b""],
            [0, None],
        )
        # Only the four visible labels are retained.  Keeping the full
        # twenty-lap history as display strings would trade frame churn for a
        # larger permanent heap footprint.
        self._runtime = (
            [[None, None, None, None], None, None, None],
            [None, None, None],
        )
        self._ensure_footer_cache()

    def activate(self):
        pass

    def release_memory(self):
        released = False
        labels = self._runtime[0][0]
        index = 0
        while index < len(labels):
            if labels[index] is not None:
                labels[index] = None
                released = True
            index += 1
        runtime = self._runtime
        if (runtime[0][1] is not None or runtime[0][2] is not None
                or runtime[0][3] is not None or runtime[1][0] is not None):
            released = True
        runtime[0][1] = None
        runtime[0][2] = None
        runtime[0][3] = None
        runtime[1][0] = None
        return released

    def detach_state(self):
        """Move the running clock/lap state out of the disposable page."""
        if (self._runtime[1][1] is not None
                or self._runtime[1][2] is not None):
            raise RuntimeError("Stopwatch scenario transaction is active")
        self.release_memory()
        clock = self._clock
        self._clock = None
        self._render = None
        self._footer = None
        self._runtime = None
        return clock

    def open_scenario_lease(self):
        """Open a no-copy checkpoint for a future bounded acceptance session."""
        if (self._runtime[1][1] is not None
                or self._runtime[1][2] is not None):
            raise RuntimeError("Stopwatch scenario lease is already active")
        lease_type = _StopwatchScenarioLease
        if lease_type is None:
            from screens.stopwatch_scenario import (
                StopwatchScenarioLease as lease_type)
        return lease_type(self)

    def open_page_scenario_transaction(self):
        """Open the controller-only one-step Stopwatch page activation lease."""
        if (self._runtime[1][1] is not None
                or self._runtime[1][2] is not None):
            raise RuntimeError("Stopwatch page scenario transaction is already active")
        lease_type = _StopwatchPageScenarioLease
        if lease_type is None:
            from screens.stopwatch_scenario import (
                StopwatchPageScenarioLease as lease_type)
        return lease_type(self)

    # ── timer logic ─────────────────────────────────────────────

    def _start(self):
        if self._clock[1]:
            return False
        if self._clock[2][0]:
            self._clock[2][1] = time.ticks_add(time.ticks_ms(), -self._clock[2][2])
            self._clock[2][0] = False
        else:
            self._clock[2][1] = time.ticks_ms()
        self._clock[1] = True
        return True

    def _pause(self):
        if self._clock[1]:
            self._clock[2][2] = time.ticks_diff(time.ticks_ms(), self._clock[2][1])
            self._clock[1] = False
            self._clock[2][0] = True
            return True
        return False

    def _reset(self):
        changed = bool(self._clock[1] or self._clock[2][0] or self._clock[2][2]
                       or self._clock[2][3] or self._clock[3][0] or self._clock[3][1]
                       or self._clock[3][2] != 1)
        self._clock[1] = False
        self._clock[2][0] = False
        self._clock[2][2] = 0
        self._clock[2][3] = []
        self._clock[3][0] = 0
        self._clock[3][1] = 0
        self._clock[3][2] = 1
        if changed:
            self._clock[3][3] += 1
            # Release the bounded rendered page immediately instead of
            # retaining old lap strings until a later full redraw.
            labels = self._runtime[0][0]
            labels[0] = None
            labels[1] = None
            labels[2] = None
            labels[3] = None
            self._runtime[0][1] = None
            self._runtime[0][2] = None
            self._runtime[0][3] = None
            self._runtime[1][0] = None
        return changed

    def _lap(self):
        if self._clock[1]:
            elapsed = time.ticks_diff(time.ticks_ms(), self._clock[2][1])
            self._clock[2][3].insert(0, (self._clock[3][2], elapsed))  # newest first
            self._clock[3][2] += 1
            if len(self._clock[2][3]) > LAP_MAX:
                del self._clock[2][3][LAP_MAX:]  # drop oldest beyond the cap
            self._clock[3][3] += 1
            return True
        return False

    def _get_elapsed(self):
        if self._clock[1]:
            return time.ticks_diff(time.ticks_ms(), self._clock[2][1])
        return self._clock[2][2]

    @staticmethod
    def _put_pair(target, index, value):
        target[index] = 48 + value // 10
        target[index + 1] = 48 + value % 10

    @staticmethod
    def _put_triplet(target, index, value):
        target[index] = 48 + value // 100
        target[index + 1] = 48 + (value // 10) % 10
        target[index + 2] = 48 + value % 10

    @staticmethod
    def _draw_builtin_time(display, text, x):
        """Render the fixed ASCII time buffer without FrameBuffer.text()."""
        index = 0
        length = len(text)
        while index < length:
            code = text[index]
            if code == 58:  # ':'
                display.fill_rectangle(x + 1, 2, 1, 1, 15)
                display.fill_rectangle(x + 1, 6, 1, 1, 15)
                x += 3
            else:
                mask = _TIMER_DIGIT_SEGMENTS[code - 48]
                if mask & 0x01:
                    display.fill_rectangle(x + 1, 0, 4, 1, 15)
                if mask & 0x02:
                    display.fill_rectangle(x + 5, 1, 1, 3, 15)
                if mask & 0x04:
                    display.fill_rectangle(x + 5, 5, 1, 3, 15)
                if mask & 0x08:
                    display.fill_rectangle(x + 1, 8, 4, 1, 15)
                if mask & 0x10:
                    display.fill_rectangle(x, 5, 1, 3, 15)
                if mask & 0x20:
                    display.fill_rectangle(x, 1, 1, 3, 15)
                if mask & 0x40:
                    display.fill_rectangle(x + 1, 4, 4, 1, 15)
                x += 7
            index += 1

    def _draw_time(self, display, elapsed):
        """Update fixed ASCII digits and draw only the current timer text."""
        elapsed = abs(elapsed)
        hours = elapsed // 3600000
        minutes = (elapsed % 3600000) // 60000
        seconds = (elapsed % 60000) // 1000
        centiseconds = (elapsed % 1000) // 10
        if hours >= 100:
            text = self._render[0][2]
            self._put_triplet(text, 0, min(999, hours))
            self._put_pair(text, 4, minutes)
            self._put_pair(text, 7, seconds)
            self._put_pair(text, 10, centiseconds)
            x = _EXTENDED_TIME_X
        elif hours:
            text = self._render[0][1]
            self._put_pair(text, 0, hours)
            self._put_pair(text, 3, minutes)
            self._put_pair(text, 6, seconds)
            self._put_pair(text, 9, centiseconds)
            x = _LONG_TIME_X
        else:
            text = self._render[0][0]
            self._put_pair(text, 0, minutes)
            self._put_pair(text, 3, seconds)
            self._put_pair(text, 6, centiseconds)
            x = _SHORT_TIME_X
        if self._clock[0]:
            direct = get_direct_text_draw(display)
            if direct is not None:
                direct(display, x, 2, text, self._clock[0], gs=15)
                return
        self._draw_builtin_time(display, text, x)

    def _clamp_lap_view(self):
        total = len(self._clock[2][3])
        if total:
            self._clock[3][0] = max(0, min(self._clock[3][0], total - 1))
            if self._clock[3][0] < self._clock[3][1]:
                self._clock[3][1] = self._clock[3][0]
            elif self._clock[3][0] >= self._clock[3][1] + LAP_COUNT:
                self._clock[3][1] = self._clock[3][0] - LAP_COUNT + 1
            self._clock[3][1] = max(
                0, min(self._clock[3][1], max(0, total - LAP_COUNT)))
        else:
            self._clock[3][0] = 0
            self._clock[3][1] = 0

    def _footer_state(self):
        if self._clock[1]:
            return 2
        if self._clock[2][0]:
            return 1
        return 0

    def _ensure_footer_cache(self):
        """Refresh the bounded footer cache when the timer state changes."""
        state = self._footer_state()
        if self._footer[1][1] == state:
            return
        if state == 2:
            hint = _FOOTER_RUNNING_HINT
            right = _FOOTER_LAP_RIGHT
        elif state == 1:
            hint = _FOOTER_PAUSED_HINT
            right = _FOOTER_RESET_RIGHT
        else:
            hint = _FOOTER_IDLE_HINT
            right = _FOOTER_RESET_RIGHT
        hint = fit_text(hint, 126, self._clock[0])
        right = fit_text(right, 76, self._clock[0])
        self._footer[0][0] = hint
        self._footer[0][1] = hint.encode() if self._clock[0] else b""
        self._footer[0][2] = right
        self._footer[0][3] = right.encode() if self._clock[0] else b""
        self._footer[1][0] = max(
            130, self.width - text_width(right, self._clock[0]) - 2)
        self._footer[1][1] = state

    def _draw_footer(self, display):
        self._ensure_footer_cache()
        draw_footer_cached(
            display, self._footer[0][0], self._footer[0][1], self._clock[0],
            self._footer[0][2], self._footer[0][3],
            self._footer[1][0])

    def _ensure_lap_label_cache(self, total):
        """Build at most the currently visible four lap labels on change."""
        laps = self._clock[2][3]
        view_offset = self._clock[3][1]
        if (self._runtime[0][1] == self._clock[3][3]
                and self._runtime[0][2] == view_offset
                and self._runtime[0][3] is laps
                and self._runtime[1][0] == total):
            return
        labels = self._runtime[0][0]
        stop = min(view_offset + LAP_COUNT, total)
        index = view_offset
        row = 0
        while index < stop:
            number, elapsed = laps[index]
            labels[row] = "Lap" + str(number) + ":  " + self._fmt(elapsed)
            row += 1
            index += 1
        while row < LAP_COUNT:
            labels[row] = None
            row += 1
        self._runtime[0][1] = self._clock[3][3]
        self._runtime[0][2] = view_offset
        self._runtime[0][3] = laps
        self._runtime[1][0] = total

    def collect_present_damage(self, damage):
        """Keep a running stopwatch to the top timer band between actions."""
        self._clamp_lap_view()
        elapsed_cs = self._get_elapsed() // 10
        if self._render[1][0] is None:
            return DAMAGE_FULL
        if (self._clock[1] != self._render[1][0]
                or self._clock[3][3] != self._render[1][2]
                or self._clock[3][0] != self._render[1][3]
                or self._clock[3][1] != self._render[2][0]
                or self._footer_state() != self._render[2][1]):
            return DAMAGE_FULL
        if self._clock[1] and elapsed_cs != self._render[1][1]:
            damage.add(0, _TIMER_BAND_H)
            return DAMAGE_PARTIAL
        return DAMAGE_NONE

    def mark_presented(self):
        self._clamp_lap_view()
        self._render[1][0] = self._clock[1]
        self._render[1][1] = self._get_elapsed() // 10
        self._render[1][2] = self._clock[3][3]
        self._render[1][3] = self._clock[3][0]
        self._render[2][0] = self._clock[3][1]
        self._render[2][1] = self._footer_state()

    def draw_present_rows(self, display):
        display.fill_rectangle(0, 0, self.width, _TIMER_BAND_H, 0)
        self._draw_time(display, self._get_elapsed())
        display.draw_hline(0, 12, self.width, 15)

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
        self._draw_time(display, elapsed)

        # Divider
        display.draw_hline(0, 12, self.width, 15)

        # Lap list (y=14-51, 4 rows × 9px = 36px)
        lap_top = 14
        self._clamp_lap_view()
        total = len(self._clock[2][3])
        self._ensure_lap_label_cache(total)

        i = self._clock[3][1]
        end = min(i + LAP_COUNT, total)
        row = 0
        while i < end:
            y = lap_top + row * LAP_H
            label = self._runtime[0][0][row]
            is_selected = (i == self._clock[3][0])

            if is_selected:
                fh = self._clock[0].height if self._clock[0] else 8
                display.fill_rectangle(2, y, self.width - 4, fh, 14)
                if self._clock[0]:
                    draw_text(display, 4, y, label, self._clock[0], invert=True,
                              gs=14, raw=True)
                else:
                    display.draw_text8x8(4, y, label, gs=0)
            else:
                if self._clock[0]:
                    draw_text(display, 4, y, label, self._clock[0], gs=15,
                              raw=True)
                else:
                    display.draw_text8x8(4, y, label, gs=15)
            i += 1
            row += 1

        self._draw_footer(display)

    # ── input ───────────────────────────────────────────────────

    def update(self, kb, event=None):
        if event is None:
            return None

        r, c, shift = event
        label = get_key_label(r, c, shift)

        if label == "ESC":
            return "BACK"

        # Navigation: scroll lap list
        changed = False
        if label in ("up", "8"):
            if self._clock[2][3] and self._clock[3][0] > 0:
                self._clock[3][0] -= 1
                changed = True
        elif label in ("down", "2"):
            if self._clock[3][0] < len(self._clock[2][3]) - 1:
                self._clock[3][0] += 1
                changed = True
        elif label == "ENT":
            if self._clock[1]:
                changed = self._pause()
            else:
                changed = self._start()
        elif label == "DEL":
            if self._clock[1]:
                changed = self._lap()
            else:
                changed = self._reset()

        return "REDRAW" if changed else None
