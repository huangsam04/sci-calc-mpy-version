"""Plot screen — full-screen graph with slide-in expression editor."""
import time
from framebuf import FrameBuffer, MONO_HMSB  # type: ignore
from ui.element import UIElement
from ui.inputbox import InputBox
from calc.parser import compile_expression, evaluate_program, ParseError
from calc.functions import EvalContext
from calc.number import Number, coerce
from anim.engine import insert_animation
from input.keyboard import get_key_label
from ui.theme import draw_footer
from ui.error_popup import ErrorPopup
from ui.motion import PANEL_SLIDE_MS, MOTION_EASING
from ui.residency import SETTLE_MORE, SETTLE_REDRAW


# Layout constants
OVERLAY_H = 14
HINT_H = 10
GRAPH_PAD_X = 2
ROBUST_SAMPLE_LIMIT = 24
# The curve renderer joins adjacent points, so evaluating every second pixel
# preserves the visible horizontal resolution while halving evaluator work.
CURVE_SAMPLE_STEP = 2
CURVE_WORK_SLICE = 24
CURVE_CLEAR_SLICE = 256
CURVE_WORK_BUDGET_US = 8000


class PlotScreen(UIElement):
    swap_key = "plot"
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
        self._program_cache = {}
        self._program_cache_order = []
        self._program_cache_revision = None
        self._eval_vars = {"x": 0.0}
        self._eval_context = EvalContext(self._eval_vars, registry)
        self._needs_curve_restore = False
        self._curve_restore_auto_scale = False
        self._curve_reveal = self.width
        self._curve_job = None

    def activate(self):
        self.mode = 0
        self._overlay_y = -OVERLAY_H
        self.input_box.activate()
        if not self.input_box.get_str() and self.expr:
            self.input_box.set_str(self.expr)
        if self.expr and self._curve_fb is None:
            self._render_curve()

    def animation_children(self):
        return (self.input_box, self.error_popup)

    def release_memory(self):
        """Drop graph/cache objects before Nav returns its workspace to RAM."""
        released = bool(self._curve_fb or self._program
                        or self._program_cache or self._program_cache_order)
        self._curve_fb = None
        self._curve_buf = None
        self._program = None
        self._program_cache.clear()
        self._program_cache_order = []
        self._program_cache_revision = None
        self._curve_job = None
        self.error_popup.dismiss()
        self.input_box.release_memory()
        return released

    def snapshot_state(self):
        return {
            "expr": self.expr,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "y_min": self._y_min,
            "y_max": self._y_max,
            "mode": self.mode if self.mode in (0, 1) else 0,
            "input": self.input_box.get_str(),
            "input_cursor": self.input_box.cursor_pos,
        }

    def reset_state(self):
        self.expr = ""
        self.x_min = -10.0
        self.x_max = 10.0
        self._y_min = -5.0
        self._y_max = 5.0
        self.mode = 0
        self._overlay_y = -OVERLAY_H
        self._edit_original = ""
        self.input_box.str = ""
        self.input_box.cursor_pos = 0
        self.input_box.view_offset = 0
        self.input_box._layout_dirty = True
        self.input_box.cursor.is_visible = False
        self._needs_curve_restore = False
        self._curve_restore_auto_scale = False
        self._curve_reveal = 0
        self._curve_job = None
        self.error_popup.dismiss()

    def activate_default(self):
        self.mode = 0
        self._overlay_y = -OVERLAY_H
        self.input_box.cursor.is_visible = False

    def restore_state(self, state):
        expr = state.get("expr", "")
        input_text = state.get("input", expr)
        if not isinstance(expr, str) or not isinstance(input_text, str):
            raise ValueError("Invalid plot snapshot")
        self.expr = expr
        self.x_min = float(state.get("x_min", -10.0))
        self.x_max = float(state.get("x_max", 10.0))
        self._y_min = float(state.get("y_min", -5.0))
        self._y_max = float(state.get("y_max", 5.0))
        if self.x_min >= self.x_max or self._y_min >= self._y_max:
            raise ValueError("Invalid plot viewport snapshot")
        self.mode = int(state.get("mode", 0))
        self.input_box.set_str(input_text)
        self.input_box.cursor_pos = max(0, min(
            int(state.get("input_cursor", len(input_text))), len(input_text)))
        self._overlay_y = 0 if self.mode == 1 else -OVERLAY_H
        self.input_box.cursor.is_visible = (self.mode == 1)
        self._needs_curve_restore = bool(self.expr)
        self._curve_restore_auto_scale = False

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
        try:
            status = self._advance_curve_job()
        except MemoryError:
            return self._fail_curve_job_memory()
        if status == 0:
            return SETTLE_MORE
        if status < 0:
            return SETTLE_REDRAW
        graph_width = self.width - GRAPH_PAD_X * 2 + 1
        self._curve_reveal = 0
        insert_animation(self, '_curve_reveal', 0, graph_width,
                         PANEL_SLIDE_MS, MOTION_EASING)
        return SETTLE_REDRAW | SETTLE_MORE

    def draw_transition_default(self, display):
        graph_h = self.height - HINT_H
        display.draw_rectangle(GRAPH_PAD_X - 1, 0,
                               self.width - GRAPH_PAD_X * 2 + 2,
                               graph_h, 8)
        display.draw_hline(GRAPH_PAD_X, graph_h // 2,
                           self.width - GRAPH_PAD_X * 2, 5)
        display.draw_vline(self.width // 2, 1, graph_h - 2, 5)
        display.draw_text8x8(3, self.height - 9, "Loading graph...", gs=8)

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
        """Reuse parsed expressions until the live function registry changes."""
        revision = getattr(self.registry, "revision", None)
        if revision != self._program_cache_revision:
            self._program_cache.clear()
            self._program_cache_order = []
            self._program_cache_revision = revision

        program = self._program_cache.get(self.expr)
        if program is None:
            program = compile_expression(self.expr, self.registry)
            if len(self._program_cache_order) >= 4:
                oldest = self._program_cache_order.pop(0)
                del self._program_cache[oldest]
            self._program_cache[self.expr] = program
            self._program_cache_order.append(self.expr)
        else:
            # Keep the active expression in the bounded LRU cache so each
            # pan/zoom continues to reuse its compiled form.
            self._program_cache_order.remove(self.expr)
            self._program_cache_order.append(self.expr)
        self._program = program

    def _eval(self, x_val):
        try:
            self._eval_vars["x"] = coerce(x_val)
            result = evaluate_program(self._program, self._eval_context)
            value = result.to_float() if isinstance(result, Number) else float(result)
            return value, True, ""
        except Exception as e:
            return 0.0, False, str(e)

    def _begin_curve_job(self, auto_scale):
        """Prepare a bounded curve job; sampling happens in later slices."""
        self._curve_job = None
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
        return True

    def _advance_curve_job(self):
        """Run one fixed work slice: 0=more, 1=done, -1=failed."""
        job = self._curve_job
        phase = job["phase"]
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
        self._curve_job = None
        self._curve_reveal = self.width
        return 1

    def _fail_curve_job_memory(self):
        self._curve_job = None
        self._curve_fb = None
        self.error_popup.show(self.expr, "Graph memory is busy")
        self.mode = 2
        return SETTLE_REDRAW

    def _render_curve(self, auto_scale=True):
        """Render once, reclaiming inactive caches before one retry on pressure."""
        if (self.memory is not None
                and self.memory.get_buffer("plot_curve") is None):
            self.memory.reserve_plot_workspace(self.height)
        try:
            rendered = self._render_curve_once(auto_scale)
            if rendered:
                self._curve_reveal = self.width
            return rendered
        except MemoryError:
            if self.memory is not None and self.memory.reclaim_for(
                    self, aggressive=True):
                try:
                    rendered = self._render_curve_once(auto_scale)
                    if rendered:
                        self._curve_reveal = self.width
                    return rendered
                except MemoryError:
                    pass
            self._curve_fb = None
            self.error_popup.show(self.expr, "Graph memory is busy")
            self.mode = 2
            return False

    def _render_curve_once(self, auto_scale=True):
        """Compile and render without retaining a full screen of sample values."""
        if not self.expr.strip():
            self._curve_fb = None
            return False

        try:
            self._compile_program()
        except ParseError as error:
            self._curve_fb = None
            self.error_popup.show(self.expr, error, error.pos)
            self.mode = 2
            return False

        graph_w = self.width - GRAPH_PAD_X * 2
        graph_left = GRAPH_PAD_X
        graph_right = self.width - GRAPH_PAD_X
        graph_h = self.height - HINT_H
        n = graph_right - graph_left + 1

        if auto_scale:
            # The old implementation retained one Python value per horizontal
            # pixel, then sorted a second list.  That late 1 KiB+ allocation
            # is exactly what fragments a full device heap.  Keep only a
            # bounded evenly-spaced sample for outlier detection, then draw
            # from a second evaluation pass below.
            valid_count = 0
            y_min = y_max = 0.0
            robust_values = []
            sample_stride = max(1, n // ROBUST_SAMPLE_LIMIT)
            first_err = ""

            for index in range(0, n, CURVE_SAMPLE_STEP):
                x_val = (self.x_min + index / graph_w
                         * (self.x_max - self.x_min))
                y_val, ok, err = self._eval(x_val)
                if ok and abs(y_val) < 1e6:
                    if valid_count == 0:
                        y_min = y_val
                        y_max = y_val
                    else:
                        y_min = min(y_min, y_val)
                        y_max = max(y_max, y_val)
                    valid_count += 1
                    if (index % sample_stride == 0
                            and len(robust_values) < ROBUST_SAMPLE_LIMIT):
                        robust_values.append(y_val)
                elif err and not first_err:
                    first_err = err

            if valid_count == 0:
                self._y_min = -1.0
                self._y_max = 1.0
                self._curve_fb = None
                self.error_popup.show(
                    self.expr, first_err or "Cannot evaluate expression")
                self.mode = 2
                return False

            y_range = y_max - y_min

            # Compare the full extent with a bounded central sample. Smooth
            # curves retain their true extrema, while a few samples next to a
            # pole cannot flatten everything else on screen.
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

        # ── Acquire / reuse the fixed curve workspace ──
        buf_size = ((n + 7) // 8) * graph_h  # MONO_HMSB: 1 bit per pixel
        if self.memory is not None:
            curve_buf = self.memory.get_buffer("plot_curve", buf_size)
            if curve_buf is None:
                raise MemoryError("Plot workspace was not reserved")
        else:
            curve_buf = self._curve_buf
            if curve_buf is None or len(curve_buf) < buf_size:
                curve_buf = bytearray(buf_size)

        # A pooled buffer can contain a prior graph after the page released
        # its FrameBuffer wrapper. Clear it before either reusing or wrapping.
        for i in range(buf_size):
            curve_buf[i] = 0
        if self._curve_buf is not curve_buf or self._curve_fb is None:
            self._curve_buf = curve_buf
            self._curve_fb = FrameBuffer(self._curve_buf, n, graph_h, MONO_HMSB)

        # ── Pass 2: evaluate directly into the planned mono buffer ──
        y_range = self._y_max - self._y_min
        prev_px = prev_py = None
        step = CURVE_SAMPLE_STEP  # line segments fill the skipped pixels

        for i in range(0, n, step):
            x_val = (self.x_min + i / graph_w
                     * (self.x_max - self.x_min))
            y_val, ok, _ = self._eval(x_val)
            # Values beyond the robust viewport belong to an off-screen
            # branch.  Breaking here also prevents false vertical asymptotes.
            if (ok and abs(y_val) < 1e6 and y_range > 0
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
        return True

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
