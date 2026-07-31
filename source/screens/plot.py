'Plot screen — full-screen graph with a bounded expression editor.'
import gc
import time
import math
from ui.element import (
    SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW, UIElement)
from ui.inputbox import InputBox
from calc.parser import compile_expression, evaluate_program, ParseError
from calc.functions import EvalContext
from calc.number import Number, coerce
from input.keyboard import get_key_label
from ui.motion import DAMAGE_FULL, DAMAGE_NONE, DAMAGE_PARTIAL
from ui.theme import draw_footer_cached, fit_text, text_width
from ui.error_popup import ErrorPopup


# Layout constants
OVERLAY_H = 14
HINT_H = 10
GRAPH_PAD_X = 2
PLOT_PROGRESS = (42, 31, 126, 7)
ROBUST_SAMPLE_LIMIT = 12
# The curve renderer joins adjacent points, so evaluating every second pixel
# preserves the visible horizontal resolution while halving evaluator work.
CURVE_SAMPLE_STEP = 2
CURVE_WORK_SLICE = 16
CURVE_CLEAR_SLICE = 256
CURVE_WORK_BUDGET_US = 4000
CURVE_GC_SLICE_INTERVAL = 6
CURVE_INVALID_Y = 255
_CURVE_MEMORY_FAILED = -2
_MEMORY_ERROR_TITLE = 'Graph paused'
_MEMORY_ERROR_DETAIL = 'Low memory: graph stopped'
_VIEW_HINT_RIGHT = '8/2 zoom 4/6 pan'
_EDIT_HINT = 'ENT plot  ESC cancel'
_EDIT_HINT_RIGHT = 'RPN x'

MAX_PLOT_EXPRESSION_CHARS = 42

_FLOAT_PREFIXES = (
    'sin', 'cos', 'tan', 'asin', 'acos', 'atan',
    'sec', 'csc', 'cot', 'sqrt', 'ln', 'exp', 'log', 'abs')


class _CurveJob:
    "Fixed-shape state for one bounded Plot build.\n\n    This stays private to ``PlotScreen``: callers only observe whether a job\n    exists and the screen's settle result.  The slot layout prevents every\n    sampling pass from retaining a hash table and fifteen string keys.\n    "
    __slots__ = (
        'phase', 'graph_w', 'graph_h', 'n', 'index', 'valid',
        'y_min', 'y_max', 'robust', 'stride', 'first_error', 'clear',
        'buf_size', 'curve_buf')

    def __init__(self, auto_scale, graph_w, graph_h, sample_count):
        # Allocate the only small sample table while the boot heap is still
        # contiguous.  Plot jobs reset this object in place thereafter.
        self.robust = [0.0] * ROBUST_SAMPLE_LIMIT
        self.reset(auto_scale, graph_w, graph_h, sample_count)

    def reset(self, auto_scale, graph_w, graph_h, sample_count):
        self.phase = 0 if auto_scale else 1
        self.graph_w = graph_w
        self.graph_h = graph_h
        self.n = sample_count
        self.index = 0
        self.valid = 0
        self.y_min = 0.0
        self.y_max = 0.0
        self.stride = max(1, sample_count // ROBUST_SAMPLE_LIMIT)
        self.first_error = None
        self.clear = 0
        self.buf_size = 0
        self.curve_buf = None


PlotScenarioTransaction = None


def _float_compatible(node):
    kind = node[0]
    if kind == 'literal':
        return isinstance(node[1], (Number, int, float))
    if kind == 'variable':
        return node[1] in ('x', 'pi', 'e')
    if kind == 'unary':
        return node[1] in ('+', '-') and _float_compatible(node[2])
    if kind == 'infix':
        return (node[1] in ('+', '-', '*', '/', '^')
                and _float_compatible(node[2])
                and _float_compatible(node[3]))
    if kind == 'prefix':
        return (node[1] in _FLOAT_PREFIXES
                and _float_compatible(node[2]))
    if kind == 'list' and node[1] in ('max', 'min'):
        for child in node[2]:
            if not _float_compatible(child):
                return False
        return True
    return False


def _float_value(node, x_value, degrees):
    kind = node[0]
    if kind == 'literal':
        value = node[1]
        return value.to_float() if isinstance(value, Number) else float(value)
    if kind == 'variable':
        name = node[1]
        if name == 'x':
            return x_value
        return math.pi if name == 'pi' else math.e
    if kind == 'unary':
        value = _float_value(node[2], x_value, degrees)
        return -value if node[1] == '-' else value
    if kind == 'infix':
        left = _float_value(node[2], x_value, degrees)
        right = _float_value(node[3], x_value, degrees)
        operator = node[1]
        if operator == '+':
            return left + right
        if operator == '-':
            return left - right
        if operator == '*':
            return left * right
        if operator == '/':
            return left / right
        return left ** right
    if kind == 'list':
        children = node[2]
        value = _float_value(children[0], x_value, degrees)
        for child in children[1:]:
            candidate = _float_value(child, x_value, degrees)
            if ((node[1] == 'max' and candidate > value)
                    or (node[1] == 'min' and candidate < value)):
                value = candidate
        return value

    name = node[1]
    value = _float_value(node[2], x_value, degrees)
    if name in ('sin', 'cos', 'tan', 'sec', 'csc', 'cot') and degrees:
        value = value * math.pi / 180.0
    if name == 'sin':
        return math.sin(value)
    if name == 'cos':
        return math.cos(value)
    if name == 'tan':
        return math.tan(value)
    if name == 'sec':
        return 1.0 / math.cos(value)
    if name == 'csc':
        return 1.0 / math.sin(value)
    if name == 'cot':
        return 1.0 / math.tan(value)
    if name == 'sqrt':
        return math.sqrt(value)
    if name == 'ln':
        return math.log(value)
    if name == 'exp':
        return math.exp(value)
    if name == 'log':
        return math.log10(value)
    if name == 'abs':
        return abs(value)
    if name == 'asin':
        result = math.asin(value)
    elif name == 'acos':
        result = math.acos(value)
    else:
        result = math.atan(value)
    return result * 180.0 / math.pi if degrees else result


class PlotScreen(UIElement):
    transition_title = 'Plot'
    requires_plot_workspace = True
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ('input_box', 'error_popup', 'expr', '_state')

    def __init__(self, font, small_font=None, registry=None, memory=None,
                 retained_state=None):
        # Construct child instance blocks before the four-key Plot map. Fixed
        # tables of at most four references replace the former 34-key
        # MicroPython object without one large contiguous allocation.
        small_font = small_font or font
        if retained_state is None:
            input_box = InputBox(
                0, 1, 210, 12, MAX_PLOT_EXPRESSION_CHARS, font)
            bounds = [-10.0, 10.0, -5.0, 5.0]
            expression = ''
            curve = [None, None, False, False]
        else:
            input_box = retained_state[0]
            expression = retained_state[1]
            bounds = retained_state[2]
            curve = retained_state
        eval_vars = {'x': 0.0}
        eval_context = EvalContext(eval_vars, registry)
        error_popup = ErrorPopup(font, small_font)

        self.input_box = input_box
        self.error_popup = error_popup
        self.expr = expression
        self._state = None
        # bounds: x minimum/maximum, then y minimum/maximum
        # presented: frame/footer/lease/mode; curve: pixels/restore flags
        # runtime: reveal/job/GC countdown, then view/program/evaluator tables
        self._state = (
            bounds,
            [None, None, None, 0],
            curve,
            [self.width, None, 0, (
                [font, small_font, '', None],
                [registry, memory, None, None],
                [None, False, eval_vars, eval_context],
                _CurveJob(False, 0, 0, 0),
            )],
        )
        if retained_state is not None:
            curve[0] = None
            curve[1] = None
            curve[2] = False
            curve[3] = False
        # The editor only ever draws at its visible resting coordinate. The
        # old off-screen negative-y staging made direct packed text unsafe.
        self._clear_presented_editor_state()

    def activate(self):
        self._activate_visible_state()

    def _activate_visible_state(self):
        'Apply the small visible state change used by normal activation.'
        self._state[1][3] = 0
        self._state[3][3][0][3] = None
        self._clear_presented_editor_state()
        self.input_box.activate()
        if not self.input_box.get_str() and self.expr:
            self.input_box.set_str(self.expr)
        if self.expr and self._state[2][0] is None:
            self._state[2][2] = True
            self._state[2][3] = False

    def open_scenario_transaction(self):
        'Open one no-copy checkpoint for a future bounded Plot exercise.'
        if self._state[1][2] is not None:
            raise RuntimeError('Plot scenario transaction is already active')
        transaction_type = PlotScenarioTransaction
        if transaction_type is None:
            from screens.plot_scenario import (
                PlotScenarioTransaction as transaction_type)
        return transaction_type(self)

    def blocks_global_shortcuts(self):
        'An active plot error popup owns every edge until dismissal.'
        return self._state[1][3] == 2

    def letter_input_target(self):
        'Only the visible plot editor may receive alphabet input.'
        return self.input_box if self._state[1][3] == 1 else None

    def _clear_program(self):
        'Forget the compiled AST and every cache tag that describes it.'
        self._state[3][3][1][2] = None
        self._state[3][3][1][3] = None
        self._state[3][3][2][0] = None
        self._state[3][3][2][1] = False

    def _discard_curve_pixels(self):
        'Cancel a running raster job without dropping reusable program/workspace.'
        job = self._state[3][1]
        released = bool(self._state[2][0] or self._state[2][1] or job)
        if job is not None:
            job.curve_buf = None
            job.first_error = None
        self._state[3][1] = None
        self._state[3][2] = 0
        self._state[2][0] = None
        self._state[2][1] = None
        self._state[3][0] = self.width
        return released

    def _discard_curve_runtime(self, release_workspace=False, collect=False):
        'Drop every derived Plot object without allocating an error surface.'
        released = self._discard_curve_pixels()
        released = bool(self._state[3][3][1][2]) or released
        self._clear_program()
        if self._state[3][3][1][1] is not None and release_workspace:
            released = self._state[3][3][1][1].release_plot_workspace() or released
        if collect:
            gc.collect()
        return released

    def release_memory(self):
        'Drop graph/cache objects before Nav returns its workspace to RAM.'
        released = self._discard_curve_runtime()
        released = self.error_popup.release_memory() or released
        released = self.input_box.release_memory() or released
        if self._state[1][0] is not None:
            self._state[1][0] = None
            released = True
        if self._state[1][1] is not None:
            self._state[1][1] = None
            released = True
        return released

    def detach_state(self):
        'Move expression/view state out and discard Plot-only runtime.'
        state = self._state
        if state[1][2] is not None:
            raise RuntimeError('Plot scenario transaction is active')
        self.release_memory()
        retained = state[2]
        retained[0] = self.input_box
        retained[1] = self.expr
        retained[2] = state[0]
        retained[3] = None
        self.input_box = None
        self.error_popup = None
        self.expr = ''
        self._state = None
        return retained

    def on_angle_mode_changed(self):
        'Atomically discard every curve derived under the old angle mode.\n\n        Angle conversion is read while sampling, so retaining either a running\n        job or a completed buffer would mix RAD and DEG pixels.  This method\n        only drops references and marks the existing bounded pipeline for a\n        later rebuild; it does not allocate, collect, or synchronously sample\n        on the key-event path.\n        '
        released = self._discard_curve_runtime(release_workspace=True)
        self._state[3][0] = self.width
        self.error_popup.release_memory()
        if self._state[1][3] == 2:
            self._state[1][3] = 0
        self._state[2][2] = bool(self.expr) and self._state[1][3] != 1
        self._state[2][3] = self._state[2][2]
        self._clear_presented_editor_state()
        return released

    def settle_step(self):
        'Run ordinary Plot maintenance, converting OOM into the local UI.'
        return self._settle_curve_step()

    def _settle_curve_step(self, propagate_memory=False):
        'Advance one quiet Plot phase, optionally preserving the primary OOM.'
        if self._state[1][3] == 2 and self.error_popup.expired():
            self._state[1][3] = 0
            self.error_popup.dismiss()
            return SETTLE_REDRAW
        if self._state[2][2]:
            self._state[2][2] = False
            auto_scale = self._state[2][3]
            self._state[2][3] = False
            if propagate_memory:
                started = self._begin_curve_job(auto_scale)
                if started == _CURVE_MEMORY_FAILED:
                    return self._fail_curve_job_memory()
                if not started:
                    return SETTLE_REDRAW
            else:
                try:
                    started = self._begin_curve_job(auto_scale)
                    if started == _CURVE_MEMORY_FAILED:
                        return self._fail_curve_job_memory()
                    if not started:
                        return SETTLE_REDRAW
                except MemoryError:
                    return self._fail_curve_job_memory()
            return SETTLE_MORE
        if self._state[3][1] is None:
            return 0
        phase = self._state[3][1].phase
        if (phase == 0 or phase == 2) and self._state[3][2] <= 0:
            self._state[3][2] = CURVE_GC_SLICE_INTERVAL
            return SETTLE_COLLECT | SETTLE_MORE
        if propagate_memory:
            status = self._advance_curve_job()
        else:
            try:
                status = self._advance_curve_job()
            except MemoryError:
                return self._fail_curve_job_memory()
        if status == _CURVE_MEMORY_FAILED:
            return self._fail_curve_job_memory()
        if ((phase == 0 or phase == 2)
                and self._state[3][2] > 0):
            self._state[3][2] -= 1
        if status == 0:
            if self._state[3][0] < 0:
                job = self._state[3][1]
                samples = (job.n + 1) // CURVE_SAMPLE_STEP
                if job.phase == 0:
                    done = job.index // CURVE_SAMPLE_STEP
                elif job.phase == 1:
                    done = samples + job.clear
                elif job.phase == 2:
                    done = samples * 2 + job.index // CURVE_SAMPLE_STEP
                else:
                    done = samples * 3
                fill = done * (PLOT_PROGRESS[2] - 2) // (samples * 3)
                encoded = -1 - fill
                if encoded != self._state[3][0]:
                    self._state[3][0] = encoded
                    return SETTLE_REDRAW | SETTLE_MORE
            return SETTLE_MORE
        if status < 0:
            return SETTLE_REDRAW
        self._state[3][0] = self.width
        return SETTLE_REDRAW

    def _clear_presented_editor_state(self):
        # Mode is the validity sentinel; stale values in the other fixed
        # slots are never observed while it is invalid.
        presented = self._state[1][0]
        if presented is not None:
            presented[0] = None

    def _editor_damage_state(self):
        'Classify a settled editor frame without a transient 16-item tuple.'
        presented = self._state[1][0]
        if (presented is None or self._state[1][3] != 1 or presented[0] != 1
                or self._state[3][3][0][3] != 0 or presented[1] != 0):
            return DAMAGE_FULL
        if (self.expr != presented[7]
                or self._state[0][0] != presented[8]
                or self._state[0][1] != presented[9]
                or self._state[0][2] != presented[10]
                or self._state[0][3] != presented[11]
                or self._state[3][0] != presented[12]
                or self._state[2][2] != presented[13]
                or (self._state[3][1] is not None) != presented[14]
                or self.error_popup.active != presented[15]):
            return DAMAGE_FULL
        if (self.input_box.y == presented[2]
                and self.input_box.str == presented[3]
                and self.input_box.cursor_pos == presented[4]
                and self.input_box.cursor.x == presented[5]
                and self.input_box.cursor.y == presented[6]):
            return DAMAGE_NONE
        return DAMAGE_PARTIAL

    def collect_present_damage(self, damage):
        presented = self._state[1][0]
        if (self._state[3][0] < 0 and presented is not None
                and presented[0] == 0 and presented[12] < 0):
            if self._state[3][0] == presented[12]:
                return DAMAGE_NONE
            damage.add(PLOT_PROGRESS[1], PLOT_PROGRESS[3])
            return DAMAGE_PARTIAL
        state = self._editor_damage_state()
        if state == DAMAGE_PARTIAL:
            damage.add(0, OVERLAY_H)
            damage.add(54, 10)
        return state

    def mark_presented(self):
        presented = self._state[1][0]
        if presented is None:
            presented = [None] * 16
            self._state[1][0] = presented
        presented[0] = self._state[1][3]
        presented[1] = self._state[3][3][0][3]
        presented[2] = self.input_box.y
        presented[3] = self.input_box.str
        presented[4] = self.input_box.cursor_pos
        presented[5] = self.input_box.cursor.x
        presented[6] = self.input_box.cursor.y
        presented[7] = self.expr
        presented[8] = self._state[0][0]
        presented[9] = self._state[0][1]
        presented[10] = self._state[0][2]
        presented[11] = self._state[0][3]
        presented[12] = self._state[3][0]
        presented[13] = self._state[2][2]
        presented[14] = self._state[3][1] is not None
        presented[15] = self.error_popup.active

    # ── zoom / pan ───────────────────────────────────────────────

    def _zoom_y(self, factor):
        mid = (self._state[0][2] + self._state[0][3]) / 2.0
        half = (self._state[0][3] - self._state[0][2]) / 2.0 * factor
        self._state[0][2] = mid - half
        self._state[0][3] = mid + half
        if self.expr:
            self._discard_curve_pixels()
            self._state[2][2] = True
            self._state[2][3] = False

    def _zoom_x(self, factor):
        mid = (self._state[0][0] + self._state[0][1]) / 2.0
        half = (self._state[0][1] - self._state[0][0]) / 2.0 * factor
        self._state[0][0] = mid - half
        self._state[0][1] = mid + half
        if self.expr:
            self._discard_curve_pixels()
            self._state[2][2] = True
            self._state[2][3] = True

    def _pan_x(self, fraction):
        shift = (self._state[0][1] - self._state[0][0]) * fraction
        self._state[0][0] += shift
        self._state[0][1] += shift
        if self.expr:
            self._discard_curve_pixels()
            self._state[2][2] = True
            self._state[2][3] = True

    # ── mode switching ──────────────────────────────────────────

    def _enter_edit(self, prefill=''):
        self._state[3][3][0][2] = self.expr
        if prefill:
            self.input_box.insert_str(prefill)
        self._state[1][3] = 1
        self._state[3][3][0][3] = 0
        self.input_box.cursor.is_visible = True

    def _leave_edit(self, plot=True):
        self._state[1][3] = 0
        self._state[3][3][0][3] = None
        self.input_box.cursor.is_visible = False
        if plot:
            previous_expr = self.expr
            self.expr = self.input_box.get_str().strip()
            if self.expr:
                if self.expr != previous_expr:
                    self._discard_curve_runtime()
                else:
                    self._discard_curve_pixels()
                self._state[2][2] = True
                self._state[2][3] = True
                self._state[3][0] = -1
            else:
                self._discard_curve_runtime()
                self._state[2][2] = False
                self._state[2][3] = False
        else:
            self.input_box.set_str(self._state[3][3][0][2])

    # ── curve rendering (2-pass: find range → retain y samples) ──

    def _compile_program(self):
        "Reuse only the active expression's program."
        revision = getattr(self._state[3][3][1][0], 'revision', None)
        if (self._state[3][3][1][2] is not None
                and self._state[3][3][1][3] == self.expr
                and self._state[3][3][2][0] == revision):
            return
        # A replacement compiler can otherwise retain both full ASTs at its
        # allocation peak. This runs only from a quiet Plot settle step.
        had_program = self._state[3][3][1][2] is not None
        self._clear_program()
        if had_program:
            gc.collect()
        program = compile_expression(self.expr, self._state[3][3][1][0])
        self._state[3][3][1][2] = program
        self._state[3][3][1][3] = self.expr
        self._state[3][3][2][0] = revision
        self._state[3][3][2][1] = _float_compatible(program)

    def _eval(self, x_val):
        try:
            if self._state[3][3][2][1]:
                return (_float_value(
                    self._state[3][3][1][2], x_val,
                    bool(getattr(self._state[3][3][1][0], 'angle_mode', 0))),
                        True, '')
            self._state[3][3][2][2]['x'] = coerce(x_val)
            result = evaluate_program(self._state[3][3][1][2], self._state[3][3][2][3])
            value = result.to_float() if isinstance(result, Number) else float(result)
            return value, True, ''
        except MemoryError:
            # Plot's quiet-step recovery owns this error. Converting it to a
            # per-sample string would retain more state and keep sampling.
            raise
        except Exception as error:
            # Domain errors can occur at every sampled x. Retain/stringify at
            # most the first one, and only while autoscale might need it for a
            # user-facing failure message.
            job = self._state[3][1]
            if job is not None:
                if job.phase == 0 and job.first_error is None:
                    job.first_error = str(error)
                return 0.0, False, ''
            return 0.0, False, str(error)

    def _begin_curve_job(self, auto_scale):
        'Prepare a bounded curve job; sampling happens in later slices.'
        self._state[3][1] = None
        self._state[3][2] = 0
        if not self.expr.strip():
            self._discard_curve_runtime(release_workspace=True)
            return False
        if self._state[3][3][1][1] is not None:
            # Always ask the owner to validate the full fixed workspace before
            # compilation. An existing undersized buffer cannot slip through
            # to sampling as a late "MemoryError".
            if self._state[3][3][1][1].reserve_plot_workspace(self.height) is None:
                return _CURVE_MEMORY_FAILED
        try:
            self._compile_program()
        except ParseError as error:
            self.error_popup.release_memory()
            self._discard_curve_runtime(release_workspace=True)
            self.error_popup.show(self.expr, error, error.pos)
            self._state[1][3] = 2
            return False

        graph_w = self.width - GRAPH_PAD_X * 2
        graph_right = self.width - GRAPH_PAD_X
        n = graph_right - GRAPH_PAD_X + 1
        job = self._state[3][3][3]
        job.reset(auto_scale, graph_w, self.height - HINT_H, n)
        self._state[3][1] = job
        self._state[3][2] = CURVE_GC_SLICE_INTERVAL
        return True

    def _advance_curve_job(self):
        'Run one fixed work slice: 0=more, 1=done, -1=error, -2=memory.'
        job = self._state[3][1]
        phase = job.phase
        if phase == 3:
            # Keep the final sampling slice and the OLED transfer in separate
            # loop iterations. Either operation fits the input deadline on
            # its own, while combining them can exceed the strict 40 ms budget.
            job.curve_buf = None
            job.first_error = None
            self._state[3][1] = None
            return 1
        if phase == 0:
            processed = 0
            index = job.index
            slice_started = time.ticks_us()
            while index < job.n and processed < CURVE_WORK_SLICE:
                x_val = (self._state[0][0] + index / job.graph_w
                         * (self._state[0][1] - self._state[0][0]))
                y_val, ok, err = self._eval(x_val)
                if ok and abs(y_val) < 1e6:
                    if job.valid == 0:
                        job.y_min = y_val
                        job.y_max = y_val
                    else:
                        job.y_min = min(job.y_min, y_val)
                        job.y_max = max(job.y_max, y_val)
                    job.valid += 1
                    if (index % job.stride == 0
                            and job.clear < ROBUST_SAMPLE_LIMIT):
                        job.robust[job.clear] = y_val
                        job.clear += 1
                elif err and job.first_error is None:
                    job.first_error = err
                index += CURVE_SAMPLE_STEP
                processed += 1
                if (time.ticks_diff(time.ticks_us(), slice_started)
                        >= CURVE_WORK_BUDGET_US):
                    break
            job.index = index
            if index < job.n:
                return 0
            if job.valid == 0:
                self._state[0][2] = -1.0
                self._state[0][3] = 1.0
                error = job.first_error or 'Cannot evaluate expression'
                self.error_popup.release_memory()
                self._discard_curve_runtime(release_workspace=True, collect=True)
                self.error_popup.show(
                    self.expr, error)
                self._state[1][3] = 2
                return -1

            y_min = job.y_min
            y_max = job.y_max
            y_range = y_max - y_min
            robust_values = job.robust
            robust_count = job.clear
            if robust_count > 2:
                # Sort only the populated prefix of the preallocated table.
                robust_index = 1
                while robust_index < robust_count:
                    robust_value = robust_values[robust_index]
                    robust_cursor = robust_index
                    while (robust_cursor > 0
                           and robust_values[robust_cursor - 1]
                           > robust_value):
                        robust_values[robust_cursor] = robust_values[
                            robust_cursor - 1]
                        robust_cursor -= 1
                    robust_values[robust_cursor] = robust_value
                    robust_index += 1
                trim = max(1, robust_count // 10)
                robust_min = robust_values[trim]
                robust_max = robust_values[robust_count - trim - 1]
                robust_range = robust_max - robust_min
                if (robust_range > 1e-10
                        and y_range > robust_range * 4.0):
                    y_min = robust_min
                    y_max = robust_max
                    y_range = robust_range
            pad = max(y_range * 0.1, 0.5)
            if y_range < 1e-10:
                pad = 1.0
            self._state[0][2] = y_min - pad
            self._state[0][3] = y_max + pad
            job.phase = 1
            job.index = 0
            job.clear = 0
            return 0

        if phase == 1:
            if job.buf_size == 0:
                buf_size = ((job.n + CURVE_SAMPLE_STEP - 1)
                            // CURVE_SAMPLE_STEP)
                if self._state[3][3][1][1] is not None:
                    curve_buf = self._state[3][3][1][1].get_plot_workspace(
                        buf_size)
                    if curve_buf is None:
                        return _CURVE_MEMORY_FAILED
                else:
                    curve_buf = self._state[2][1]
                    if curve_buf is None or len(curve_buf) < buf_size:
                        curve_buf = bytearray(buf_size)
                job.buf_size = buf_size
                job.curve_buf = curve_buf
            start = job.clear
            end = min(job.buf_size, start + CURVE_CLEAR_SLICE)
            curve_buf = job.curve_buf
            index = start
            while index < end:
                curve_buf[index] = CURVE_INVALID_Y
                index += 1
            job.clear = end
            if end < job.buf_size:
                return 0
            self._state[2][1] = curve_buf
            self._state[2][0] = curve_buf
            job.phase = 2
            job.index = 0
            return 0

        processed = 0
        index = job.index
        slice_started = time.ticks_us()
        y_range = self._state[0][3] - self._state[0][2]
        curve_buf = job.curve_buf
        while index < job.n and processed < CURVE_WORK_SLICE:
            x_val = (self._state[0][0] + index / job.graph_w
                     * (self._state[0][1] - self._state[0][0]))
            y_val, ok, _ = self._eval(x_val)
            if (ok and abs(y_val) < 1e6 and y_range > 0
                    and self._state[0][2] <= y_val <= self._state[0][3]):
                ratio = (y_val - self._state[0][2]) / y_range
                py = job.graph_h - 1 - int(
                    ratio * (job.graph_h - 1))
                py = max(0, min(job.graph_h - 1, py))
                curve_buf[index // CURVE_SAMPLE_STEP] = py
            else:
                curve_buf[index // CURVE_SAMPLE_STEP] = CURVE_INVALID_Y
            index += CURVE_SAMPLE_STEP
            processed += 1
            if (time.ticks_diff(time.ticks_us(), slice_started)
                    >= CURVE_WORK_BUDGET_US):
                break
        job.index = index
        if index < job.n:
            return 0
        job.phase = 3
        return 0

    def _fail_curve_job_memory(self):
        self._state[2][2] = False
        self._state[2][3] = False
        self.error_popup.release_memory()
        self._discard_curve_runtime(release_workspace=True, collect=True)
        self.error_popup.show_static(
            _MEMORY_ERROR_TITLE, _MEMORY_ERROR_DETAIL)
        self._state[1][3] = 2
        return SETTLE_REDRAW

    # ── drawing ─────────────────────────────────────────────────

    def _draw_graph(self, display):
        graph_w = self.width - GRAPH_PAD_X * 2
        graph_left = GRAPH_PAD_X
        graph_right = self.width - GRAPH_PAD_X
        graph_top = 0
        graph_h = self.height - HINT_H
        graph_bot = self.height - HINT_H

        # Rebuild the curve from one y byte per evaluated column directly in
        # the display's only framebuffer.  Axes and the border are redrawn
        # afterward so they remain stable over the curve.
        y_range = self._state[0][3] - self._state[0][2]
        samples = self._state[2][0]
        if samples is not None and y_range > 0:
            curve_width = graph_right - graph_left + 1
            reveal = max(0, min(curve_width, int(self._state[3][0])))
            sample_index = 0
            sample_x = 0
            previous_x = None
            previous_y = 0
            while sample_index < len(samples) and sample_x < reveal:
                sample_y = samples[sample_index]
                if sample_y != CURVE_INVALID_Y:
                    if (previous_x is not None
                            and abs(sample_y - previous_y)
                            <= graph_h * 3 // 4):
                        display.draw_line(
                            graph_left + previous_x, graph_top + previous_y,
                            graph_left + sample_x, graph_top + sample_y, 15)
                    else:
                        display.draw_pixel(
                            graph_left + sample_x, graph_top + sample_y, 15)
                    previous_x = sample_x
                    previous_y = sample_y
                else:
                    previous_x = None
                sample_index += 1
                sample_x += CURVE_SAMPLE_STEP

        # Border
        display.draw_rectangle(graph_left - 1, graph_top,
                               graph_right - graph_left + 2, graph_h, 8)

        # Axes
        x_range = self._state[0][1] - self._state[0][0]
        x_zero = y_zero = None

        if y_range > 0 and self._state[0][2] <= 0 <= self._state[0][3]:
            ratio = (0 - self._state[0][2]) / y_range
            y_zero = graph_bot - int(ratio * graph_h)
            if graph_top <= y_zero <= graph_bot:
                display.draw_hline(graph_left, y_zero, graph_w + 1, 6)

        if x_range > 0 and self._state[0][0] <= 0 <= self._state[0][1]:
            ratio = (0 - self._state[0][0]) / x_range
            x_zero = graph_left + int(ratio * graph_w)
            if graph_left <= x_zero <= graph_right:
                display.draw_vline(x_zero, graph_top, graph_h + 1, 6)

        # Origin crosshair
        if x_zero is not None and y_zero is not None:
            display.draw_pixel(x_zero - 2, y_zero, 12)
            display.draw_pixel(x_zero + 2, y_zero, 12)
            display.draw_pixel(x_zero, y_zero - 2, 12)
            display.draw_pixel(x_zero, y_zero + 2, 12)

    def _draw_overlay(self, display):
        oy = self._state[3][3][0][3]
        if oy is None:
            return
        display.fill_rectangle(0, oy, self.width, OVERLAY_H, 0)
        self.input_box.y = oy + 1
        self.input_box.draw(display)
        display.draw_hline(0, oy + OVERLAY_H - 1, self.width, 10)

    def _draw_plot_progress(self, display):
        if self._state[3][0] >= 0:
            return
        x, y, width, height = PLOT_PROGRESS
        display.draw_text8x8(77, 19, 'Plotting', gs=15)
        display.fill_rectangle(x + 1, y + 1, width - 2, height - 2, 0)
        display.draw_rectangle(x, y, width, height, 8)
        fill = -self._state[3][0] - 1
        if fill > 0:
            display.fill_rectangle(x + 1, y + 1, fill, height - 2, 15)

    def _ensure_footer_cache(self):
        'Refresh the one visible footer only when its state changes.'
        mode = self._state[1][3]
        footer = self._state[1][1]
        if footer is None:
            footer = [None] * 10
            self._state[1][1] = footer
        if (footer[5] == mode
                and (mode != 0 or (
                    footer[6] == self._state[0][0]
                    and footer[7] == self._state[0][1]
                    and footer[8] == self._state[0][2]
                    and footer[9] == self._state[0][3]))):
            return

        if mode == 0:
            hint = (f"x:{self._state[0][0]:.2g}~{self._state[0][1]:.2g} "
                    f"y:{self._state[0][2]:.2g}~{self._state[0][3]:.2g}")
            right = _VIEW_HINT_RIGHT
        else:
            hint = _EDIT_HINT
            right = _EDIT_HINT_RIGHT
        hint = fit_text(hint, 126, self._state[3][3][0][1])
        right = fit_text(right, 76, self._state[3][3][0][1])
        footer[0] = hint
        footer[1] = hint.encode() if self._state[3][3][0][1] else b""
        footer[2] = right
        footer[3] = (
            right.encode() if self._state[3][3][0][1] and right else b"")
        footer[4] = max(
            130, 210 - text_width(right, self._state[3][3][0][1]) - 2)
        footer[5] = mode
        footer[6] = self._state[0][0]
        footer[7] = self._state[0][1]
        footer[8] = self._state[0][2]
        footer[9] = self._state[0][3]

    def _draw_hint(self, display):
        self._ensure_footer_cache()
        footer = self._state[1][1]
        draw_footer_cached(
            display, footer[0], footer[1], self._state[3][3][0][1],
            footer[2], footer[3], footer[4])

    def draw_present_rows(self, display):
        'Redraw the settled editor overlay without repainting the graph.'
        if self._state[3][0] < 0:
            self._draw_plot_progress(display)
            return
        self._draw_overlay(display)
        self._draw_hint(display)

    def draw(self, display):
        if self._state[1][3] == 2:
            self.error_popup.draw(display)
            return
        self._draw_graph(display)
        self._draw_plot_progress(display)
        self._draw_overlay(display)
        self._draw_hint(display)

    # ── input ───────────────────────────────────────────────────

    def update(self, kb, event=None):
        if self._state[1][3] == 2:
            changed = False
            if self.error_popup.expired():
                self._state[1][3] = 0
                changed = True
            elif event is not None:
                self._state[1][3] = 0
                changed = True
            if self._state[1][3] == 0:
                self.error_popup.dismiss()
            return 'REDRAW' if changed else None

        if event is not None and self._state[3][0] < 0:
            self._state[2][2] = False
            self._state[2][3] = False
            self._discard_curve_runtime()

        if kb.consume_long_press(0, 0, 1000):
            return 'BACK'

        if self._state[1][3] == 0:
            if event is not None:
                r, c, shift = event
                changed = False

                if r == 4 and c == 0:
                    pass
                elif r == 0 and c == 0:
                    return 'BACK'
                elif r == 1 and c == 1:
                    self._zoom_y(0.5) if not shift else self._zoom_x(0.5)
                    changed = True
                elif r == 3 and c == 1:
                    self._zoom_y(2.0) if not shift else self._zoom_x(2.0)
                    changed = True
                elif r == 2 and c == 0:
                    self._pan_x(-0.25)
                    changed = True
                elif r == 2 and c == 2:
                    self._pan_x(0.25)
                    changed = True
                elif r == 3 and c == 3:
                    self._enter_edit()
                    changed = True
                elif r == 3 and c == 5 and shift:
                    pass
                elif r == 3 and c == 5:
                    self._enter_edit('x')
                    changed = True
                # All other keys ignored in view mode
                return 'REDRAW' if changed else None

        else:
            action = self.input_box.update(kb, event)
            if action in ('MOVE', 'CHANGE'):
                return 'REDRAW'
            if action == 'ENT':
                self._leave_edit(plot=True)
                return 'REDRAW'
            elif action == 'rpn':
                return ('REDRAW' if self.input_box.insert_str('x')
                        else None)
            elif action == 'ESC':
                self._leave_edit(plot=False)
                return 'REDRAW'
            elif action == 'stab':
                if self._state[0][0] != -10.0 or self._state[0][1] != 10.0:
                    self._state[0][0] = -10.0
                    self._state[0][1] = 10.0
                    return 'REDRAW'
            elif action == 'DELETE':
                return 'REDRAW'

        return None
