# ponytail: direct GPIO scan, matches original firmware's keyboard behavior
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
CLICK_WINDOW = 100  # ms
DEBOUNCE_MS = 15    # ms — minimum release time before new press is recognized


class Key:
    def __init__(self, row, col):
        self.row = row
        self.col = col
        self.state = NOT_PRESSED
        self.is_pressed = False
        self.status_time = 0
        self.start_press = 0
        self.end_press = 0
        self.click_cnt = 0
        self._pending = 0  # timestamp of first high read for two-sample
        self._consumed = False  # edge consumed by pop_key_event()

    def update(self, cur_state, cur_time):
        if cur_state:
            if not self.is_pressed:
                since_release = time.ticks_diff(cur_time, self.end_press)
                # If released long ago (>50ms), accept single sample (fast response)
                if since_release > 50:
                    self._pending = 0
                    self.click_cnt += 1
                    self.is_pressed = True
                    self.state = RISING_EDGE
                    self._consumed = False
                    self.status_time = 0
                    self.start_press = cur_time
                elif since_release >= DEBOUNCE_MS:
                    # Recent release: require two consecutive high reads (bounce filter)
                    if self._pending == 0:
                        self._pending = cur_time
                    else:
                        self._pending = 0
                        self.click_cnt += 1
                        self.is_pressed = True
                        self.state = RISING_EDGE
                        self._consumed = False
                        self.status_time = 0
                        self.start_press = cur_time
                # else: within DEBOUNCE_MS of release, ignore
            else:
                self._pending = 0
                self.state = PRESSED
                self.status_time = cur_time - self.start_press
        else:
            self._pending = 0
            if self.is_pressed:
                self.is_pressed = False
                self.state = FALLING_EDGE
                self.status_time = 0
                self.end_press = cur_time
                self.start_press = 0
            else:
                self.state = NOT_PRESSED
                self.status_time = cur_time - self.end_press
                if self.status_time > CLICK_WINDOW:
                    self.click_cnt = 0

    def get_hold_time(self):
        """Return how long this key has been held in ms, or 0."""
        if self.is_pressed:
            return time.ticks_diff(time.ticks_ms(), self.start_press)
        return 0


class Keyboard:
    def __init__(self):
        self.keys = [[Key(r, c) for c in range(COLS)] for r in range(ROWS)]
        self._row_pins = [Pin(p, Pin.IN) for p in ROW_PINS]
        self._col_pins = [Pin(p, Pin.OUT) for p in COL_PINS]
        self._last_scan = 0
        for cp in self._col_pins:
            cp.value(0)

    def scan(self):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_scan) < SCAN_INTERVAL:
            return
        self._last_scan = now
        for ci, cp in enumerate(self._col_pins):
            cp.value(1)
            time.sleep_us(10)
            for ri in range(ROWS):
                raw = self._row_pins[ri].value()
                self.keys[ri][ci].update(raw, now)
            cp.value(0)

    def get_rising_edge(self):
        """Peek at first rising edge key. Does NOT consume - matches original getRisingEdgeKey()."""
        for ri in range(ROWS):
            for ci in range(COLS):
                if self.keys[ri][ci].state == RISING_EDGE:
                    return (ri, ci)
        return None

    def is_pressed(self, row, col):
        return self.keys[row][col].is_pressed

    def get_hold_time(self, row, col):
        return self.keys[row][col].get_hold_time()


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
