"""Letter overlay panel: Shift+RPN opens A-Z input, default uppercase.
Shift key toggles case for lowercase letters (pi, e, variable names).
Semicolon supports multi-statement input."""
from ui.element import UIElement
from ui.theme import draw_footer


# Physical key → char (None = special). Case applied at input time.
_KEY_MAP = {
    (0,0): None,  (0,1): 'A', (0,2): 'B', (0,3): 'C', (0,4): 'D', (0,5): 'E',
    (1,0): 'F',   (1,1): 'G', (1,2): 'H', (1,3): 'I', (1,4): 'J', (1,5): 'K',
    (2,0): 'L',   (2,1): 'M', (2,2): 'N', (2,3): 'O', (2,4): 'P', (2,5): 'Q',
    (3,0): 'R',   (3,1): 'S', (3,2): 'T', (3,3): 'X', (3,4): 'Y', (3,5): 'Z',
    (4,0): None,  (4,1): None, (4,2): '"', (4,3): ';', (4,4): None,  (4,5): None,
}

_SPECIAL_LABELS = {
    (0,0): "ESC",
    (4,0): "Sh",   # case toggle
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
    transition_title = "Letters"

    def __init__(self, font, input_box):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.input_box = input_box
        self.text = ""
        self.upper = True   # default uppercase

    def activate(self):
        self.text = ""
        self.upper = True

    @staticmethod
    def _get_char(row, col):
        return _KEY_MAP.get((row, col))

    def draw(self, display):
        # Keep the editable tail visible inside the 210px content area.
        visible = self.text[-22:]
        prefix = "["
        display.draw_text8x8(2, 1, prefix, gs=15)
        cx = 2 + 8
        if visible:
            display.draw_text8x8(cx, 1, visible, gs=15)
            cx += len(visible) * 8
        display.draw_vline(cx, 1, 8, 15)
        display.draw_text8x8(cx + 2, 1, "]", gs=15)

        # Key legend — show current case
        for row_idx, keys in enumerate(_DISPLAY_ROWS):
            y = 12 + row_idx * 8
            x = 4
            for _ci, (r, c) in enumerate(keys):
                ch = self._get_char(r, c)
                if ch is not None:
                    label = ch.upper() if self.upper else ch.lower()
                else:
                    label = _SPECIAL_LABELS.get((r, c), "  ")
                display.draw_text8x8(x, y, label.center(3), gs=15)
                x += 32

        case_str = "ABC" if self.upper else "abc"
        draw_footer(display, "OK save  ESC cancel", self.font, case_str)

    def update(self, kb, event=None):
        if event is None:
            return None

        r, c, _ = event  # letter panel ignores shift — uses raw (r,c) for key mapping

        # ESC (0,0): cancel
        if r == 0 and c == 0:
            self.text = ""
            return "LETTER_DONE"

        # OK (4,5): confirm
        if r == 4 and c == 5:
            if self.text:
                self.input_box.insert_str(self.text)
            self.text = ""
            return "LETTER_DONE"

        # Bk (4,4): backspace
        if r == 4 and c == 4:
            if self.text:
                self.text = self.text[:-1]
            return None

        # Shift (4,0): toggle case
        if r == 4 and c == 0:
            self.upper = not self.upper
            return None

        # Character key
        ch = self._get_char(r, c)
        if ch is not None:
            remaining = self.input_box.max_char - len(self.input_box.get_str())
            if len(self.text) < remaining:
                self.text += ch.upper() if self.upper else ch.lower()

        return None
