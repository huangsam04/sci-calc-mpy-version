"""Calculator screen: expression input, history, and evaluation."""
import time
from ui.element import UIElement
from ui.inputbox import InputBox
from calc.parser import evaluate, ParseError
import calc.functions
from input.keyboard import get_key_label

HIST_ROW_H = 10    # ponytail: compact rows, 4 visible (0-3)
HIST_VISIBLE = 4


class CalculatorScreen(UIElement):
    def __init__(self, font, small_font=None):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.small_font = small_font or font
        self.input_box = InputBox(0, 0, 210, 12, 42, font)
        self.mode = 0        # 0=input, 1=history nav, 2=error popup
        self.history = []
        self._cursor = 0         # selected history index
        self._view_offset = 0    # first visible history index
        self._cooldown = 0
        self._hist_last_key = None
        self._esc_guard = 0       # prevent ESC double-fire after exiting history
        self.vars = {}
        self.func_table = {}
        # Error popup
        self._err_expr = ""
        self._err_pos = 0
        self._err_msg = ""
        self._err_time = 0

    def init(self, display):
        pass

    def activate(self):
        self.input_box.activate()
        self.mode = 0

    def _enter(self):
        expr = self.input_box.get_str().strip()
        if not expr:
            return
        try:
            result, self.vars = evaluate(expr, self.vars, self.func_table)
            self.history.insert(0, (expr, result))
            if len(self.history) > 20:
                self.history.pop()
            self.input_box.clear_str()
        except ParseError as e:
            self._err_expr = e.expr if e.expr else expr
            self._err_pos = e.pos
            self._err_msg = str(e)
            self._err_time = time.ticks_ms()
            self.mode = 2
        except Exception as e:
            self._err_expr = expr
            self._err_pos = 0
            self._err_msg = str(e)
            self._err_time = time.ticks_ms()
            self.mode = 2

    def _fmt(self, val):
        if isinstance(val, float):
            if abs(val) >= 1e10 or (abs(val) < 1e-6 and val != 0):
                return f"{val:.6g}"
            s = f"{val:.6f}".rstrip('0').rstrip('.')
            return s if s else "0"
        return str(val)

    def _clamp_view(self):
        max_off = max(0, len(self.history) - HIST_VISIBLE)
        if self._cursor < self._view_offset:
            self._view_offset = self._cursor
        if self._cursor >= self._view_offset + HIST_VISIBLE:
            self._view_offset = self._cursor - HIST_VISIBLE + 1
        if self._view_offset > max_off:
            self._view_offset = max_off
        if self._view_offset < 0:
            self._view_offset = 0

    def _draw_error_popup(self, display):
        display.fill_rectangle(0, 0, 210, 64, 3)
        display.fill_rectangle(5, 4, 200, 56, 0)
        display.draw_rectangle(5, 4, 200, 56, 15)
        expr = self._err_expr
        max_w = 190
        if self.font and self.font.measure_text(expr) > max_w:
            while len(expr) > 0 and self.font.measure_text(expr + "~") > max_w:
                expr = expr[:-1]
            expr += "~"
        if self.font:
            display.draw_text(10, 8, expr, self.font, gs=15)
        else:
            display.draw_text8x8(10, 8, expr, gs=15)
        if self._err_pos > 0 and self.font:
            prefix = self._err_expr[:self._err_pos]
            px = 10 + self.font.measure_text(prefix)
            if px < 190:
                display.draw_text(px, 18, "^", self.font, gs=15)
        elif self._err_pos > 0:
            px = 10 + self._err_pos * 8
            if px < 190:
                display.draw_text8x8(px, 18, "^", gs=15)
        msg = self._err_msg
        if len(msg) > 32:
            mid = msg.rfind(' ', 0, 32)
            if mid < 0:
                mid = 30
            line1 = msg[:mid]
            line2 = msg[mid:].strip()
            if self.small_font:
                display.draw_text(10, 30, line1, self.small_font, gs=15)
                display.draw_text(10, 39, line2, self.small_font, gs=15)
            else:
                display.draw_text8x8(10, 30, line1, gs=15)
                display.draw_text8x8(10, 39, line2, gs=15)
        else:
            if self.small_font:
                display.draw_text(10, 30, msg, self.small_font, gs=15)
            else:
                display.draw_text8x8(10, 30, msg, gs=15)
        hint = "[Any key to dismiss]"
        if self.small_font:
            display.draw_text(10, 50, hint, self.small_font, gs=10)
        else:
            display.draw_text8x8(10, 50, hint, gs=10)

    def draw(self, display):
        if self.mode == 2:
            if time.ticks_diff(time.ticks_ms(), self._err_time) > 10000:
                self.mode = 0
                self._err_msg = ""
            self._draw_error_popup(display)
            return

        # --- Input box (y=0..11) ---
        self.input_box.y = 0
        self.input_box.cursor.is_visible = (self.mode == 0)
        self.input_box.draw(display)

        # --- Divider ---
        display.draw_hline(0, 12, 210, 8)

        # --- Scrollable history (y=14..53, 4 rows × 10px) ---
        hist_start_y = 14
        self._clamp_view()
        for i in range(HIST_VISIBLE):
            hist_idx = self._view_offset + i
            if hist_idx >= len(self.history):
                break
            y = hist_start_y + i * HIST_ROW_H
            expr_str, result = self.history[hist_idx]
            rhs = "= " + self._fmt(result)
            is_selected = (self.mode == 1 and hist_idx == self._cursor)
            font_h = self.font.height if self.font else 8
            expr_disp = expr_str[:22] + "~" if len(expr_str) > 23 else expr_str

            if is_selected:
                display.fill_rectangle(2, y, 206, font_h, 12)
                if self.font:
                    display.draw_text(4, y, expr_disp, self.font, invert=True, gs=14)
                    display.draw_text(120, y, rhs, self.font, invert=True, gs=14)
                else:
                    display.draw_text8x8(4, y, expr_disp, gs=0)
                    display.draw_text8x8(120, y, rhs, gs=0)
            else:
                if self.font:
                    display.draw_text(4, y, expr_disp, self.font, gs=15)
                    display.draw_text(120, y, rhs, self.font, gs=15)
                else:
                    display.draw_text8x8(4, y, expr_disp, gs=15)
                    display.draw_text8x8(120, y, rhs, gs=15)

        # --- Status line (y=55..63) ---
        rad_str = "DEG" if calc.functions.ANGLE_MODE else "RAD"
        if self.mode == 0:
            total = len(self.history)
            mode_hint = f"[Tab:hist] [{total}]" if total else "[Tab:hist]"
        else:
            mode_hint = f"[{self._cursor+1}/{len(self.history)}] [Tab:input]"
        status = f"{mode_hint} [{rad_str}]"
        if self.small_font:
            display.draw_text(2, 55, status, self.small_font, gs=15)
        else:
            display.draw_text8x8(2, 55, status, gs=15)

    def update(self, kb):
        if self.mode == 2:
            if kb.pop_key_event() is not None:
                self.mode = 0
                self._err_msg = ""
            return None

        # Long-hold ESC: go back
        if kb.is_pressed(0, 0) and kb.get_hold_time(0, 0) > 1000:
            return "BACK"

        if self.mode == 0:
            action = self.input_box.update(kb)
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
        else:
            # History nav mode
            event = kb.pop_key_event()
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
