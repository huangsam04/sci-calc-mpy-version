"""Keyboard matrix scanner for 5x6 key matrix.

Row pins (INPUT):  33, 32, 35, 34, 39
Column pins (OUTPUT): 13, 12, 14, 27, 26, 25
"""
import time
from machine import Pin

ROW_PINS = [33, 32, 35, 34, 39]
COL_PINS = [13, 12, 14, 27, 26, 25]
ROWS = 5
COLS = 6

SCAN_INTERVAL = 8   # ms
DEBOUNCE_MS = 8     # ms — minimum release time before new press is recognized
EVENT_QUEUE_CAPACITY = 8
SHIFT_EVENT_FLAG = 0x20
SHIFT_INDEX = 4 * COLS


def _decode_event(code):
    position = code & (SHIFT_EVENT_FLAG - 1)
    return (position // COLS, position % COLS,
            bool(code & SHIFT_EVENT_FLAG))


class Keyboard:
    """Allocation-free matrix state using bitmaps instead of 30 key objects."""

    __slots__ = (
        "_row_pins", "_col_pins", "_last_scan", "_pressed_mask",
        "_released_mask", "_hold_consumed_mask", "_release_times",
        "_press_starts", "_event_data", "_event_head", "_event_count")

    def __init__(self):
        self._row_pins = [Pin(p, Pin.IN) for p in ROW_PINS]
        self._col_pins = [Pin(p, Pin.OUT) for p in COL_PINS]
        self._last_scan = None
        self._pressed_mask = 0
        self._released_mask = 0
        self._hold_consumed_mask = 0
        self._release_times = [0] * (ROWS * COLS)
        self._press_starts = [0] * (ROWS * COLS)
        self._event_data = bytearray(EVENT_QUEUE_CAPACITY)
        self._event_head = 0
        self._event_count = 0
        for cp in self._col_pins:
            cp.value(0)

    def _update_key(self, index, raw, now):
        """Update one matrix position and return whether it rose."""
        bit = 1 << index
        pressed = bool(self._pressed_mask & bit)
        if raw:
            if pressed:
                return False
            if (self._released_mask & bit
                    and time.ticks_diff(
                        now, self._release_times[index]) < DEBOUNCE_MS):
                return False
            self._pressed_mask |= bit
            self._hold_consumed_mask &= ~bit
            self._press_starts[index] = now
            return True
        if pressed:
            self._pressed_mask &= ~bit
            self._released_mask |= bit
            self._hold_consumed_mask &= ~bit
            self._release_times[index] = now
            self._press_starts[index] = 0
        return False

    def scan(self):
        now = time.ticks_ms()
        if (self._last_scan is not None
                and time.ticks_diff(now, self._last_scan) < SCAN_INTERVAL):
            return
        self._last_scan = now
        rising_mask = 0
        for ci, cp in enumerate(self._col_pins):
            cp.value(1)
            time.sleep_us(10)
            for ri in range(ROWS):
                raw = self._row_pins[ri].value()
                index = ri * COLS + ci
                if self._update_key(index, raw, now):
                    rising_mask |= 1 << index
            cp.value(0)
        self._capture_rising(rising_mask)

    def _queue_event(self, row, col, shift_held):
        """Keep a bounded edge backlog across slow display frames."""
        if self._event_count >= EVENT_QUEUE_CAPACITY:
            return False
        tail = (self._event_head + self._event_count) % EVENT_QUEUE_CAPACITY
        code = row * COLS + col
        if shift_held:
            code |= SHIFT_EVENT_FLAG
        self._event_data[tail] = code
        self._event_count += 1
        return True

    def _capture_rising(self, rising_mask):
        shift_held = bool(self._pressed_mask & (1 << SHIFT_INDEX))
        for index in range(ROWS * COLS):
            if rising_mask & (1 << index):
                if not self._queue_event(
                        index // COLS, index % COLS, shift_held):
                    return

    def pop_key_event(self):
        """Return (row, col, shift_held) for first unconsumed rising edge and
        consume it. Shift state is captured atomically with the edge — no more
        label misresolution. Returns None if all edges already consumed."""
        if self._event_count == 0:
            return None
        code = self._event_data[self._event_head]
        self._event_head = (self._event_head + 1) % EVENT_QUEUE_CAPACITY
        self._event_count -= 1
        return _decode_event(code)

    def pop_key_event_at(self, row, col):
        """Remove one matching edge while preserving all other queued taps."""
        wanted = row * COLS + col
        for offset in range(self._event_count):
            index = (self._event_head + offset) % EVENT_QUEUE_CAPACITY
            code = self._event_data[index]
            if code & (SHIFT_EVENT_FLAG - 1) != wanted:
                continue
            while offset < self._event_count - 1:
                next_index = (index + 1) % EVENT_QUEUE_CAPACITY
                self._event_data[index] = self._event_data[next_index]
                index = next_index
                offset += 1
            self._event_count -= 1
            return _decode_event(code)
        return None

    def is_pressed(self, row, col):
        return bool(self._pressed_mask & (1 << (row * COLS + col)))

    def get_hold_time(self, row, col):
        index = row * COLS + col
        if not self._pressed_mask & (1 << index):
            return 0
        return time.ticks_diff(time.ticks_ms(), self._press_starts[index])

    def consume_long_press(self, row, col, threshold_ms):
        index = row * COLS + col
        bit = 1 << index
        if (not self._pressed_mask & bit
                or self._hold_consumed_mask & bit
                or time.ticks_diff(
                    time.ticks_ms(),
                    self._press_starts[index]) < threshold_ms):
            return False
        self._hold_consumed_mask |= bit
        return True

    def discard_pending_events(self):
        self._event_head = 0
        self._event_count = 0

    def has_pending_events(self):
        return self._event_count != 0

    def any_pressed(self):
        return self._pressed_mask != 0


# --- Key label lookup (matches original calcLayout) ---

_CALC_MAP = {
    (0,0): "ESC",  (0,1): "/",   (0,2): "*",   (0,3): "-",   (0,4): "sin",  (0,5): "sec",
    (1,0): "7",    (1,1): "8",   (1,2): "9",   (1,3): "+",   (1,4): "cos",  (1,5): "csc",
    (2,0): "4",    (2,1): "5",   (2,2): "6",   (2,3): "^",   (2,4): "tan",  (2,5): "cot",
    (3,0): "1",    (3,1): "2",   (3,2): "3",   (3,3): "ENT", (3,4): "exp",  (3,5): "rpn",
    (4,0): "shift",(4,1): "0",   (4,2): ".",   (4,3): "DEL", (4,4): "ang",  (4,5): "tab",
}

_CALC_SHIFT_MAP = {
    (0,0): "ESC",  (0,1): "(",   (0,2): ")",   (0,3): "-",   (0,4): "asin", (0,5): "sec",
    (1,0): "7",    (1,1): "up",  (1,2): "9",   (1,3): "+",   (1,4): "acos", (1,5): "csc",
    (2,0): "left", (2,1): "5",   (2,2): "right",(2,3): "sqrt",(2,4): "atan",(2,5): "cot",
    (3,0): "1",    (3,1): "down",(3,2): "3",   (3,3): "ENT", (3,4): "ln",   (3,5): "rpn",
    (4,0): "shift",(4,1): "0",   (4,2): ",",   (4,3): "DEL", (4,4): "ang",  (4,5): "stab",
}


def get_key_label(row, col, shift_held=False):
    """Map a physical key position to its label string.
    Matches the original calcLayout Macropad behavior."""
    if shift_held and (row, col) in _CALC_SHIFT_MAP:
        label = _CALC_SHIFT_MAP[(row, col)]
        if label is not None:
            return label
    return _CALC_MAP.get((row, col), "")
