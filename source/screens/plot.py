"""Plot screen — full-screen graph with slide-in expression editor."""
import time
import math
from framebuf import FrameBuffer, MONO_HMSB  # type: ignore
from ui.element import (
    SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW, UIElement)
from ui.inputbox import InputBox
from calc.parser import compile_expression, evaluate_program, ParseError
from calc.functions import EvalContext
from calc.number import Number, coerce
from input.keyboard import get_key_label
from ui.theme import draw_footer
from ui.error_popup import ErrorPopup


# Layout constants
OVERLAY_H = 14
HINT_H = 10
GRAPH_PAD_X = 2
ROBUST_SAMPLE_LIMIT = 12
# The curve renderer joins adjacent points, so evaluating every second pixel
# preserves the visible horizontal resolution while halving evaluator work.
CURVE_SAMPLE_STEP = 2
CURVE_WORK_SLICE = 16
CURVE_CLEAR_SLICE = 256
CURVE_WORK_BUDGET_US = 4000
CURVE_GC_SLICE_INTERVAL = 6

_FLOAT_PREFIXES = (
    "sin", "cos", "tan", "asin", "acos", "atan",
    "sec", "csc", "cot", "sqrt", "ln", "exp", "log", "abs")


def _float_compatible(node):
    kind = node[0]
    if kind == "literal":
        return isinstance(node[1], (Number, int, float))
    if kind == "variable":
        return node[1] in ("x", "pi", "e")
    if kind == "unary":
        return node[1] in ("+", "-") and _float_compatible(node[2])
    if kind == "infix":
        return (node[1] in ("+", "-", "*", "/", "^")
                and _float_compatible(node[2])
                and _float_compatible(node[3]))
    if kind == "prefix":
        return (node[1] in _FLOAT_PREFIXES
                and _float_compatible(node[2]))
    if kind == "list" and node[1] in ("max", "min"):
        for child in node[2]:
            if not _float_compatible(child):
                return False
        return True
    return False


def _float_value(node, x_value, degrees):
    kind = node[0]
    if kind == "literal":
        value = node[1]
        return value.to_float() if isinstance(value, Number) else float(value)
    if kind == "variable":
        name = node[1]
        if name == "x":
            return x_value
        return math.pi if name == "pi" else math.e
    if kind == "unary":
        value = _float_value(node[2], x_value, degrees)
        return -value if node[1] == "-" else value
    if kind == "infix":
        left = _float_value(node[2], x_value, degrees)
        right = _float_value(node[3], x_value, degrees)
        operator = node[1]
        if operator == "+":
            return left + right
        if operator == "-":
            return left - right
        if operator == "*":
            return left * right
        if operator == "/":
            return left / right
        return left ** right
    if kind == "list":
        children = node[2]
        value = _float_value(children[0], x_value, degrees)
        for child in children[1:]:
            candidate = _float_value(child, x_value, degrees)
            if ((node[1] == "max" and candidate > value)
                    or (node[1] == "min" and candidate < value)):
                value = candidate
        return value

    name = node[1]
    value = _float_value(node[2], x_value, degrees)
    if name in ("sin", "cos", "tan", "sec", "csc", "cot") and degrees:
        value = value * math.pi / 180.0
    if name == "sin":
        return math.sin(value)
    if name == "cos":
        return math.cos(value)
    if name == "tan":
        return math.tan(value)
    if name == "sec":
        return 1.0 / math.cos(value)
    if name == "csc":
        return 1.0 / math.sin(value)
    if name == "cot":
        return 1.0 / math.tan(value)
    if name == "sqrt":
        return math.sqrt(value)
    if name == "ln":
        return math.log(value)
    if name == "exp":
        return math.exp(value)
    if name == "log":
        return math.log10(value)
    if name == "abs":
        return abs(value)
    if name == "asin":
        result = math.asin(value)
    elif name == "acos":
        result = math.acos(value)
    else:
        result = math.atan(value)
    return result * 180.0 / math.pi if degrees else result


class PlotScreen(UIElement):
    transition_title = "Plot"
    requires_plot_workspace = True

    def __init__(self, font, small_font=None, registry=None, memory=None):
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
        self.memory = memory
        self._program = None
        self._program_expr = None
        self._program_cache_revision = None
        self._float_program = False
        self._eval_vars = {"x": 0.0}
        self._eval_context = EvalContext(self._eval_vars, registry)
        self._needs_curve_restore = False
        self._curve_restore_auto_scale = False
        self._curve_reveal = self.width
        self._curve_job = None
        self._curve_gc_countdown = 0
        self._presented_editor_state = None

    def activate(self):
        self.mode = 0
        self._overlay_y = -OVERLAY_H
        self._presented_editor_state = None
        self.input_box.activate()
        if not self.input_box.get_str() and self.expr:
            self.input_box.set_str(self.expr)
        if self.expr and self._curve_fb is None:
            self._needs_curve_restore = True
            self._curve_restore_auto_scale = False

    def release_memory(self):
        """Drop graph/cache objects before Nav returns its workspace to RAM."""
        released = bool(self._curve_fb or self._program or self._curve_job)
        self._curve_fb = None
        self._curve_buf = None
        self._program = None
        self._program_expr = None
        self._program_cache_revision = None
        self._float_program = False
        self._curve_job = None
        self._curve_gc_countdown = 0
        self.error_popup.dismiss()
        self.input_box.release_memory()
        self._presented_editor_state = None
        return released

    def settle_step(self):
        if self._needs_curve_restore:
            self._needs_curve_restore = False
            auto_scale = self._curve_restore_auto_scale
            self._curve_restore_auto_scale = False
            try:
                if not self._begin_curve_job(auto_scale):
                    return SETTLE_REDRAW
            except MemoryError:
                return self._fail_curve_job_memory()
            return SETTLE_MORE
        if self._curve_job is None:
            return 0
        phase = self._curve_job["phase"]
        if (phase == 0 or phase == 2) and self._curve_gc_countdown <= 0:
            self._curve_gc_countdown = CURVE_GC_SLICE_INTERVAL
            return SETTLE_COLLECT | SETTLE_MORE
        try:
            status = self._advance_curve_job()
        except MemoryError:
            return self._fail_curve_job_memory()
        if ((phase == 0 or phase == 2)
                and self._curve_gc_countdown > 0):
            self._curve_gc_countdown -= 1
        if status == 0:
            return SETTLE_MORE
        if status < 0:
            return SETTLE_REDRAW
        self._curve_reveal = self.width
        return SETTLE_REDRAW

    def _editor_present_state(self):
        return (
            self.mode,
            int(self._overlay_y),
            self.input_box.y,
            self.input_box.str,
            self.input_box.cursor_pos,
            self.input_box.cursor.x,
            self.input_box.cursor.y,
            self.expr,
            self.x_min,
            self.x_max,
            self._y_min,
            self._y_max,
            self._curve_reveal,
            self._needs_curve_restore,
            self._curve_job is not None,
            self.error_popup.active,
        )

    def get_present_rows(self):
        """Restrict settled plot-editor keystrokes to their visible rows."""
        current = self._editor_present_state()
        previous = self._presented_editor_state
        if (previous is None or current[0] != 1 or previous[0] != 1
                or current[1] != 0 or previous[1] != 0):
            return None
        if current[7:] != previous[7:] or current[2:7] == previous[2:7]:
            return None
        return ((0, OVERLAY_H), (54, 10))

    def mark_presented(self):
        self._presented_editor_state = self._editor_present_state()

    # ── zoom / pan ───────────────────────────────────────────────

    def _zoom_y(self, factor):
        mid = (self._y_min + self._y_max) / 2.0
        half = (self._y_max - self._y_min) / 2.0 * factor
        self._y_min = mid - half
        self._y_max = mid + half
        if self.expr:
            self._curve_fb = None
            self._curve_buf = None
            self._needs_curve_restore = True
            self._curve_restore_auto_scale = False

    def _zoom_x(self, factor):
        mid = (self.x_min + self.x_max) / 2.0
        half = (self.x_max - self.x_min) / 2.0 * factor
        self.x_min = mid - half
        self.x_max = mid + half
        if self.expr:
            self._curve_fb = None
            self._curve_buf = None
            self._needs_curve_restore = True
            self._curve_restore_auto_scale = True

    def _pan_x(self, fraction):
        shift = (self.x_max - self.x_min) * fraction
        self.x_min += shift
        self.x_max += shift
        if self.expr:
            self._curve_fb = None
            self._curve_buf = None
            self._needs_curve_restore = True
            self._curve_restore_auto_scale = True

    # ── mode switching ──────────────────────────────────────────

    def _enter_edit(self, prefill=""):
        self._edit_original = self.expr
        if prefill:
            self.input_box.insert_str(prefill)
        self.mode = 1
        self._overlay_y = 0
        self.input_box.cursor.is_visible = True

    def _leave_edit(self, plot=True):
        self.mode = 0
        self._overlay_y = -OVERLAY_H
        self.input_box.cursor.is_visible = False
        if plot:
            self.expr = self.input_box.get_str().strip()
            if self.expr:
                self._curve_fb = None
                self._curve_buf = None
                self._needs_curve_restore = True
                self._curve_restore_auto_scale = True
            else:
                self._curve_fb = None
                self._curve_buf = None
                self._needs_curve_restore = False
                self._curve_restore_auto_scale = False
        else:
            self.input_box.set_str(self._edit_original)

    # ── curve rendering (2-pass: find range → draw to buffer) ────

    def _compile_program(self):
        """Reuse only the active expression's program."""
        revision = getattr(self.registry, "revision", None)
        if (self._program is None
                or self._program_expr != self.expr
                or self._program_cache_revision != revision):
            self._program = compile_expression(self.expr, self.registry)
            self._program_expr = self.expr
            self._program_cache_revision = revision
            self._float_program = _float_compatible(self._program)

    def _eval(self, x_val):
        try:
            if self._float_program:
                return (_float_value(
                    self._program, x_val,
                    bool(getattr(self.registry, "angle_mode", 0))),
                        True, "")
            self._eval_vars["x"] = coerce(x_val)
            result = evaluate_program(self._program, self._eval_context)
            value = result.to_float() if isinstance(result, Number) else float(result)
            return value, True, ""
        except Exception as e:
            return 0.0, False, str(e)

    def _begin_curve_job(self, auto_scale):
        """Prepare a bounded curve job; sampling happens in later slices."""
        self._curve_job = None
        self._curve_gc_countdown = 0
        if not self.expr.strip():
            self._curve_fb = None
            return False
        if (self.memory is not None
                and self.memory.get_buffer("plot_curve") is None):
            self.memory.reserve_plot_workspace(self.height)
        try:
            self._compile_program()
        except ParseError as error:
            self._curve_fb = None
            self.error_popup.show(self.expr, error, error.pos)
            self.mode = 2
            return False

        graph_w = self.width - GRAPH_PAD_X * 2
        graph_right = self.width - GRAPH_PAD_X
        n = graph_right - GRAPH_PAD_X + 1
        self._curve_job = {
            "phase": 0 if auto_scale else 1,
            "graph_w": graph_w,
            "graph_h": self.height - HINT_H,
            "n": n,
            "index": 0,
            "valid": 0,
            "y_min": 0.0,
            "y_max": 0.0,
            "robust": [],
            "stride": max(1, n // ROBUST_SAMPLE_LIMIT),
            "first_err": "",
            "clear": 0,
            "prev_x": None,
            "prev_y": None,
        }
        self._curve_gc_countdown = CURVE_GC_SLICE_INTERVAL
        return True

    def _advance_curve_job(self):
        """Run one fixed work slice: 0=more, 1=done, -1=failed."""
        job = self._curve_job
        phase = job["phase"]
        if phase == 3:
            # Keep the final sampling slice and the OLED transfer in separate
            # loop iterations.  Either operation fits the input deadline on
            # its own, while combining them can block for more than 32 ms.
            self._curve_job = None
            return 1
        if phase == 0:
            processed = 0
            index = job["index"]
            slice_started = time.ticks_us()
            while index < job["n"] and processed < CURVE_WORK_SLICE:
                x_val = (self.x_min + index / job["graph_w"]
                         * (self.x_max - self.x_min))
                y_val, ok, err = self._eval(x_val)
                if ok and abs(y_val) < 1e6:
                    if job["valid"] == 0:
                        job["y_min"] = y_val
                        job["y_max"] = y_val
                    else:
                        job["y_min"] = min(job["y_min"], y_val)
                        job["y_max"] = max(job["y_max"], y_val)
                    job["valid"] += 1
                    if (index % job["stride"] == 0
                            and len(job["robust"]) < ROBUST_SAMPLE_LIMIT):
                        job["robust"].append(y_val)
                elif err and not job["first_err"]:
                    job["first_err"] = err
                index += CURVE_SAMPLE_STEP
                processed += 1
                if (time.ticks_diff(time.ticks_us(), slice_started)
                        >= CURVE_WORK_BUDGET_US):
                    break
            job["index"] = index
            if index < job["n"]:
                return 0
            if job["valid"] == 0:
                self._y_min = -1.0
                self._y_max = 1.0
                self._curve_fb = None
                self.error_popup.show(
                    self.expr, job["first_err"] or "Cannot evaluate expression")
                self.mode = 2
                self._curve_job = None
                return -1

            y_min = job["y_min"]
            y_max = job["y_max"]
            y_range = y_max - y_min
            robust_values = job["robust"]
            if len(robust_values) > 2:
                robust_values.sort()
                trim = max(1, len(robust_values) // 10)
                robust_min = robust_values[trim]
                robust_max = robust_values[-trim - 1]
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
            job["phase"] = 1
            job["index"] = 0
            # The robust list has served its purpose. Drop it before acquiring
            # the curve buffer so both allocations do not overlap.
            job["robust"] = None
            return 0

        if phase == 1:
            if "buf_size" not in job:
                buf_size = ((job["n"] + 7) // 8) * job["graph_h"]
                if self.memory is not None:
                    curve_buf = self.memory.get_buffer("plot_curve", buf_size)
                    if curve_buf is None:
                        raise MemoryError("Plot workspace was not reserved")
                else:
                    curve_buf = self._curve_buf
                    if curve_buf is None or len(curve_buf) < buf_size:
                        curve_buf = bytearray(buf_size)
                job["buf_size"] = buf_size
                job["curve_buf"] = curve_buf
            start = job["clear"]
            end = min(job["buf_size"], start + CURVE_CLEAR_SLICE)
            curve_buf = job["curve_buf"]
            for index in range(start, end):
                curve_buf[index] = 0
            job["clear"] = end
            if end < job["buf_size"]:
                return 0
            self._curve_buf = curve_buf
            self._curve_fb = FrameBuffer(
                curve_buf, job["n"], job["graph_h"], MONO_HMSB)
            job["phase"] = 2
            job["index"] = 0
            return 0

        processed = 0
        index = job["index"]
        slice_started = time.ticks_us()
        y_range = self._y_max - self._y_min
        prev_px = job["prev_x"]
        prev_py = job["prev_y"]
        while index < job["n"] and processed < CURVE_WORK_SLICE:
            x_val = (self.x_min + index / job["graph_w"]
                     * (self.x_max - self.x_min))
            y_val, ok, _ = self._eval(x_val)
            if (ok and abs(y_val) < 1e6 and y_range > 0
                    and self._y_min <= y_val <= self._y_max):
                ratio = (y_val - self._y_min) / y_range
                py = job["graph_h"] - 1 - int(
                    ratio * (job["graph_h"] - 1))
                py = max(0, min(job["graph_h"] - 1, py))
                self._curve_fb.pixel(index, py, 1)
                if (prev_px is not None
                        and abs(py - prev_py) <= job["graph_h"] * 3 // 4):
                    self._curve_fb.line(prev_px, prev_py, index, py, 1)
                prev_px, prev_py = index, py
            else:
                prev_px = prev_py = None
            index += CURVE_SAMPLE_STEP
            processed += 1
            if (time.ticks_diff(time.ticks_us(), slice_started)
                    >= CURVE_WORK_BUDGET_US):
                break
        job["index"] = index
        job["prev_x"] = prev_px
        job["prev_y"] = prev_py
        if index < job["n"]:
            return 0
        job["phase"] = 3
        self._curve_reveal = self.width
        return 0

    def _fail_curve_job_memory(self):
        self._curve_job = None
        self._curve_gc_countdown = 0
        self._curve_fb = None
        self.error_popup.show(self.expr, "Graph memory is busy")
        self.mode = 2
        return SETTLE_REDRAW

    # ── drawing ─────────────────────────────────────────────────

    def _draw_graph(self, display):
        graph_w = self.width - GRAPH_PAD_X * 2
        graph_left = GRAPH_PAD_X
        graph_right = self.width - GRAPH_PAD_X
        graph_top = 0
        graph_h = self.height - HINT_H
        graph_bot = self.height - HINT_H

        # Blit pre-rendered curve first, then mask its unrevealed tail. Axes
        # and the border are redrawn afterward so they remain stable while the
        # restored curve appears from left to right.
        y_range = self._y_max - self._y_min
        if self._curve_fb is not None and y_range > 0:
            display.palette.bg(0)
            display.palette.fg(15)
            display.gs4_fb.blit(self._curve_fb, graph_left, graph_top,
                                0, display.palette)
            curve_width = graph_right - graph_left + 1
            reveal = max(0, min(curve_width, int(self._curve_reveal)))
            if reveal < curve_width:
                display.fill_rectangle(graph_left + reveal, graph_top,
                                       curve_width - reveal, graph_h, 0)

        # Border
        display.draw_rectangle(graph_left - 1, graph_top,
                               graph_right - graph_left + 2, graph_h, 8)

        # Axes
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

    def draw_present_rows(self, display):
        """Redraw the settled editor overlay without repainting the graph."""
        self._draw_overlay(display)
        self._draw_hint(display)

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
