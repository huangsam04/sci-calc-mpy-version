from ui.element import UIElement
from ui import theme as _theme


class FunctionPicker(UIElement):
    transition_title = "Functions"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("_state",)

    def __init__(self, font, calc_screen):
        # calc, names, cursor, offset, notice, scenario lease.
        # Build the only names backing block while the boot heap is contiguous.
        # Later activations refill and sort the same list in place.
        names = sorted(calc_screen.context.registry.keys())
        self._state = [calc_screen, names, 0, 0, "", None]

    def activate(self):
        state = self._state
        if state[5] is not None:
            raise RuntimeError("Function picker scenario transaction is active")
        names = state[1]
        names[:] = ()
        for name in state[0].context.registry.keys():
            names.append(name)
        names.sort()
        state[2] = 0
        state[3] = 0
        state[4] = ""

    def release_memory(self):
        state = self._state
        released = bool(state[4])
        state[2] = 0
        state[3] = 0
        state[4] = ""
        return released

    def open_scenario_transaction(self):
        from screens.function_picker_scenario import (
            FunctionPickerScenarioTransaction)
        return FunctionPickerScenarioTransaction(self)

    def draw(self, display):
        state = self._state
        names = state[1]
        _theme.draw_header_fast(display, "Functions", b"Functions", None)
        display.draw_rectangle(0, 13, self.width, 40, 15)
        count = len(names)
        if count:
            state[2] = max(0, min(state[2], count - 1))
            if not state[3] <= state[2] < state[3] + 8:
                state[3] = state[2] // 8 * 8
            state[3] = min(state[3], (count - 1) // 8 * 8)
        row = 0
        while row < 4:
            y = 15 + row * 10
            column = 0
            while column < 2:
                index = state[3] + row + column * 4
                if index < count:
                    selected = index == state[2]
                    x = 4 if column == 0 else 105
                    if selected:
                        display.fill_rectangle(x, y, 96, 8, 12)
                    display.draw_text8x8(
                        x + 2, y, names[index][:12],
                        gs=0 if selected else 15)
                column += 1
            row += 1
        if not count:
            hint = "[No functions loaded]"
            hint_bytes = b"[No functions loaded]"
            right = ""
        elif state[4]:
            hint = "Input full"
            hint_bytes = b"Input full"
            right = str(state[2] + 1) + "/" + str(count) + " ENT"
        else:
            hint = "UP/DN move  4/6 column"
            hint_bytes = b"UP/DN move  4/6 column"
            right = str(state[2] + 1) + "/" + str(count) + " ENT"
        _theme.draw_footer_fast(display, hint, hint_bytes, None, right)

    def update(self, kb, event=None):
        state = self._state
        if state[5] is not None or event is None:
            return None
        row, col, _shift = event
        names = state[1]
        count = len(names)
        previous = state[2]
        if row == 3 and col == 1 and state[2] < count - 1:
            state[2] += 1
        elif row == 1 and col == 1 and state[2] > 0:
            state[2] -= 1
        elif row == 2 and col == 0 and state[2] >= 4:
            state[2] -= 4
        elif row == 2 and col == 2 and state[2] + 4 < count:
            state[2] += 4
        elif row == 3 and col == 3:
            if not names:
                return "FUNC_PICKER_DONE"
            name = names[state[2]]
            calc_screen = state[0]
            entry = calc_screen.context.registry.get(name)
            kind = entry[2] if entry else None
            text = (name + "(" if kind == "prefix" or kind == "list"
                    else name)
            if calc_screen.input_box.try_insert(text):
                state[4] = ""
                return "FUNC_PICKER_DONE"
            if state[4] == "Input full":
                return None
            state[4] = "Input full"
            return "REDRAW"
        elif row == 0 and col == 0:
            return "FUNC_PICKER_DONE"
        return "REDRAW" if state[2] != previous else None
