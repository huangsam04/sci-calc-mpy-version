"""Variable panel: Shift+Tab — 2-column table of defined variables."""
import time
from ui.element import UIElement
from input.keyboard import get_key_label
from ui.theme import (SHELL_VARIABLE_PANEL, draw_empty, draw_footer,
                      draw_header, draw_page_shell)
from ui.residency import SETTLE_MORE, SETTLE_REDRAW

VISIBLE = 4
ROW_H = 10
COL_W = 90
COL2_X = 108


class VariablePanel(UIElement):
    swap_key = "variable_panel"
    transition_title = "Variables"

    def __init__(self, font, calc_screen):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.calc = calc_screen
        self._names = []
        self._cursor = 0
        self._offset = 0
        self._cooldown = 0
        self._last_key = None
        self._needs_names_restore = None

    def activate(self):
        self._rebuild()
        self._cursor = 0
        self._offset = 0
        self._cooldown = 0
        self._last_key = None
        self._needs_names_restore = None

    def release_memory(self):
        """Variable names are a disposable sorted view of calculator state."""
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
        self._needs_names_restore = None

    def activate_default(self):
        self._names = []
        self._cooldown = 0
        self._last_key = None
        self._needs_names_restore = -1

    def restore_state(self, state):
        self._cursor = max(0, int(state.get("cursor", 0)))
        self._offset = max(0, int(state.get("offset", 0)))
        self._needs_names_restore = -1

    def settle_step(self):
        stage = self._needs_names_restore
        if stage is None:
            return 0
        if stage < 0:
            self._rebuild()
            self._clamp()
            self._needs_names_restore = 0
            if not self._names:
                self._needs_names_restore = None
                return SETTLE_REDRAW
            return SETTLE_MORE
        stage += 1
        if stage >= VISIBLE:
            self._needs_names_restore = None
            return SETTLE_REDRAW
        self._needs_names_restore = stage
        return SETTLE_REDRAW | SETTLE_MORE

    def draw_transition_default(self, display):
        draw_page_shell(display, SHELL_VARIABLE_PANEL, self.font)

    def _rebuild(self):
        self._names = sorted(self.calc.vars.keys())

    def _clamp(self):
        n = len(self._names)
        if n == 0:
            return
        self._cursor = max(0, min(self._cursor, n - 1))
        PAGE = VISIBLE * 2
        if self._cursor < self._offset or self._cursor >= self._offset + PAGE:
            self._offset = (self._cursor // PAGE) * PAGE
        max_off = max(0, ((n - 1) // PAGE) * PAGE)
        self._offset = max(0, min(self._offset, max_off))

    def _draw_item(self, display, x, y, name, value_str, selected):
        font_h = self.font.height if self.font else 8
        label = f"{name}={value_str}"
        if self.font and self.font.measure_text(label) > COL_W - 12:
            while len(label) > 0 and self.font.measure_text(label + "~") > COL_W - 12:
                label = label[:-1]
            label += "~"
        if selected:
            display.fill_rectangle(x, y, COL_W, font_h, 14)
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
        draw_header(display, "Variables", self.font)
        display.draw_rectangle(0, 13, self.width, 40, 15)

        self._clamp()
        n = len(self._names)

        if n == 0:
            draw_empty(display, "No variables defined", self.font)
        else:
            visible_rows = (VISIBLE if self._needs_names_restore is None
                            else max(0, self._needs_names_restore))
            for row in range(visible_rows):
                y = 15 + row * ROW_H
                li = self._offset + row
                if li < n:
                    name = self._names[li]
                    val = self._fmt(self.calc.vars[name])
                    self._draw_item(display, 4, y, name, val, li == self._cursor)
                ri = self._offset + VISIBLE + row
                if ri < n:
                    name = self._names[ri]
                    val = self._fmt(self.calc.vars[name])
                    self._draw_item(display, COL2_X, y, name, val, ri == self._cursor)

        total = len(self._names)
        hint = "ENT insert  DEL remove" if total else "No variables"
        right = f"{self._cursor+1}/{total}" if total else ""
        draw_footer(display, hint, self.font, right)

    def _fmt(self, val):
        return self.calc._fmt(val)

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
        elif label == "ENT":
            if self._names:
                self.calc.input_box.insert_str(self._names[self._cursor])
            return "VAR_PANEL_DONE"
        elif label == "DEL":
            if self._names and 0 <= self._cursor < n:
                name = self._names[self._cursor]
                if name in self.calc.vars:
                    self.calc.context.delete_var(name)
                self._rebuild()
                if self._cursor >= len(self._names):
                    self._cursor = max(0, len(self._names) - 1)
        elif label == "ESC":
            return "VAR_PANEL_DONE"
        # Left/Right: physical 4/6 keys
        elif r == 2 and c == 0:
            nc = self._cursor - VISIBLE
            if nc >= 0:
                self._cursor = nc
        elif r == 2 and c == 2:
            nc = self._cursor + VISIBLE
            if nc < n:
                self._cursor = nc

        return None
