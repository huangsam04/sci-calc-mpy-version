from ui.element import UIElement
from input.keyboard import get_key_label
from ui import inputbox as _inputbox
from ui import theme as _theme


class VariablePanel(UIElement):
    transition_title = "Variables"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("_state",)

    def __init__(self, font, calc_screen):
        # calculator, names, cursor, offset, notice, scenario lease.
        self._state = [calc_screen, (), 0, 0, "", None]

    def activate(self):
        state = self._state
        if state[5] is not None:
            raise RuntimeError("Variable panel scenario transaction is active")
        self._rebuild()
        state[2] = 0
        state[3] = 0
        state[4] = ""

    def release_memory(self):
        state = self._state
        released = bool(state[1] or state[4])
        state[1] = ()
        state[2] = 0
        state[3] = 0
        state[4] = ""
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
            return
        state[2] = max(0, min(state[2], count - 1))
        if not state[3] <= state[2] < state[3] + 8:
            state[3] = state[2] // 8 * 8
        state[3] = min(state[3], (count - 1) // 8 * 8)

    def _draw_item(self, display, x, y, name, value_str, selected):
        label = (name + "=" + value_str)[:12]
        if selected:
            display.fill_rectangle(x, y, 90, 8, 14)
        display.draw_text8x8(
            x + 2, y, label, gs=0 if selected else 15)

    def draw(self, display):
        state = self._state
        calc = state[0]
        names = state[1]
        _theme.draw_header_fast(display, "Variables", b"Variables", None)
        display.draw_rectangle(0, 13, self.width, 40, 15)
        self._clamp()
        count = len(names)
        if not count:
            _theme.draw_empty(display, "No variables defined", None)
        else:
            row = 0
            while row < 4:
                left = state[3] + row
                right = left + 4
                y = 15 + row * 10
                if left < count:
                    name = names[left]
                    self._draw_item(
                        display, 4, y, name,
                        calc._fmt(calc.vars[name]),
                        left == state[2])
                if right < count:
                    name = names[right]
                    self._draw_item(
                        display, 108, y, name,
                        calc._fmt(calc.vars[name]),
                        right == state[2])
                row += 1
        if not count:
            hint, hint_bytes, right = "No variables", b"No variables", ""
        else:
            hint = (_inputbox.INPUT_FULL_NOTICE if state[4]
                    else "ENT insert  DEL remove")
            hint_bytes = (_inputbox.INPUT_FULL_NOTICE_BYTES if state[4]
                          else b"ENT insert  DEL remove")
            right = str(state[2] + 1) + "/" + str(count)
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
        elif label == "ESC":
            return "VAR_PANEL_DONE"
        elif row == 2 and col == 0 and state[2] >= 4:
            state[2] -= 4
        elif row == 2 and col == 2 and state[2] + 4 < count:
            state[2] += 4
        return "REDRAW" if changed or state[2] != previous else None
