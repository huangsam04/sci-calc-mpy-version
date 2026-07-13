"""Letter overlay panel: RPN key opens A-Z input (uppercase = variables)."""
import time
from ui.element import UIElement


# Physical key → uppercase letter (None = special)
# Layout: ESC at (0,0), A-Z in order left→right top→bottom, BKSP+OK at end
_KEY_MAP = {
    (0,0): None,  (0,1): 'A', (0,2): 'B', (0,3): 'C', (0,4): 'D', (0,5): 'E',
    (1,0): 'F',   (1,1): 'G', (1,2): 'H', (1,3): 'I', (1,4): 'J', (1,5): 'K',
    (2,0): 'L',   (2,1): 'M', (2,2): 'N', (2,3): 'O', (2,4): 'P', (2,5): 'Q',
    (3,0): 'R',   (3,1): 'S', (3,2): 'T', (3,3): 'U', (3,4): 'V', (3,5): 'W',
    (4,0): 'X',   (4,1): 'Y', (4,2): 'Z', (4,3): ';',  (4,4): None,  (4,5): None,
}

_SPECIAL_LABELS = {
    (0,0): "ESC",
    (4,4): "Bk",
    (4,5): "OK",
}

_DISPLAY_ROWS = [
    [(0,0),(0,1),(0,2),(0,3),(0,4),(0,5)],
    [(1,0),(1,1),(1,2),(1,3),(1,4),(1,5)],
    [(2,0),(2,1),(2,2),(2,3),(2,4),(2,5)],
    [(3,0),(3,1),(3,2),(3,3),(3,4),(3,5)],
    [(4,0),(4,1),(4,2),(4,3),(4,4),(4,5)],
]


class LetterPanel(UIElement):
    def __init__(self, font, input_box):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.input_box = input_box
        self.text = ""
        self._last_action = 0

    def activate(self):
        self.text = ""
        self._last_action = time.ticks_ms()  # block spurious RPN-key edge at open

    @staticmethod
    def _get_char(row, col):
        return _KEY_MAP.get((row, col))

    def draw(self, display):
        # Accumulated text: [HELLO]|
        prefix = "["
        display.draw_text8x8(2, 1, prefix, gs=15)
        cx = 2 + 8
        if self.text:
            display.draw_text8x8(cx, 1, self.text, gs=15)
            cx += len(self.text) * 8
        display.draw_vline(cx, 1, 8, 15)
        display.draw_text8x8(cx + 2, 1, "]", gs=15)

        # Key legend
        for row_idx, keys in enumerate(_DISPLAY_ROWS):
            y = 13 + row_idx * 9
            x = 4
            for _ci, (r, c) in enumerate(keys):
                ch = self._get_char(r, c)
                if ch is not None:
                    label = ch
                else:
                    label = _SPECIAL_LABELS.get((r, c), "  ")
                display.draw_text8x8(x, y, label.center(3), gs=15)
                x += 32

        display.draw_text8x8(2, 55, "[OK:done] [ESC:cancel] [Bk:del]", gs=15)

    def update(self, kb):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_action) < 150:
            return None

        key = kb.get_rising_edge()
        if key is None:
            return None

        r, c = key

        # ESC (0,0): cancel, close without inserting
        if r == 0 and c == 0:
            return "LETTER_DONE"

        # OK (4,5): confirm, insert text, close
        if r == 4 and c == 5:
            if self.text:
                self.input_box.insert_str(self.text)
            return "LETTER_DONE"

        # BKSP (4,4): backspace
        if r == 4 and c == 4:
            if self.text:
                self.text = self.text[:-1]
            self._last_action = now
            return None

        # Letter key
        ch = self._get_char(r, c)
        if ch is not None:
            self.text += ch
            self._last_action = now

        return None
