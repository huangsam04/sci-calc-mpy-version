"""Plot screen — full-screen graph with slide-in expression editor."""
from framebuf import FrameBuffer, MONO_HMSB  # type: ignore
from ui.element import UIElement
from ui.inputbox import InputBox
from calc.parser import compile_expression, evaluate_program, ParseError
from calc.functions import EvalContext
from anim.engine import insert_animation
from input.keyboard import get_key_label
from ui.theme import draw_footer
from ui.error_popup import ErrorPopup
from ui.motion import PANEL_SLIDE_MS, MOTION_EASING


# Layout constants
OVERLAY_H = 14
HINT_H = 10
GRAPH_PAD_X = 2


class PlotScreen(UIElement):
    def __init__(self, font, small_font=None, registry=None):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.small_font = small_font or font
        self.input_box = InputBox(0, -OVERLAY_H, 210, 12, 42, font)

        self.expr = ""
        self.x_min = -10.0
        self.x_max = 10.0
        self._y_min = -5.0
        self._y_max = 5.0
        self.error_popup = ErrorPopup(font, self.small_font)
        self._edit_original = ""

        # Pre-allocated curve buffer — rendered once, blitted each frame
        self._curve_fb = None   # (FrameBuffer, w, h) or None if not plotted
        self._curve_buf = None  # bytearray backing the FrameBuffer

        self._overlay_y = -OVERLAY_H
        self.mode = 0
        self.registry = registry
        self._program = None
        self._eval_vars = {"x": 0.0}
        self._eval_context = EvalContext(self._eval_vars, registry)

    def activate(self):
        self.mode = 0
        self._overlay_y = -OVERLAY_H
        self.input_box.activate()
        if not self.input_box.get_str() and self.expr:
            self.input_box.set_str(self.expr)

    def animation_children(self):
        return (self.input_box,)

    # ── zoom / pan ───────────────────────────────────────────────

    def _zoom_y(self, factor):
        mid = (self._y_min + self._y_max) / 2.0
        half = (self._y_max - self._y_min) / 2.0 * factor
        self._y_min = mid - half
        self._y_max = mid + half
        if self.expr:
            self._render_curve(auto_scale=False)

    def _zoom_x(self, factor):
        mid = (self.x_min + self.x_max) / 2.0
        half = (self.x_max - self.x_min) / 2.0 * factor
        self.x_min = mid - half
        self.x_max = mid + half
        if self.expr:
            self._render_curve()

    def _pan_x(self, fraction):
        shift = (self.x_max - self.x_min) * fraction
        self.x_min += shift
        self.x_max += shift
        if self.expr:
            self._render_curve()

    # ── mode switching ──────────────────────────────────────────

    def _enter_edit(self, prefill=""):
        self._edit_original = self.expr
        if prefill:
            self.input_box.insert_str(prefill)
        self.mode = 1
        insert_animation(self, '_overlay_y', self._overlay_y, 0,
                         PANEL_SLIDE_MS, MOTION_EASING)
        self.input_box.cursor.is_visible = True

    def _leave_edit(self, plot=True):
        self.mode = 0
        insert_animation(self, '_overlay_y', self._overlay_y, -OVERLAY_H,
                         PANEL_SLIDE_MS, MOTION_EASING)
        self.input_box.cursor.is_visible = False
        if plot:
            self.expr = self.input_box.get_str().strip()
            if self.expr:
                self._render_curve()
            else:
                self._curve_fb = None
        else:
            self.input_box.set_str(self._edit_original)

    # ── curve rendering (2-pass: find range → draw to buffer) ────

    def _eval(self, x_val):
        try:
            self._eval_vars["x"] = x_val
            result = evaluate_program(self._program, self._eval_context)
            return float(result), True, ""
        except Exception as e:
            return 0.0, False, str(e)

    def _render_curve(self, auto_scale=True):
        """Compile and render; optionally derive a fresh visible Y range."""
        if not self.expr.strip():
            self._curve_fb = None
            return

        try:
            self._program = compile_expression(self.expr, self.registry)
        except ParseError as error:
            self._curve_fb = None
            self.error_popup.show(self.expr, error, error.pos)
            self.mode = 2
            return

        graph_w = self.width - GRAPH_PAD_X * 2
        graph_left = GRAPH_PAD_X
        graph_right = self.width - GRAPH_PAD_X
        graph_h = self.height - HINT_H
        n = graph_right - graph_left + 1

        # ── Pass 1: sample once and derive a robust visible range ──
        samples = [None] * n
        valid_values = []
        first_err = ""

        for px in range(graph_left, graph_right + 1):
            x_val = self.x_min + (px - graph_left) / graph_w * (self.x_max - self.x_min)
            y_val, ok, err = self._eval(x_val)
            if ok and abs(y_val) < 1e6:
                sample_index = px - graph_left
                samples[sample_index] = y_val
                valid_values.append(y_val)
            elif err and not first_err:
                first_err = err

        if not valid_values:
            self._y_min = -1.0
            self._y_max = 1.0
            self._curve_fb = None
            self.error_popup.show(self.expr, first_err or "Cannot evaluate expression")
            self.mode = 2
            return

        if auto_scale:
            valid_values.sort()
            y_min = valid_values[0]
            y_max = valid_values[-1]
            y_range = y_max - y_min

            # Compare the full extent with the central 90%.  Smooth curves
            # retain their true extrema, while a few samples next to a pole
            # cannot flatten everything else on screen.
            trim = max(1, len(valid_values) // 20)
            robust_min = valid_values[trim]
            robust_max = valid_values[-trim - 1]
            robust_range = robust_max - robust_min
            if (robust_range > 1e-10
                    and y_range > robust_range * 4.0):
                y_min = robust_min
                y_max = robust_max
                y_range = robust_range

            pad = max(y_range * 0.1, 0.5)
            if y_range < 1e-10:
                pad = 1.0
            self._y_min = y_min - pad
            self._y_max = y_max + pad

        # ── Allocate / reuse curve buffer ──
        buf_size = ((n + 7) // 8) * graph_h  # MONO_HMSB: 1 bit per pixel
        if self._curve_buf is None or len(self._curve_buf) < buf_size:
            self._curve_buf = bytearray(buf_size)
        else:
            # Zero existing buffer
            for i in range(buf_size):
                self._curve_buf[i] = 0
        self._curve_fb = FrameBuffer(self._curve_buf, n, graph_h, MONO_HMSB)

        # ── Pass 2: draw the cached samples to the mono buffer ──
        y_range = self._y_max - self._y_min
        prev_px = prev_py = None
        step = 2  # every 2nd pixel, line segments fill the gap

        for i in range(0, n, step):
            y_val = samples[i]
            # Values beyond the robust viewport belong to an off-screen
            # branch.  Breaking here also prevents false vertical asymptotes.
            if (y_val is not None and y_range > 0
                    and self._y_min <= y_val <= self._y_max):
                ratio = (y_val - self._y_min) / y_range
                py = graph_h - 1 - int(ratio * (graph_h - 1))
                py = max(0, min(graph_h - 1, py))
                bx = i  # buffer-local x
                self._curve_fb.pixel(bx, py, 1)
                # Large vertical jumps are usually asymptotes.  Leave a gap
                # instead of drawing a misleading full-height spike.
                if prev_px is not None and abs(py - prev_py) <= graph_h * 3 // 4:
                    self._curve_fb.line(prev_px, prev_py, bx, py, 1)
                prev_px, prev_py = bx, py
            else:
                prev_px = prev_py = None

    # ── drawing ─────────────────────────────────────────────────

    def _draw_graph(self, display):
        graph_w = self.width - GRAPH_PAD_X * 2
        graph_left = GRAPH_PAD_X
        graph_right = self.width - GRAPH_PAD_X
        graph_top = 0
        graph_h = self.height - HINT_H
        graph_bot = self.height - HINT_H

        # Border
        display.draw_rectangle(graph_left - 1, graph_top,
                               graph_right - graph_left + 2, graph_h, 8)

        # Axes
        y_range = self._y_max - self._y_min
        x_range = self.x_max - self.x_min
        x_zero = y_zero = None

        if y_range > 0 and self._y_min <= 0 <= self._y_max:
            ratio = (0 - self._y_min) / y_range
            y_zero = graph_bot - int(ratio * graph_h)
            if graph_top <= y_zero <= graph_bot:
                display.draw_hline(graph_left, y_zero, graph_w + 1, 6)

        if x_range > 0 and self.x_min <= 0 <= self.x_max:
            ratio = (0 - self.x_min) / x_range
            x_zero = graph_left + int(ratio * graph_w)
            if graph_left <= x_zero <= graph_right:
                display.draw_vline(x_zero, graph_top, graph_h + 1, 6)

        # Origin crosshair
        if x_zero is not None and y_zero is not None:
            for dx in (-2, 2):
                display.draw_pixel(x_zero + dx, y_zero, 12)
            for dy in (-2, 2):
                display.draw_pixel(x_zero, y_zero + dy, 12)

        # Blit pre-rendered curve (MONO → GS4 via palette)
        if self._curve_fb is not None and y_range > 0:
            display.palette.bg(0)
            display.palette.fg(15)
            display.gs4_fb.blit(self._curve_fb, graph_left, graph_top,
                                0, display.palette)  # key=0: black pixels transparent

    def _draw_overlay(self, display):
        oy = self._overlay_y
        if oy <= -OVERLAY_H:
            return
        display.fill_rectangle(0, oy, self.width, OVERLAY_H, 0)
        self.input_box.y = oy + 1
        self.input_box.cursor.y = oy + 2
        self.input_box.draw(display)
        display.draw_hline(0, oy + OVERLAY_H - 1, self.width, 10)

    def _draw_hint(self, display):
        if self.mode == 0:
            hint = f"x:{self.x_min:.2g}~{self.x_max:.2g} y:{self._y_min:.2g}~{self._y_max:.2g}"
            hint2 = "8/2 zoom 4/6 pan"
        else:
            hint = "ENT plot  ESC cancel"
            hint2 = "RPN x"
        draw_footer(display, hint, self.small_font, hint2)

    def draw(self, display):
        if self.mode == 2:
            if self.error_popup.expired():
                self.mode = 0
                self.error_popup.dismiss()
            else:
                self.error_popup.draw(display)
                return
        self._draw_graph(display)
        self._draw_overlay(display)
        self._draw_hint(display)

    # ── input ───────────────────────────────────────────────────

    def update(self, kb, event=None):
        if self.mode == 2:
            if self.error_popup.expired():
                self.mode = 0
            elif event is not None:
                self.mode = 0
            if self.mode == 0:
                self.error_popup.dismiss()
            return None

        if kb.consume_long_press(0, 0, 1000):
            return "BACK"

        if self.mode == 0:
            if event is not None:
                r, c, _ = event
                shift = kb.is_pressed(4, 0)
                label = get_key_label(r, c, shift)

                if r == 4 and c == 0:
                    pass
                elif r == 0 and c == 0:
                    return "BACK"
                elif r == 1 and c == 1:
                    self._zoom_y(0.5) if not shift else self._zoom_x(0.5)
                elif r == 3 and c == 1:
                    self._zoom_y(2.0) if not shift else self._zoom_x(2.0)
                elif r == 2 and c == 0:
                    self._pan_x(-0.25)
                elif r == 2 and c == 2:
                    self._pan_x(0.25)
                elif r == 3 and c == 3:
                    self._enter_edit()
                elif r == 3 and c == 5 and shift:
                    pass
                elif r == 3 and c == 5:
                    self._enter_edit("x")
                # All other keys ignored in view mode
                return None

        else:
            action = self.input_box.update(kb, event)
            if action == "ENT":
                self._leave_edit(plot=True)
            elif action == "rpn":
                self.input_box.insert_str("x")
            elif action == "ESC":
                self._leave_edit(plot=False)
            elif action == "stab":
                self.x_min = -10.0
                self.x_max = 10.0
            elif action == "DELETE":
                return "REDRAW"

        return None
