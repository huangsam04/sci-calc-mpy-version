"""Function picker: Shift+RPN — 2-column scrollable list of all functions."""
import time
from ui.element import UIElement
from input.keyboard import get_key_label
from ui.theme import draw_footer, draw_header
from ui.residency import SETTLE_REDRAW

VISIBLE = 4      # rows visible at once
ROW_H = 10       # pixel height per row
COL_W = 100      # pixel width per column
COL2_X = 105     # x start of right column


class FunctionPicker(UIElement):
    swap_key = "function_picker"
    transition_title = "Functions"

    def __init__(self, font, calc_screen):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.calc_screen = calc_screen
        self._names = []
        self._cursor = 0     # flat index into _names
        self._offset = 0     # first visible row's base index (multiple of VISIBLE)
        self._cooldown = 0
        self._last_key = None

    def activate(self):
        ft = self.calc_screen.context.registry
        self._names = sorted(ft.keys())
        self._cursor = 0
        self._offset = 0
        self._cooldown = 0
        self._last_key = None

    def release_memory(self):
        """Function names are rebuilt from the live registry on activation."""
        if not self._names:
            return False
        self._names = []
        return True

    def snapshot_state(self):
        return {"cursor": self._cursor, "offset": self._offset}

    def reset_state(self):
        self._names = []
        self._cursor = 0
        self._offset = 0
        self._cooldown = 0
        self._last_key = None
        self._needs_names_restore = False

    def activate_default(self):
        self._names = []
        self._cooldown = 0
        self._last_key = None
        self._needs_names_restore = True

    def restore_state(self, state):
        self._cursor = max(0, int(state.get("cursor", 0)))
        self._offset = max(0, int(state.get("offset", 0)))
        self._needs_names_restore = True

    def settle_step(self):
        if not getattr(self, "_needs_names_restore", False):
            return 0
        self._needs_names_restore = False
        self._names = sorted(self.calc_screen.context.registry.keys())
        self._clamp()
        return SETTLE_REDRAW

    def draw_transition_default(self, display):
        display.draw_text8x8(4, 2, "Functions", gs=15)
        display.draw_hline(2, 11, self.width - 4, 8)
        display.draw_rectangle(1, 13, self.width - 2, 40, 8)
        display.draw_text8x8(4, self.height - 9, "Loading list...", gs=8)

    def _clamp(self):
        n = len(self._names)
        if n == 0:
            return
        self._cursor = max(0, min(self._cursor, n - 1))
        PAGE = VISIBLE * 2  # 8 items = one full 2-col page
        # If cursor left the current page, flip to cursor's page
        if self._cursor < self._offset or self._cursor >= self._offset + PAGE:
            self._offset = (self._cursor // PAGE) * PAGE
        max_off = max(0, ((n - 1) // PAGE) * PAGE)
        self._offset = max(0, min(self._offset, max_off))

    def _insert_selected(self):
        if not self._names:
            return
        name = self._names[self._cursor]
        ft = self.calc_screen.context.registry
        entry = ft.get(name)
        if entry:
            kind = entry[2]
            if kind in ("prefix", "list"):
                self.calc_screen.input_box.insert_str(name + "(")
            else:
                self.calc_screen.input_box.insert_str(name)
        else:
            self.calc_screen.input_box.insert_str(name)

    def _draw_item(self, display, x, y, name, selected):
        font_h = self.font.height if self.font else 8
        label = name
        if self.font and self.font.measure_text(label) > COL_W - 12:
            while len(label) > 0 and self.font.measure_text(label + "~") > COL_W - 12:
                label = label[:-1]
            label += "~"
        if selected:
            display.fill_rectangle(x, y, COL_W - 4, font_h, 12)
            if self.font:
                display.draw_text(x + 2, y, label, self.font, invert=True, gs=14)
            else:
                display.draw_text8x8(x + 2, y, label[:12], gs=0)
        else:
            if self.font:
                display.draw_text(x + 2, y, label, self.font, gs=15)
            else:
                display.draw_text8x8(x + 2, y, label[:12], gs=15)

    def draw(self, display):
        draw_header(display, "Functions", self.font)

        self._clamp()
        n = len(self._names)

        for row in range(VISIBLE):
            y = 12 + row * ROW_H
            # Left column
            li = self._offset + row
            if li < n:
                self._draw_item(display, 4, y, self._names[li], li == self._cursor)
            # Right column
            ri = self._offset + VISIBLE + row
            if ri < n:
                self._draw_item(display, COL2_X, y, self._names[ri], ri == self._cursor)

        # Status
        total = len(self._names)
        hint = "UP/DN move  4/6 column"
        right = f"{self._cursor+1}/{total} ENT" if total else ""
        if total == 0:
            hint = "[No functions loaded]"
        draw_footer(display, hint, self.font, right)

    def update(self, kb, event=None):
        if event is None:
            return None

        r, c, shift = event
        now = time.ticks_ms()

        # Per-key cooldown: same-key rapid-fire prevention, different keys pass through
        if (r, c) == self._last_key and time.ticks_diff(now, self._cooldown) < 150:
            return None
        self._cooldown = now
        self._last_key = (r, c)
        label = get_key_label(r, c, shift)
        n = len(self._names)

        if label in ("2", "down"):
            if self._cursor < n - 1:
                self._cursor += 1
        elif label in ("8", "up"):
            if self._cursor > 0:
                self._cursor -= 1
        # Left/Right: physical 4/6 keys jump between columns
        elif r == 2 and c == 0:
            nc = self._cursor - VISIBLE
            if nc >= 0:
                self._cursor = nc
        elif r == 2 and c == 2:
            nc = self._cursor + VISIBLE
            if nc < n:
                self._cursor = nc
        elif label == "ENT":
            self._insert_selected()
            return "FUNC_PICKER_DONE"
        elif label == "ESC":
            return "FUNC_PICKER_DONE"

        return None
