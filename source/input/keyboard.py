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

NOT_PRESSED = 0
RISING_EDGE = 1
PRESSED = 2
FALLING_EDGE = 3

SCAN_INTERVAL = 15  # ms
DEBOUNCE_MS = 15    # ms — minimum release time before new press is recognized
EVENT_QUEUE_CAPACITY = 8
SHIFT_EVENT_FLAG = 0x20


def _decode_event(code):
    position = code & (SHIFT_EVENT_FLAG - 1)
    return (position // COLS, position % COLS,
            bool(code & SHIFT_EVENT_FLAG))


class Key:
    __slots__ = (
        "row", "col", "state", "is_pressed", "start_press", "end_press",
        "_pending", "_consumed", "_hold_consumed")

    def __init__(self, row, col):
        self.row = row
        self.col = col
        self.state = NOT_PRESSED
        self.is_pressed = False
        self.start_press = 0
        self.end_press = None
        self._pending = None  # timestamp of first high read for two-sample
        self._consumed = False  # edge consumed by pop_key_event()
        self._hold_consumed = False

    def _accept_press(self, cur_time):
        self._pending = None
        self.is_pressed = True
        self.state = RISING_EDGE
        self._consumed = False
        self.start_press = cur_time
        self._hold_consumed = False

    def update(self, cur_state, cur_time):
        if cur_state:
            if not self.is_pressed:
                since_release = (None if self.end_press is None else
                                 time.ticks_diff(cur_time, self.end_press))
                # If released long ago (>50ms), accept single sample (fast response)
                if since_release is None or since_release > 50:
                    self._accept_press(cur_time)
                    return True
                elif since_release >= DEBOUNCE_MS:
                    # Recent release: require two consecutive high reads (bounce filter)
                    if self._pending is None:
                        self._pending = cur_time
                    else:
                        self._accept_press(cur_time)
                        return True
                # else: within DEBOUNCE_MS of release, ignore
            else:
                self._pending = None
                self.state = PRESSED
        else:
            self._pending = None
            if self.is_pressed:
                self.is_pressed = False
                self.state = FALLING_EDGE
                self.end_press = cur_time
                self.start_press = 0
                self._hold_consumed = False
            else:
                self.state = NOT_PRESSED
        return False

    def get_hold_time(self):
        """Return how long this key has been held in ms, or 0."""
        if self.is_pressed:
            return time.ticks_diff(time.ticks_ms(), self.start_press)
        return 0

    def consume_long_press(self, now, threshold_ms):
        if (self.is_pressed and not self._hold_consumed
                and time.ticks_diff(now, self.start_press) >= threshold_ms):
            self._hold_consumed = True
            return True
        return False


class Keyboard:
    __slots__ = (
        "keys", "_row_pins", "_col_pins", "_last_scan",
        "_event_data", "_event_head", "_event_count")

    def __init__(self):
        self.keys = [[Key(r, c) for c in range(COLS)] for r in range(ROWS)]
        self._row_pins = [Pin(p, Pin.IN) for p in ROW_PINS]
        self._col_pins = [Pin(p, Pin.OUT) for p in COL_PINS]
        self._last_scan = None
        self._event_data = bytearray(EVENT_QUEUE_CAPACITY)
        self._event_head = 0
        self._event_count = 0
        for cp in self._col_pins:
            cp.value(0)

    def scan(self):
        now = time.ticks_ms()
        if (self._last_scan is not None
                and time.ticks_diff(now, self._last_scan) < SCAN_INTERVAL):
            return
        self._last_scan = now
        for ci, cp in enumerate(self._col_pins):
            cp.value(1)
            time.sleep_us(10)
            for ri in range(ROWS):
                raw = self._row_pins[ri].value()
                self.keys[ri][ci].update(raw, now)
            cp.value(0)
        self._capture_edges()

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

    def _capture_edges(self):
        shift_held = self.keys[4][0].is_pressed
        for row in self.keys:
            for key in row:
                if key.state == RISING_EDGE and not key._consumed:
                    if not self._queue_event(
                            key.row, key.col, shift_held):
                        return
                    key._consumed = True

    def pop_key_event(self):
        """Return (row, col, shift_held) for first unconsumed rising edge and
        consume it. Shift state is captured atomically with the edge — no more
        label misresolution. Returns None if all edges already consumed."""
        if self._event_count == 0:
            # Also supports diagnostic/test callers that update Key objects
            # directly rather than going through scan().
            self._capture_edges()
        if self._event_count == 0:
            return None
        code = self._event_data[self._event_head]
        self._event_head = (self._event_head + 1) % EVENT_QUEUE_CAPACITY
        self._event_count -= 1
        return _decode_event(code)

    def pop_key_event_at(self, row, col):
        """Remove one matching edge while preserving all other queued taps."""
        if self._event_count == 0:
            self._capture_edges()
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
        return self.keys[row][col].is_pressed

    def get_hold_time(self, row, col):
        return self.keys[row][col].get_hold_time()

    def consume_long_press(self, row, col, threshold_ms):
        return self.keys[row][col].consume_long_press(time.ticks_ms(), threshold_ms)

    def discard_pending_events(self):
        self._event_head = 0
        self._event_count = 0
        for row in self.keys:
            for key in row:
                if key.state == RISING_EDGE:
                    key._consumed = True

    def any_pressed(self):
        for row in self.keys:
            for key in row:
                if key.is_pressed:
                    return True
        return False


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
