"""Plot screen — full-screen graph with slide-in expression editor."""
from ui.element import UIElement
from ui.inputbox import InputBox
from calc.parser import evaluate, ParseError
from calc.functions import _current_func_table
from anim.engine import insert_animation
from input.keyboard import get_key_label


# Layout constants
OVERLAY_H = 14       # height of input overlay when visible
HINT_H = 10          # bottom hint bar
GRAPH_PAD_X = 2      # graph left/right padding


class PlotScreen(UIElement):
    def __init__(self, font, small_font=None):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.small_font = small_font or font
        self.input_box = InputBox(0, -OVERLAY_H, 210, 12, 42, font)

        self.expr = ""
        self.x_min = -10.0
        self.x_max = 10.0
        self._curve = []
        self._y_min = 0.0
        self._y_max = 1.0
        self._err_msg = ""

        # Animation targets — driven by anim engine
        self._overlay_y = -OVERLAY_H   # input overlay y position
        self._graph_top = 0            # graph area top y

        self.mode = 0   # 0=view, 1=edit

    def activate(self):
        self.mode = 0
        self._overlay_y = -OVERLAY_H
        self._graph_top = 0
        self.input_box.activate()
        # Preserve any text set by letter panel, else restore last expression
        if not self.input_box.get_str() and self.expr:
            self.input_box.set_str(self.expr)

    # ── mode switching ──────────────────────────────────────────

    def _enter_edit(self, prefill=""):
        """Slide input overlay down, optionally prefill text."""
        if prefill:
            self.input_box.insert_str(prefill)
        self.mode = 1
        insert_animation(self, '_overlay_y', self._overlay_y, 0, 180, "INDENT")
        insert_animation(self, '_graph_top', self._graph_top, OVERLAY_H, 180, "INDENT")
        self.input_box.cursor.is_visible = True

    def _leave_edit(self, plot=True):
        """Slide input overlay up. If plot=True, re-plot with current expression."""
        self.mode = 0
        self.expr = self.input_box.get_str().strip()
        insert_animation(self, '_overlay_y', self._overlay_y, -OVERLAY_H, 180, "INDENT")
        insert_animation(self, '_graph_top', self._graph_top, 0, 180, "INDENT")
        self.input_box.cursor.is_visible = False
        if plot and self.expr:
            self._plot()

    # ── plotting ────────────────────────────────────────────────

    def _eval_func(self, x_val):
        ft = _current_func_table or {}
        test_vars = {"x": x_val}
        try:
            result, _ = evaluate(self.expr, test_vars, ft)
            return float(result), True
        except Exception:
            return 0.0, False

    def _plot(self):
        self._curve = []
        self._err_msg = ""
        if not self.expr.strip():
            return

        graph_w = self.width - GRAPH_PAD_X * 2
        graph_left = GRAPH_PAD_X
        graph_right = self.width - GRAPH_PAD_X

        y_vals = []
        pts = []

        for px in range(graph_left, graph_right + 1):
            x_val = self.x_min + (px - graph_left) / graph_w * (self.x_max - self.x_min)
            y_val, ok = self._eval_func(x_val)
            if ok and abs(y_val) < 1e30:
                y_vals.append(y_val)
                pts.append((px, y_val))
            else:
                pts.append((px, None))

        if not y_vals:
            self._y_min = -1.0
            self._y_max = 1.0
            self._curve = []
            return

        y_min = min(y_vals)
        y_max = max(y_vals)
        pad = max((y_max - y_min) * 0.1, 0.5)
        if y_max - y_min < 1e-10:
            pad = 1.0
        self._y_min = y_min - pad
        self._y_max = y_max + pad

        y_range = self._y_max - self._y_min
        graph_h = self.height - HINT_H - self._graph_top
        graph_bot = self.height - HINT_H

        self._curve = []
        for px, y_val in pts:
            if y_val is None:
                self._curve.append((px, None))
            else:
                ratio = (y_val - self._y_min) / y_range
                py = graph_bot - int(ratio * graph_h)
                py = max(self._graph_top, min(graph_bot, py))
                self._curve.append((px, py))

    # ── drawing ─────────────────────────────────────────────────

    def _draw_graph(self, display):
        graph_w = self.width - GRAPH_PAD_X * 2
        graph_left = GRAPH_PAD_X
        graph_right = self.width - GRAPH_PAD_X
        graph_h = self.height - HINT_H - self._graph_top
        graph_bot = self.height - HINT_H

        # Border
        display.draw_rectangle(graph_left - 1, self._graph_top,
                               graph_right - graph_left + 2, graph_h, 8)

        # Axes
        y_range = self._y_max - self._y_min
        if y_range > 0 and self._y_min <= 0 <= self._y_max:
            ratio = (0 - self._y_min) / y_range
            y_zero = graph_bot - int(ratio * graph_h)
            if self._graph_top <= y_zero <= graph_bot:
                display.draw_hline(graph_left, y_zero, graph_w + 1, 6)

        x_range = self.x_max - self.x_min
        if x_range > 0 and self.x_min <= 0 <= self.x_max:
            ratio = (0 - self.x_min) / x_range
            x_zero = graph_left + int(ratio * graph_w)
            if graph_left <= x_zero <= graph_right:
                display.draw_vline(x_zero, self._graph_top, graph_h + 1, 6)

        # Curve
        prev_x = prev_y = None
        for px, py in self._curve:
            if py is not None:
                display.draw_pixel(px, py, 15)
                if prev_x is not None:
                    display.draw_line(prev_x, prev_y, px, py, 15)
                prev_x, prev_y = px, py
            else:
                prev_x = prev_y = None

    def _draw_overlay(self, display):
        """Input box overlay — only visible when _overlay_y > -OVERLAY_H."""
        oy = self._overlay_y
        if oy <= -OVERLAY_H:
            return
        # Background
        display.fill_rectangle(0, oy, self.width, OVERLAY_H, 0)
        # Input box
        self.input_box.y = oy + 1
        self.input_box.cursor.y = oy + 2
        self.input_box.draw(display)
        # Divider
        display.draw_hline(0, oy + OVERLAY_H - 1, self.width, 10)

    def _draw_hint(self, display):
        y = self.height - HINT_H + 1
        if self.mode == 0:
            if self._err_msg:
                hint = self._err_msg
            else:
                hint = f"x:[{self.x_min:.4g},{self.x_max:.4g}]  y:[{self._y_min:.4g},{self._y_max:.4g}]"
            hint2 = "[ENT:edit] [RPN:x] [ESC:back]"
        else:
            hint = "[ENT:plot] [RPN:x] [ESC:cancel]"
            hint2 = "[Sh+RPN:letters] [Sh+Tab:reset]"

        if self.small_font:
            display.draw_text(2, y, hint, self.small_font, gs=15)
            display.draw_text(120, y, hint2, self.small_font, gs=10)
        else:
            display.draw_text8x8(2, y, hint, gs=15)
            display.draw_text8x8(120, y, hint2, gs=10)

    def draw(self, display):
        self._draw_graph(display)
        self._draw_overlay(display)
        self._draw_hint(display)

    # ── input ───────────────────────────────────────────────────

    def update(self, kb):
        # Long-hold ESC: go back
        if kb.is_pressed(0, 0) and kb.get_hold_time(0, 0) > 1000:
            return "BACK"

        if self.mode == 0:
            # ── View mode: any key opens editor ──
            event = kb.pop_key_event()
            if event is not None:
                r, c, shift = event
                if r == 0 and c == 0:  # ESC → back
                    return "BACK"
                elif r == 3 and c == 3:  # ENT → open empty editor
                    self._enter_edit()
                elif r == 3 and c == 5 and not shift:  # RPN → editor with 'x'
                    self._enter_edit("x")
                elif r == 3 and c == 5 and shift:
                    # Shift+RPN handled by global hotkey → letter panel
                    pass
                else:
                    # Any other key: open editor and insert the character
                    label = get_key_label(r, c, shift)
                    if label and len(label) == 1 and label not in (
                        "ENT", "ESC", "tab", "stab", "ang", "rpn",
                        "left", "right", "up", "down", "DEL"
                    ):
                        self._enter_edit(label)
                    elif label in ("sin", "cos", "tan", "sec", "csc", "cot",
                                   "asin", "acos", "atan", "ln", "exp", "sqrt"):
                        self._enter_edit(label + "(")
                    else:
                        self._enter_edit()
                return None

        else:
            # ── Edit mode ──
            action = self.input_box.update(kb)

            if action == "ENT":
                self._leave_edit(plot=True)
            elif action == "rpn":
                self.input_box.insert_str("x")
            elif action == "ESC":
                self._leave_edit(plot=False)
            elif action == "stab":
                self.x_min = -10.0
                self.x_max = 10.0

        return None
