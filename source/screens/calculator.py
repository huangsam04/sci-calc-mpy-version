"""Calculator screen: expression input, history, and evaluation."""
import time
from anim.engine import insert_animation
from ui.element import UIElement
from ui.inputbox import InputBox
from ui.motion import RESULT_PULSE_MS
from calc.functions import EvalContext
from calc.number import (DEFAULT_DISPLAY_DIGITS, MAX_DISPLAY_DIGITS,
                         MIN_DISPLAY_DIGITS, Number, format_number)
from calc.parser import evaluate, ParseError
from input.keyboard import get_key_label
from ui.theme import (SHELL_CALCULATOR, draw_footer, draw_footer_fast,
                      draw_page_shell, draw_text, fit_text, text_width)
from ui.error_popup import ErrorPopup
from ui.residency import SETTLE_MORE, SETTLE_REDRAW
from utils.storage import _decode_numbers, _encode_numbers

INPUT_SINGLE_H = 12
INPUT_DOUBLE_H = 22
INPUT_DIVIDER_GAP = 1
HIST_TOP_GAP = 2
HIST_ROW_H = 9
HIST_VISIBLE_SINGLE = 4
HIST_VISIBLE_DOUBLE = 3
MAX_EXPRESSION_CHARS = 96


class CalculatorScreen(UIElement):
    swap_key = "calculator"
    transition_title = "Calculator"

    def __init__(self, font, small_font=None, registry=None, variables=None,
                 display_digits=DEFAULT_DISPLAY_DIGITS):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.small_font = small_font or font
        self._input_footer_hint = fit_text(
            "ENT calc  Tab history", 126, self.small_font)
        self._input_footer_hint_bytes = self._input_footer_hint.encode()
        self._history_footer_hint = fit_text(
            "8/2 select  Tab input", 126, self.small_font)
        self._history_footer_hint_bytes = self._history_footer_hint.encode()
        self.input_box = InputBox(0, 0, 210, INPUT_SINGLE_H,
                                  MAX_EXPRESSION_CHARS, font, visible_rows=2)
        self.mode = 0        # 0=input, 1=history nav, 2=error popup
        self.history = []
        self._cursor = 0         # selected history index
        self._view_offset = 0    # first visible history index
        self._cooldown = 0
        self._hist_last_key = None
        self._esc_guard = 0       # prevent ESC double-fire after exiting history
        self.context = EvalContext(variables if variables is not None else {}, registry)
        self.display_digits = DEFAULT_DISPLAY_DIGITS
        self.set_display_digits(display_digits)
        self.storage_error = ""
        self._storage_error_time = 0
        self.error_popup = ErrorPopup(font, self.small_font)
        self._result_pulse = 0
        self._history_restore = None
        self._history_restore_index = 0
        self._presented_editor_state = None

    def activate(self):
        self.input_box.activate()
        self.mode = 0
        self._presented_editor_state = None

    def deactivate(self):
        # Navigation cancels owned animations after this hook. Clear the
        # rendered value too, so an interrupted pulse cannot remain visible
        # when the calculator is opened again.
        self._result_pulse = 0
        self._presented_editor_state = None

    def animation_children(self):
        return (self.input_box, self.error_popup)

    def release_memory(self):
        """Release derived editor/error state while preserving 20-entry history."""
        released = self.error_popup.release_memory()
        return self.input_box.release_memory() or released

    def snapshot_state(self):
        history = []
        for expr, result in self.history[:20]:
            history.append([expr, _encode_numbers(result)])
        return {
            "input": self.input_box.get_str(),
            "input_cursor": self.input_box.cursor_pos,
            "input_view": self.input_box.view_offset,
            "mode": self.mode if self.mode in (0, 1) else 0,
            "history_cursor": self._cursor,
            "history_view": self._view_offset,
            "history": history,
        }

    def reset_state(self):
        self.input_box.str = ""
        self.input_box.cursor_pos = 0
        self.input_box.view_offset = 0
        self.input_box._layout_dirty = True
        self.input_box.cursor.is_visible = False
        self.history = []
        self._history_restore = None
        self._history_restore_index = 0
        self.mode = 0
        self._cursor = 0
        self._view_offset = 0
        self._result_pulse = 0
        self.error_popup.dismiss()
        self._presented_editor_state = None

    def activate_default(self):
        self.mode = 0
        self.input_box.cursor.is_visible = False
        self._presented_editor_state = None

    def restore_state(self, state):
        text = state.get("input", "")
        if not isinstance(text, str):
            raise ValueError("Invalid calculator input snapshot")
        self.input_box.set_str(text, immediate=True)
        self.input_box.cursor_pos = max(
            0, min(int(state.get("input_cursor", len(text))), len(text)))
        self.input_box.view_offset = max(0, int(state.get("input_view", 0)))
        self.mode = int(state.get("mode", 0))
        self._cursor = max(0, int(state.get("history_cursor", 0)))
        self._view_offset = max(0, int(state.get("history_view", 0)))
        rows = state.get("history", [])
        if not isinstance(rows, list) or len(rows) > 20:
            raise ValueError("Invalid calculator history snapshot")
        self.history = []
        self._history_restore = rows
        self._history_restore_index = 0
        self.input_box.cursor.is_visible = (self.mode == 0)
        self._presented_editor_state = None

    def settle_step(self):
        rows = self._history_restore
        if rows is None:
            return 0
        index = self._history_restore_index
        if index >= len(rows):
            self._history_restore = None
            self._history_restore_index = 0
            return 0
        row = rows[index]
        if (not isinstance(row, list) or len(row) != 2
                or not isinstance(row[0], str)):
            raise ValueError("Invalid calculator history row")
        self.history.append((row[0], _decode_numbers(row[1])))
        self._history_restore_index = index + 1
        if self._history_restore_index < len(rows):
            return SETTLE_REDRAW | SETTLE_MORE
        self._history_restore = None
        self._history_restore_index = 0
        return SETTLE_REDRAW

    def draw_transition_default(self, display):
        draw_page_shell(display, SHELL_CALCULATOR, self.font)

    def _enter(self):
        expr = self.input_box.get_str().strip()
        if not expr:
            return
        try:
            result = evaluate(expr, self.context)
            self.history.insert(0, (expr, result))
            if len(self.history) > 20:
                self.history.pop()
            self.input_box.clear_str()
            self._result_pulse = 15
            insert_animation(self, "_result_pulse", 15, 0,
                             RESULT_PULSE_MS)
        except ParseError as e:
            self.error_popup.show(e.expr if e.expr else expr, e, e.pos)
            self.mode = 2
        except Exception as e:
            self.error_popup.show(expr, e)
            self.mode = 2

    def _fmt(self, val):
        if isinstance(val, (Number, int, float)):
            return format_number(val, self.display_digits)
        return str(val)

    def set_display_digits(self, digits):
        """Apply the user preference without changing stored calculation values."""
        if not isinstance(digits, int):
            digits = DEFAULT_DISPLAY_DIGITS
        self.display_digits = max(MIN_DISPLAY_DIGITS,
                                  min(MAX_DISPLAY_DIGITS, digits))

    def _panel_layout(self):
        """Size the editor first, then give the remaining room to history."""
        if self.input_box.active_rows > 1:
            input_height = INPUT_DOUBLE_H
            history_visible = HIST_VISIBLE_DOUBLE
        else:
            input_height = INPUT_SINGLE_H
            history_visible = HIST_VISIBLE_SINGLE
        self.input_box.set_height(input_height)
        divider_y = input_height + INPUT_DIVIDER_GAP
        history_start_y = divider_y + HIST_TOP_GAP
        return divider_y, history_start_y, history_visible

    def _editor_present_state(self):
        self._panel_layout()
        return (
            self.mode,
            self.input_box.str,
            self.input_box.cursor_pos,
            self.input_box.cursor.x,
            self.input_box.cursor.y,
            self.input_box.height,
            len(self.history),
            self._cursor,
            self._view_offset,
            self._result_pulse,
            self.storage_error,
        )

    def get_present_rows(self):
        """Limit ordinary editor feedback to its rows and the counter footer."""
        current = self._editor_present_state()
        previous = self._presented_editor_state
        if previous is None or current[0] != 0 or previous[0] != 0:
            return None
        if current[5] != previous[5] or current[6:] != previous[6:]:
            return None
        if current[1:5] == previous[1:5]:
            return None
        return ((0, current[5]), (54, 10))

    def mark_presented(self):
        self._presented_editor_state = self._editor_present_state()

    def _clamp_view(self, history_visible):
        max_off = max(0, len(self.history) - history_visible)
        if self._cursor < self._view_offset:
            self._view_offset = self._cursor
        if self._cursor >= self._view_offset + history_visible:
            self._view_offset = self._cursor - history_visible + 1
        if self._view_offset > max_off:
            self._view_offset = max_off
        if self._view_offset < 0:
            self._view_offset = 0

    def _history_text(self, expr, result):
        """Fit expression and result into one row without letting them overlap."""
        result_text = fit_text("= " + self._fmt(result), 78, self.font)
        result_x = max(108, self.width - text_width(result_text, self.font) - 4)
        expr_width = max(24, result_x - 8)
        return fit_text(expr, expr_width, self.font), result_text, result_x

    def _draw_history_row(self, display, y, expr, result, selected, fresh=False):
        expr_text, result_text, result_x = self._history_text(expr, result)
        if selected:
            display.fill_rectangle(2, y, 206, HIST_ROW_H - 1, 12)
        elif fresh:
            pulse_gs = 3 + min(8, self._result_pulse // 2)
            display.fill_rectangle(2, y, 206, HIST_ROW_H - 1, pulse_gs)
        draw_text(display, 4, y, expr_text, self.font,
                  gs=14 if selected else 15, invert=selected)
        draw_text(display, result_x, y, result_text, self.font,
                  gs=14 if selected else 15, invert=selected)

    def _draw_editor(self, display):
        self.input_box.y = 0
        divider_y, hist_start_y, hist_visible = self._panel_layout()
        self.input_box.cursor.is_visible = (self.mode == 0)
        self.input_box.draw(display)
        display.draw_hline(0, divider_y, 210, 8)
        return hist_start_y, hist_visible

    def _draw_footer(self, display):
        if (self.storage_error
                and time.ticks_diff(time.ticks_ms(),
                                    self._storage_error_time) < 5000):
            draw_footer(display, self.storage_error, self.small_font)
        elif self.mode == 0:
            right = (str(len(self.input_box.get_str()))
                     + "/" + str(MAX_EXPRESSION_CHARS))
            draw_footer_fast(display, self._input_footer_hint,
                             self._input_footer_hint_bytes,
                             self.small_font, right)
        else:
            right = str(self._cursor + 1) + "/" + str(len(self.history))
            draw_footer_fast(display, self._history_footer_hint,
                             self._history_footer_hint_bytes,
                             self.small_font, right)

    def draw_present_rows(self, display):
        """Redraw only the rows declared safe by ``get_present_rows``."""
        self._draw_editor(display)
        self._draw_footer(display)

    def draw(self, display):
        if self.mode == 2:
            if self.error_popup.expired():
                self.mode = 0
                self.error_popup.dismiss()
            else:
                self.error_popup.draw(display)
                return

        # --- One-line editor that expands to two rows only when needed ---
        hist_start_y, hist_visible = self._draw_editor(display)

        # --- History: four rows with a compact editor, three when expanded ---
        self._clamp_view(hist_visible)
        for i in range(hist_visible):
            hist_idx = self._view_offset + i
            if hist_idx >= len(self.history):
                break
            y = hist_start_y + i * HIST_ROW_H
            expr_str, result = self.history[hist_idx]
            is_selected = (self.mode == 1 and hist_idx == self._cursor)
            self._draw_history_row(display, y, expr_str, result, is_selected,
                                   fresh=(hist_idx == 0 and self._result_pulse > 0))

        # --- Status line (y=55..63) ---
        self._draw_footer(display)

    def update(self, kb, event=None):
        if self.mode == 2:
            if event is not None:
                self.mode = 0
                self.error_popup.dismiss()
            return None

        # Long-hold ESC: go back
        if kb.consume_long_press(0, 0, 1000):
            return "BACK"

        if self.mode == 0:
            action = self.input_box.update(kb, event)
            if action == "ENT":
                if kb.is_pressed(4, 0):
                    self.input_box.insert_str("=")
                else:
                    self._enter()
            elif action == "tab":
                if self.history:
                    self.mode = 1
                    self._cursor = 0
                    self._view_offset = 0
                    self._cooldown = time.ticks_ms()
            elif action == "stab":
                return "VARIABLE_PANEL"
            elif action == "ESC":
                # Guard: ignore ESC within 500ms of leaving history mode
                if time.ticks_diff(time.ticks_ms(), self._esc_guard) < 500:
                    return None
                if self.input_box.get_str():
                    self.input_box.clear_str()
                else:
                    return "BACK"
            elif action == "rpn":
                if not kb.is_pressed(4, 0):
                    return "FUNC_PICKER"
            elif action == "DELETE":
                # Repeated DEL has no new edge event; explicitly request a frame.
                return "REDRAW"
        else:
            # History nav mode
            if event is None:
                return None

            r, c, shift = event
            now = time.ticks_ms()

            # Per-key cooldown: same-key rapid-fire prevention, different keys pass through
            if (r, c) == self._hist_last_key and time.ticks_diff(now, self._cooldown) < 180:
                return None
            self._cooldown = now
            self._hist_last_key = (r, c)

            label = get_key_label(r, c, shift)

            if label in ("2", "down"):
                if self._cursor < len(self.history) - 1:
                    self._cursor += 1
            elif label in ("8", "up"):
                if self._cursor > 0:
                    self._cursor -= 1
            elif label == "ENT":
                # Append result to existing input
                if self.history:
                    _, result = self.history[self._cursor]
                    self.input_box.insert_str(self._fmt(result))
                    self.mode = 0
            # Left/Right (physical 4/6 keys): append expression to input
            elif (r == 2 and c == 0) or (r == 2 and c == 2):
                if self.history:
                    expr_str, _ = self.history[self._cursor]
                    self.input_box.insert_str(expr_str)
                    self.mode = 0
            elif label == "tab":
                self.mode = 0
            elif label == "stab":
                return "VARIABLE_PANEL"
            elif label == "ESC":
                self.mode = 0
                self._esc_guard = time.ticks_ms()

        return None

    @property
    def vars(self):
        return self.context.variables

    def set_storage_error(self, message):
        self.storage_error = message
        self._storage_error_time = time.ticks_ms()
