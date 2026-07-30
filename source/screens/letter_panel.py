# Letter overlay panel with complete alphabet and explicit symbol layers.
from ui.element import UIElement
from ui.inputbox import INPUT_FULL_NOTICE
from ui.theme import draw_footer


# Each table has five six-key rows.  A space marks one of the four controls;
# the overlay has no literal-space key, so no sentinel object or 30-entry
# tuple has to stay resident.
_LAYER_CHARS = (
    " ABCDE" "FGHIJK" "LMNOPQ" "RSTUVW" " XY Z "
    " abcde" "fghijk" "lmnopq" "rstuvw" " xy z "
    ' ";()[' "]{}+-*" "/^=,_:" ".?<>!@" " #$ % ")
class LetterPanel(UIElement):
    transition_title = "Letters"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("_state",)

    def __init__(self, font, input_box):
        # input target, draft, layer, notice, scenario lease.
        self._state = [input_box, "", 0, "", None]

    def activate(self):
        state = self._state
        if state[4] is not None:
            raise RuntimeError("Letters scenario transaction is active")
        self._set_draft("")
        state[2] = 0
        state[3] = ""

    def release_memory(self):
        state = self._state
        if state[4] is not None:
            raise RuntimeError("Letters scenario transaction is active")
        released = bool(state[1] or state[3])
        state[1] = ""
        state[2] = 0
        state[3] = ""
        return released

    def open_scenario_transaction(self):
        from screens.letter_panel_scenario import (
            LetterPanelScenarioTransaction)
        return LetterPanelScenarioTransaction(self)

    def blocks_global_shortcuts(self):
        return True

    def _get_char(self, row, col):
        if row < 0 or row >= 5 or col < 0 or col >= 6:
            return None
        char = _LAYER_CHARS[self._state[2] * 30 + row * 6 + col]
        return None if char == " " else char

    def _set_draft(self, text):
        self._state[1] = text

    def draw(self, display):
        state = self._state
        # Keep the editable tail visible inside the 210px content area.
        visible = state[1][-22:]
        display.draw_text8x8(2, 1, "[", gs=15)
        cx = 10
        if visible:
            display.draw_text8x8(cx, 1, visible, gs=15)
            cx = 10 + len(visible) * 8
        display.draw_vline(cx, 1, 8, 15)
        display.draw_text8x8(cx + 2, 1, "]", gs=15)

        # Reuse table characters directly.  The four control labels are
        # selected by fixed index, so no derived legend table stays resident.
        layer_start = state[2] * 30
        row_idx = 0
        while row_idx < 5:
            y = 12 + row_idx * 8
            x = 4
            index = row_idx * 6
            col = 0
            while col < 6:
                key_index = index + col
                label = _LAYER_CHARS[layer_start + key_index]
                if label == " ":
                    if key_index == 0:
                        label = "ESC"
                        label_x = x
                    elif key_index == 24:
                        label = "Sh"
                        label_x = x + 8
                    elif key_index == 27:
                        label = "Bk"
                        label_x = x + 8
                    else:
                        label = "OK"
                        label_x = x + 8
                else:
                    label_x = x + 8
                display.draw_text8x8(label_x, y, label, gs=15)
                x += 32
                col += 1
            row_idx += 1
        hint = INPUT_FULL_NOTICE if state[3] else "OK insert ESC"
        layer_label = ("ABC" if state[2] == 0 else
                       "abc" if state[2] == 1 else "SYM")
        draw_footer(display, hint, None, layer_label)

    def update(self, kb, event=None):
        state = self._state
        if state[4] is not None:
            raise RuntimeError("Letters scenario transaction is active")
        if event is None:
            return None

        r, c, _ = event  # layer selection uses physical keys, not live Shift

        # ESC (0,0): cancel
        if r == 0 and c == 0:
            self._set_draft("")
            return "LETTER_DONE"

        # OK (4,5): confirm
        if r == 4 and c == 5:
            if state[1] and not state[0].try_insert(state[1]):
                notice_changed = state[3] != INPUT_FULL_NOTICE
                state[3] = INPUT_FULL_NOTICE
                return "REDRAW" if notice_changed else None
            self._set_draft("")
            state[3] = ""
            return "LETTER_DONE"

        # Physical DEL (4,3): backspace. ANG (4,4) is Z in this overlay.
        if r == 4 and c == 3:
            changed = bool(state[1] or state[3])
            if state[1]:
                self._set_draft(state[1][:-1])
            state[3] = ""
            return "REDRAW" if changed else None

        # Shift cycles uppercase, lowercase, and the explicit symbol layer.
        if r == 4 and c == 0:
            state[2] = (state[2] + 1) % 3
            return "REDRAW"

        char = self._get_char(r, c)
        if char is None:
            return None
        remaining = ((state[0]._state[0] >> 19) & 511
                     ) - len(state[0].get_str())
        if len(state[1]) >= remaining:
            return None
        self._set_draft(state[1] + char)
        state[3] = ""
        return "REDRAW"
