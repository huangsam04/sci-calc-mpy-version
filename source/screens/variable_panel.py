import time

from ui.element import UIElement
from input.keyboard import get_key_label
from ui import inputbox as _inputbox
from ui import theme as _theme


VARIABLE_CURSOR_MS = 96


class VariablePanel(UIElement):
    transition_title = "Variables"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("_state",)

    def __init__(self, font, calc_screen):
        # calculator, names, cursor, offset, notice, scenario lease, motion
        # start, packed cursor coordinates, fixed visible labels, footer right.
        labels = [None] * 12
        labels[9] = -1
        labels[10] = -1
        labels[11] = -1
        self._state = [
            calc_screen, (), 0, 0, "", None, -1, 0, labels, ""]

    @staticmethod
    def _position(index, page):
        relative = max(0, index - page)
        x = 4 if relative < 4 else 108
        return x | ((15 + (relative % 4) * 10) << 8)

    @staticmethod
    def _pack_cursor(start_x, start_y, current_x, current_y):
        return ((start_x & 255) | ((start_y & 63) << 8)
                | ((current_x & 255) << 14)
                | ((current_y & 63) << 22))

    def _snap_motion(self):
        state = self._state
        position = self._position(state[2], state[3])
        x = position & 255
        y = position >> 8
        state[6] = -1
        state[7] = self._pack_cursor(x, y, x, y)

    @property
    def motion_active(self):
        return self._state[6] >= 0

    def advance_motion(self, now):
        state = self._state
        started = state[6]
        if started < 0:
            return False
        elapsed = time.ticks_diff(now, started)
        if elapsed < 0:
            elapsed = 0
        packed = state[7]
        start_x = packed & 255
        start_y = (packed >> 8) & 63
        old_x = (packed >> 14) & 255
        old_y = (packed >> 22) & 63
        position = self._position(state[2], state[3])
        target_x = position & 255
        target_y = position >> 8
        if elapsed >= VARIABLE_CURSOR_MS:
            self._snap_motion()
            return True
        remaining = VARIABLE_CURSOR_MS - elapsed
        denominator = VARIABLE_CURSOR_MS * VARIABLE_CURSOR_MS
        distance = target_x - start_x
        residual = abs(distance) * remaining * remaining // denominator
        current_x = (target_x - residual if distance >= 0
                     else target_x + residual)
        distance = target_y - start_y
        residual = abs(distance) * remaining * remaining // denominator
        current_y = (target_y - residual if distance >= 0
                     else target_y + residual)
        state[7] = self._pack_cursor(
            start_x, start_y, current_x, current_y)
        return current_x != old_x or current_y != old_y

    def _start_motion(self, old_x, old_y):
        state = self._state
        position = self._position(state[2], state[3])
        target_x = position & 255
        target_y = position >> 8
        if old_x != target_x:
            old_x += 2 if target_x > old_x else -2
        if old_y != target_y:
            old_y += 2 if target_y > old_y else -2
        state[6] = time.ticks_ms()
        state[7] = self._pack_cursor(old_x, old_y, old_x, old_y)

    def _cache_page(self, page):
        state = self._state
        calc = state[0]
        names = state[1]
        count = len(names)
        labels = state[8]
        relative = 0
        while relative < 8:
            index = page + relative
            if index < count:
                name = names[index]
                label = name + "=" + calc._fmt(calc.vars[name])
                labels[relative] = (
                    label if len(label) <= 12 else label[:12])
            else:
                labels[relative] = None
            relative += 1
        labels[8] = names
        labels[9] = page

    def _cache_footer(self):
        state = self._state
        count = len(state[1])
        state[9] = (str(state[2] + 1) + "/" + str(count)
                    if count else "")
        labels = state[8]
        labels[10] = state[2]
        labels[11] = count

    def _ensure_render_cache(self):
        state = self._state
        labels = state[8]
        if labels[8] is not state[1] or labels[9] != state[3]:
            self._cache_page(state[3])
        count = len(state[1])
        if labels[10] != state[2] or labels[11] != count:
            self._cache_footer()

    def activate(self):
        state = self._state
        if state[5] is not None:
            raise RuntimeError("Variable panel scenario transaction is active")
        self._rebuild()
        state[2] = 0
        state[3] = 0
        state[4] = ""
        self._cache_page(0)
        self._cache_footer()
        self._snap_motion()

    def release_memory(self):
        state = self._state
        labels = state[8]
        released = bool(
            state[1] or state[4] or state[9] or labels[8] is not None)
        state[1] = ()
        state[2] = 0
        state[3] = 0
        state[4] = ""
        self._snap_motion()
        index = 0
        while index < 12:
            labels[index] = None
            index += 1
        labels[9] = -1
        labels[10] = -1
        labels[11] = -1
        state[9] = ""
        return released

    def open_scenario_transaction(self):
        if self._state[5] is not None:
            raise RuntimeError(
                "Variable panel scenario transaction is already active")
        from screens.variable_panel_scenario import (
            VariablePanelScenarioTransaction)
        return VariablePanelScenarioTransaction(self)

    def _rebuild(self):
        state = self._state
        variables = state[0].vars
        if not isinstance(variables, dict):
            raise RuntimeError("Variable panel variables are unavailable")
        state[1] = sorted(variables)

    def _clamp(self):
        state = self._state
        count = len(state[1])
        if not count:
            state[2] = 0
            state[3] = 0
            return
        state[2] = max(0, min(state[2], count - 1))
        if not state[3] <= state[2] < state[3] + 8:
            state[3] = state[2] // 8 * 8
        state[3] = min(state[3], (count - 1) // 8 * 8)

    def _draw_item(self, display, x, y, label, selected):
        if selected:
            display.fill_rectangle(x, y, 90, 8, 14)
        display.draw_text8x8(
            x + 2, y, label, gs=0 if selected else 15)

    def draw(self, display):
        state = self._state
        names = state[1]
        labels = state[8]
        _theme.draw_header_fast(display, "Variables", b"Variables", None)
        display.draw_rectangle(0, 13, self.width, 40, 15)
        self._clamp()
        self._ensure_render_cache()
        if state[6] < 0:
            self._snap_motion()
        count = len(names)
        if not count:
            _theme.draw_empty(display, "No variables defined", None)
        else:
            packed = state[7]
            cursor_x = (packed >> 14) & 255
            cursor_y = (packed >> 22) & 63
            display.fill_rectangle(cursor_x, cursor_y, 90, 8, 14)
            row = 0
            while row < 4:
                left = state[3] + row
                right = left + 4
                y = 15 + row * 10
                if left < count:
                    self._draw_item(
                        display, 4, y, labels[row],
                        abs(cursor_x - 4) <= 2
                        and abs(cursor_y - y) <= 2)
                if right < count:
                    self._draw_item(
                        display, 108, y, labels[4 + row],
                        abs(cursor_x - 108) <= 2
                        and abs(cursor_y - y) <= 2)
                row += 1
        if not count:
            hint, hint_bytes, right = "No variables", b"No variables", ""
        else:
            hint = (_inputbox.INPUT_FULL_NOTICE if state[4]
                    else "ENT ins DEL rm")
            hint_bytes = (_inputbox.INPUT_FULL_NOTICE_BYTES if state[4]
                          else b"ENT ins DEL rm")
            right = state[9]
        _theme.draw_footer_fast(display, hint, hint_bytes, None, right)

    def update(self, kb, event=None):
        state = self._state
        if state[5] is not None or event is None:
            return None
        row, col, shift = event
        label = get_key_label(row, col, shift)
        names = state[1]
        calc = state[0]
        count = len(names)
        previous = state[2]
        previous_page = state[3]
        if state[6] >= 0:
            self._snap_motion()
        old_position = self._position(previous, previous_page)
        old_x = old_position & 255
        old_y = old_position >> 8
        changed = False
        if label in ("2", "down") and state[2] < count - 1:
            state[2] += 1
        elif label in ("8", "up") and state[2] > 0:
            state[2] -= 1
        elif label == "ENT":
            if not names:
                return "VAR_PANEL_DONE"
            if calc.input_box.try_insert(names[state[2]]):
                state[4] = ""
                return "VAR_PANEL_DONE"
            if state[4] == _inputbox.INPUT_FULL_NOTICE:
                return None
            state[4] = _inputbox.INPUT_FULL_NOTICE
            return "REDRAW"
        elif label == "DEL" and names:
            name = names[state[2]]
            if name in calc.vars:
                calc.context.delete_var(name)
                changed = True
            self._rebuild()
            state[2] = min(state[2], max(0, len(state[1]) - 1))
            self._clamp()
            self._cache_page(state[3])
            self._cache_footer()
        elif label == "ESC":
            return "VAR_PANEL_DONE"
        elif row == 2 and col == 0 and state[2] >= 4:
            state[2] -= 4
        elif row == 2 and col == 2 and state[2] + 4 < count:
            state[2] += 4
        if state[2] != previous:
            self._clamp()
            self._cache_footer()
            if state[3] == previous_page:
                self._start_motion(old_x, old_y)
            else:
                self._cache_page(state[3])
                self._snap_motion()
        return "REDRAW" if changed or state[2] != previous else None
