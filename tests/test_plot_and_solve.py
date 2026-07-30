import pathlib

import pytest

from calc.functions import EvalContext, build_registry
from calc.loader import load_function_files
from calc.number import Number
from calc.parser import evaluate
from screens.plot import PlotScreen
from screens import plot as plot_module
from ui.element import SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW
from ui.memory import MemoryManager
from ui.motion import DAMAGE_FULL, DAMAGE_NONE, DAMAGE_PARTIAL, DamageMap


class KeyboardStub:
    def is_pressed(self, _row, _col):
        return False

    def get_hold_time(self, _row, _col):
        return 0

    def consume_long_press(self, _row, _col, _threshold):
        return False


class LiveShiftKeyboard(KeyboardStub):
    def is_pressed(self, row, col):
        return (row, col) == (4, 0)


class FooterDisplay:
    def __init__(self):
        self.direct = []

    def fill_rectangle(self, *_args):
        pass

    def draw_hline(self, *_args):
        pass

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct.append((x, y, text, font, gs))


class FooterFont:
    width = 5
    height = 7

    def measure_text(self, text, spacing=1):
        return len(text) * 6


class CurveFrame:
    def __init__(self):
        self.pixels = []
        self.lines = []

    def pixel(self, *args):
        self.pixels.append(args)

    def line(self, *args):
        self.lines.append(args)


class CurveDisplay:
    def __init__(self):
        self.gs4_fb = CurveFrame()

    def draw_rectangle(self, *_args):
        pass

    def draw_hline(self, *_args):
        pass

    def draw_vline(self, *_args):
        pass

    def draw_pixel(self, *_args):
        pass


def _curve_job(auto_scale=False, graph_w=1, graph_h=1, sample_count=1):
    return plot_module._CurveJob(auto_scale, graph_w, graph_h, sample_count)


def _finish_curve(plot, auto_scale=True):
    plot._state[2][2] = True
    plot._state[2][3] = auto_scale
    for _ in range(60):
        flags = plot.settle_step()
        if not flags & SETTLE_MORE:
            return flags
    raise AssertionError("curve job did not finish")


def test_plot_builds_reusable_curve_for_valid_expression():
    registry = build_registry()
    plot = PlotScreen(None, registry=registry)
    plot.expr = "x^2"

    assert _finish_curve(plot) == SETTLE_REDRAW

    assert plot._state[1][3] != 2
    assert plot._state[2][0] is not None
    assert plot._state[3][3][1][2] is not None


def test_plot_draws_compact_samples_directly_into_the_main_framebuffer():
    plot = PlotScreen(None, registry=build_registry())
    samples = bytearray((10, 12, plot_module.CURVE_INVALID_Y, 20))
    plot._state[2][0] = samples
    plot._state[2][1] = samples
    display = CurveDisplay()

    plot._draw_graph(display)

    assert display.gs4_fb.pixels == [
        (2, 10, 15),
        (8, 20, 15),
    ]
    assert display.gs4_fb.lines == [(2, 10, 4, 12, 15)]


def test_resident_plot_keeps_parameters_while_rebuilding_released_curve():
    memory = MemoryManager()
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "x^2"
    plot._state[0][0] = -3.0
    plot._state[0][1] = 7.0
    _finish_curve(plot)
    plot.release_memory()
    memory.release_plot_workspace()
    plot.activate()

    assert plot.expr == "x^2"
    assert plot._state[0][0] == -3.0
    assert plot._state[0][1] == 7.0
    assert plot._state[2][0] is None
    assert plot._state[2][2]
    assert _finish_curve(plot, auto_scale=False) == SETTLE_REDRAW
    assert plot._state[2][0] is not None


def test_plot_release_drops_retained_error_text_with_derived_curve_state():
    plot = PlotScreen(None, registry=build_registry())
    plot.error_popup.show("x", "domain error")

    assert plot.release_memory() is True
    assert plot.error_popup.expr == ""
    assert plot.error_popup.title == ""
    assert plot.error_popup.detail == ""


def test_plot_uses_queued_shift_snapshot_for_axis_zoom():
    plot = PlotScreen(None, registry=build_registry())

    # Shift+8 is queued, then Shift is released before this update runs.
    plot.update(KeyboardStub(), (1, 1, True))

    assert (plot._state[0][0], plot._state[0][1]) == (-5.0, 5.0)
    assert (plot._state[0][2], plot._state[0][3]) == (-5.0, 5.0)

    # A later live Shift press cannot reinterpret an ordinary queued 8 edge.
    plot = PlotScreen(None, registry=build_registry())
    plot.update(LiveShiftKeyboard(), (1, 1, False))

    assert (plot._state[0][0], plot._state[0][1]) == (-10.0, 10.0)
    assert (plot._state[0][2], plot._state[0][3]) == (-2.5, 2.5)


def test_plot_reuses_its_current_bounds_footer_without_refitting(monkeypatch):
    font = FooterFont()
    plot = PlotScreen(font, font, registry=build_registry())
    display = FooterDisplay()
    plot._draw_hint(display)
    footer = plot._state[1][1]
    hint_bytes = footer[1]
    right_bytes = footer[3]

    def unexpected_fit(*_args):
        raise AssertionError("steady graph footer must not refit text")

    monkeypatch.setattr(plot_module, "fit_text", unexpected_fit)
    plot._draw_hint(display)
    plot._draw_hint(display)

    assert plot._state[1][1] is footer
    assert footer[1] is hint_bytes
    assert footer[3] is right_bytes
    assert display.direct[-2:] == [
        (3, 56, hint_bytes, font, 9),
        (footer[4], 56, right_bytes, font, 15),
    ]
    assert not hasattr(plot, "_footer_hint")


def test_plot_exposes_letters_only_while_its_editor_is_visible():
    plot = PlotScreen(None, registry=build_registry())

    assert plot.letter_input_target() is None
    assert plot.blocks_global_shortcuts() is False

    plot._enter_edit()
    assert plot.letter_input_target() is plot.input_box

    plot._state[1][3] = 2
    assert plot.letter_input_target() is None
    assert plot.blocks_global_shortcuts() is True


def test_curve_job_is_fixed_shape_and_keeps_only_needed_autoscale_state():
    automatic = _curve_job(True, 20, 54, 21)
    manual = _curve_job(False, 20, 54, 21)

    assert not hasattr(automatic, "__dict__")
    assert automatic.phase == 0
    assert automatic.robust == [0.0] * plot_module.ROBUST_SAMPLE_LIMIT
    assert manual.phase == 1
    assert manual.robust == [0.0] * plot_module.ROBUST_SAMPLE_LIMIT


def test_plot_reuses_the_boot_allocated_curve_job():
    plot = PlotScreen(None, registry=build_registry())
    pooled = plot._state[3][3][3]
    plot.expr = "x*x"

    plot._state[2][2] = True
    plot._state[2][3] = True
    assert plot.settle_step() == SETTLE_MORE
    assert plot._state[3][1] is pooled
    _finish_curve(plot)
    assert plot._state[3][1] is None

    plot._state[2][2] = True
    plot._state[2][3] = True
    assert plot.settle_step() == SETTLE_MORE
    assert plot._state[3][1] is pooled


def test_angle_change_releases_old_curve_and_restarts_bounded_sampling():
    memory = MemoryManager()
    registry = build_registry()
    plot = PlotScreen(None, registry=registry, memory=memory)
    plot.expr = "sin(x)"
    workspace = memory.reserve_plot_workspace(plot.height)
    plot._state[2][1] = workspace
    plot._state[2][0] = object()
    plot._state[3][1] = _curve_job()
    plot._state[3][1].phase = 2
    plot._state[3][3][1][2] = ("literal", Number(1), 0)
    plot._state[3][3][1][3] = "sin(x)"
    plot._state[3][3][2][0] = registry.revision
    plot._state[3][3][2][1] = True

    registry.angle_mode = 1
    assert plot.on_angle_mode_changed() is True

    assert plot._state[3][1] is None
    assert plot._state[2][0] is None
    assert plot._state[2][1] is None
    assert plot._state[3][3][1][2] is None
    assert plot._state[3][3][1][3] is None
    assert memory.get_plot_workspace() is workspace
    assert plot._state[2][2] is True
    assert plot._state[2][3] is True

    assert plot.settle_step() == SETTLE_MORE
    value, valid, _ = plot._eval(90.0)
    assert valid is True
    assert value == pytest.approx(1.0)


def test_plot_memory_error_clears_page_refs_and_stops_sampling(monkeypatch):
    memory = MemoryManager()
    workspace = memory.reserve_plot_workspace(64)
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "x^2"
    plot._state[2][2] = True
    plot._state[2][3] = True
    samples = []

    def exhaust_heap(_screen, x_value):
        samples.append(x_value)
        raise MemoryError("injected")

    monkeypatch.setattr(PlotScreen, "_eval", exhaust_heap)

    assert plot.settle_step() == SETTLE_MORE
    assert plot.settle_step() == SETTLE_REDRAW

    assert samples
    assert plot._state[3][1] is None
    assert plot._state[2][0] is None
    assert plot._state[2][1] is None
    assert memory.get_plot_workspace() is workspace
    assert plot.error_popup.expr == ""
    assert plot.error_popup.title == "Graph paused"
    assert plot.error_popup.detail == "Low memory: graph stopped"
    assert plot.settle_step() == 0
    assert len(samples) == 1


def test_plot_evaluation_does_not_stringify_memory_error(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot._state[3][3][1][2] = ("literal", 1, 0)
    plot._state[3][3][2][1] = False

    def exhaust_heap(program, context):
        raise MemoryError("injected")

    monkeypatch.setattr(plot_module, "evaluate_program", exhaust_heap)

    with pytest.raises(MemoryError, match="injected"):
        plot._eval(1.0)


def test_plot_workspace_reservation_failure_does_not_construct_memory_error(
        monkeypatch):
    memory = MemoryManager()
    workspace = memory.get_plot_workspace()
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "x^2"
    plot._state[2][2] = True
    plot._state[2][3] = True
    compile_calls = []
    sample_calls = []

    monkeypatch.setattr(
        MemoryManager, "reserve_plot_workspace", lambda self, height: None)
    monkeypatch.setattr(
        plot_module, "compile_expression",
        lambda expr, registry: compile_calls.append((expr, registry)))
    monkeypatch.setattr(
        PlotScreen, "_eval",
        lambda _screen, x_value: sample_calls.append(x_value))

    class SyntheticMemoryErrorForbidden(Exception):
        def __init__(self, *args):
            raise AssertionError("reservation failure must not construct MemoryError")

    monkeypatch.setattr(
        plot_module, "MemoryError", SyntheticMemoryErrorForbidden,
        raising=False)

    assert plot.settle_step() == SETTLE_REDRAW
    assert memory.get_plot_workspace() is workspace
    assert compile_calls == []
    assert sample_calls == []
    assert plot.error_popup.title == "Graph paused"
    assert plot.error_popup.detail == "Low memory: graph stopped"


def test_plot_binds_preallocated_samples_without_a_framebuffer_allocation():
    memory = MemoryManager()
    workspace = memory.reserve_plot_workspace(64)
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot._state[3][1] = _curve_job()
    plot._state[3][1].phase = 1

    assert plot._advance_curve_job() == 0
    assert plot._state[3][1].phase == 2
    assert plot._state[3][1].curve_buf is workspace
    assert plot._state[2][1] is workspace
    assert plot._state[2][0] is workspace


def test_plot_discards_stale_program_before_compiling_replacement(monkeypatch):
    registry = build_registry()
    plot = PlotScreen(None, registry=registry)
    stale_program = object()
    plot.expr = "x+1"
    plot._state[3][3][1][2] = stale_program
    plot._state[3][3][1][3] = "x"
    plot._state[3][3][2][0] = registry.revision
    events = []
    original_clear_program = PlotScreen._clear_program

    def record_clear(screen):
        events.append(("clear", plot._state[3][3][1][2]))
        original_clear_program(screen)

    def record_compile(expr, active_registry):
        events.append(("compile", plot._state[3][3][1][2]))
        return ("literal", Number(1), 0)

    monkeypatch.setattr(PlotScreen, "_clear_program", record_clear)
    monkeypatch.setattr(
        plot_module.gc, "collect",
        lambda: events.append(("collect", plot._state[3][3][1][2])))
    monkeypatch.setattr(plot_module, "compile_expression", record_compile)

    plot._compile_program()

    assert events == [
        ("clear", stale_program),
        ("collect", None),
        ("compile", None),
    ]
    assert plot._state[3][3][1][3] == "x+1"


def test_plot_domain_failures_are_stringified_once_per_autoscale_job(
        monkeypatch):
    class DomainFailure(Exception):
        def __init__(self):
            self.stringifications = 0

        def __str__(self):
            self.stringifications += 1
            return "domain error"

    plot = PlotScreen(None, registry=build_registry())
    failure = DomainFailure()
    job = _curve_job(True)
    plot._state[3][1] = job
    plot._state[3][3][1][2] = ("literal", Number(1), 0)

    def fail_domain(program, context):
        raise failure

    monkeypatch.setattr(plot_module, "evaluate_program", fail_domain)

    for x_value in (0.0, 1.0, 2.0):
        assert plot._eval(x_value) == (0.0, False, "")
    job.phase = 2
    assert plot._eval(3.0) == (0.0, False, "")

    assert failure.stringifications == 1
    assert job.first_error == "domain error"


def test_plot_parse_failure_releases_all_curve_runtime(monkeypatch):
    memory = MemoryManager()
    workspace = memory.reserve_plot_workspace(64)
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "bad("
    plot._state[2][1] = workspace
    plot._state[2][0] = object()
    plot._state[3][1] = _curve_job(True)
    plot._state[3][3][1][2] = ("literal", Number(1), 0)
    plot._state[3][3][1][3] = "previous"
    plot._state[2][2] = True
    plot._state[2][3] = True

    def fail_parse(expr, registry):
        raise plot_module.ParseError("broken expression", 3)

    monkeypatch.setattr(plot_module, "compile_expression", fail_parse)

    assert plot.settle_step() == SETTLE_REDRAW
    assert plot._state[3][1] is None
    assert plot._state[2][1] is None
    assert plot._state[2][0] is None
    assert plot._state[3][3][1][2] is None
    assert plot._state[3][3][1][3] is None
    assert memory.get_plot_workspace() is workspace


def test_plot_no_valid_sample_releases_all_curve_runtime(monkeypatch):
    memory = MemoryManager()
    workspace = memory.reserve_plot_workspace(64)
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "domain(x)"
    plot._state[2][1] = workspace
    plot._state[2][0] = object()
    plot._state[3][1] = _curve_job(True)
    plot._state[3][2] = plot_module.CURVE_GC_SLICE_INTERVAL
    plot._state[3][3][1][2] = ("literal", Number(1), 0)
    plot._state[3][3][1][3] = plot.expr

    monkeypatch.setattr(
        PlotScreen, "_eval",
        lambda _screen, x_value: (0.0, False, "domain error"))

    assert plot.settle_step() == SETTLE_REDRAW
    assert plot._state[3][1] is None
    assert plot._state[2][1] is None
    assert plot._state[2][0] is None
    assert plot._state[3][3][1][2] is None
    assert plot._state[3][3][1][3] is None
    assert memory.get_plot_workspace() is workspace


def test_plot_defers_curve_work_to_bounded_idle_steps(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot._state[1][3] = 1
    plot.input_box.set_str("x+1")
    starts = []

    def begin(screen, auto_scale):
        starts.append(auto_scale)
        screen._state[3][1] = _curve_job()
        screen._state[3][1].phase = 2
        screen._state[3][2] = plot_module.CURVE_GC_SLICE_INTERVAL
        return True

    monkeypatch.setattr(PlotScreen, "_begin_curve_job", begin)
    monkeypatch.setattr(PlotScreen, "_advance_curve_job", lambda _screen: 1)

    plot._leave_edit(plot=True)

    assert starts == []
    assert plot._state[2][2] is True
    assert plot.settle_step() == SETTLE_MORE
    assert starts == [True]
    assert plot.settle_step() == SETTLE_REDRAW


def test_plot_editor_uses_overlay_and_footer_rows_at_its_legal_resting_y():
    plot = PlotScreen(None, registry=build_registry())
    assert plot._state[1][0] is None
    assert plot._state[1][1] is None
    plot._state[1][3] = 1
    plot._state[3][3][0][3] = 0
    plot.input_box.activate()
    plot.input_box.y = 1
    plot.input_box.cursor.y = 2
    plot.mark_presented()
    presented = plot._state[1][0]

    plot.input_box.insert_str("x")
    damage = DamageMap()
    assert plot.collect_present_damage(damage) == DAMAGE_PARTIAL
    assert damage.ranges == [[0, 14], [54, 10]]
    assert not hasattr(plot, "_editor_present_state")

    plot.mark_presented()
    assert plot._state[1][0] is presented
    damage.clear()
    assert plot.collect_present_damage(damage) == DAMAGE_NONE

    plot._state[3][3][0][3] = None
    damage.clear()
    assert plot.collect_present_damage(damage) == DAMAGE_FULL
    plot.release_memory()
    assert plot._state[1][0] is None
    assert plot._state[1][1] is None
    assert not hasattr(plot, "_presented_mode")


def test_plot_restore_limits_each_quiet_step_to_one_sampling_slice(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "x+1"
    plot._state[2][2] = True
    plot._state[2][3] = True
    evaluations = []
    monkeypatch.setattr(
        PlotScreen, "_eval",
        lambda _screen, x: evaluations.append(x) or (x + 1, True, ""))

    per_step = []
    for _ in range(40):
        before = len(evaluations)
        flags = plot.settle_step()
        per_step.append(len(evaluations) - before)
        if plot._state[2][0] is not None and not flags & SETTLE_MORE:
            break

    assert plot._state[2][0] is not None
    assert max(per_step) <= plot_module.CURVE_WORK_SLICE


def test_plot_defers_full_redraw_until_after_final_sampling_slice(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    samples = bytearray([plot_module.CURVE_INVALID_Y])
    plot._state[2][0] = samples
    plot._state[2][1] = samples
    plot._state[3][1] = _curve_job()
    plot._state[3][1].phase = 2
    plot._state[3][1].buf_size = 1
    plot._state[3][1].curve_buf = samples
    plot._state[3][2] = plot_module.CURVE_GC_SLICE_INTERVAL
    monkeypatch.setattr(
        PlotScreen, "_eval", lambda _screen, x: (0.0, True, ""))

    assert plot.settle_step() == SETTLE_MORE
    assert plot._state[3][1].phase == 3
    assert plot.settle_step() == SETTLE_REDRAW
    assert plot._state[3][1] is None


def test_plot_schedules_gc_by_bounded_work_without_polling_heap(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot._state[3][1] = _curve_job()
    plot._state[3][1].phase = 2
    plot._state[3][2] = plot_module.CURVE_GC_SLICE_INTERVAL
    advances = []
    monkeypatch.setattr(
        PlotScreen, "_advance_curve_job",
        lambda _screen: advances.append(1) or 0)

    class UnexpectedGC:
        def mem_free(self):
            raise AssertionError("sampling must not poll the full heap")

    monkeypatch.setattr(plot_module, "gc", UnexpectedGC(), raising=False)

    for _ in range(plot_module.CURVE_GC_SLICE_INTERVAL):
        assert plot.settle_step() == SETTLE_MORE
    assert plot.settle_step() == SETTLE_COLLECT | SETTLE_MORE
    assert len(advances) == plot_module.CURVE_GC_SLICE_INTERVAL


def test_plot_reuses_compiled_expression_across_pan_and_zoom(monkeypatch):
    registry = build_registry()
    plot = PlotScreen(None, registry=registry)
    plot.expr = "x^2"
    real_compile = plot_module.compile_expression
    calls = []

    def record_compile(expr, active_registry):
        calls.append((expr, active_registry))
        return real_compile(expr, active_registry)

    monkeypatch.setattr(plot_module, "compile_expression", record_compile)

    _finish_curve(plot)
    compiled = plot._state[3][3][1][2]
    plot._pan_x(0.25)
    _finish_curve(plot)
    plot._zoom_x(0.5)
    _finish_curve(plot)
    plot._zoom_y(0.5)
    _finish_curve(plot, auto_scale=False)

    assert calls == [("x^2", registry)]
    assert plot._state[3][3][1][2] is compiled


def test_plot_recompiles_expression_after_function_registry_replacement(monkeypatch):
    registry = build_registry()
    plot = PlotScreen(None, registry=registry)
    plot.expr = "x^2"
    real_compile = plot_module.compile_expression
    calls = []

    def record_compile(expr, active_registry):
        calls.append(expr)
        return real_compile(expr, active_registry)

    monkeypatch.setattr(plot_module, "compile_expression", record_compile)
    _finish_curve(plot)
    registry.replace(build_registry())
    _finish_curve(plot)

    assert calls == ["x^2", "x^2"]


def test_plot_keeps_only_the_current_compiled_expression(monkeypatch):
    registry = build_registry()
    plot = PlotScreen(None, registry=registry)
    real_compile = plot_module.compile_expression
    calls = []

    def record_compile(expr, active_registry):
        calls.append(expr)
        return real_compile(expr, active_registry)

    monkeypatch.setattr(plot_module, "compile_expression", record_compile)
    for expr in ("x", "x+1", "x+2", "x+3", "x+4"):
        plot.expr = expr
        _finish_curve(plot)

    plot._pan_x(0.25)
    _finish_curve(plot)

    assert calls == ["x", "x+1", "x+2", "x+3", "x+4"]
    assert plot._state[3][3][1][3] == "x+4"
    assert not hasattr(plot, "_program_cache")


def test_expression_editor_snaps_as_overlay_without_moving_graph():
    plot = PlotScreen(None, registry=build_registry())

    assert plot.input_box.y == 1
    assert plot._state[3][3][0][3] is None

    plot._enter_edit()
    assert plot._state[3][3][0][3] == 0
    plot._leave_edit(plot=False)

    assert plot._state[3][3][0][3] is None
    assert not hasattr(plot, "_graph_top")


def test_escape_cancels_plot_edit_without_changing_active_expression():
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "x"
    plot.input_box.set_str("x")
    plot._enter_edit()
    plot.input_box.set_str("x^2")

    plot._leave_edit(plot=False)

    assert plot.expr == "x"
    assert plot.input_box.get_str() == "x"


def test_y_zoom_preserves_requested_manual_range():
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "x^2"
    _finish_curve(plot)
    old_range = plot._state[0][3] - plot._state[0][2]

    plot._zoom_y(0.5)

    assert plot._state[0][3] - plot._state[0][2] == pytest.approx(old_range * 0.5)


def test_auto_scale_ignores_samples_clustered_around_asymptotes():
    """A few near-pole samples must not flatten the useful tan(x) curve."""
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "tan(x)"

    _finish_curve(plot)

    assert plot._state[0][2] > -30
    assert plot._state[0][3] < 30


def test_auto_scale_reuses_the_curve_sampling_step(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "x^2"
    calls = []

    def evaluate(_screen, x_value):
        calls.append(x_value)
        return x_value * x_value, True, ""

    monkeypatch.setattr(PlotScreen, "_eval", evaluate)

    _finish_curve(plot)

    graph_width = plot.width - plot_module.GRAPH_PAD_X * 2
    sample_count = len(range(0, graph_width + 1, 2))
    assert len(calls) == sample_count * 2


@pytest.mark.parametrize(
    ("action", "restore_auto_scale"),
    (
        ("zoom_y", False),
        ("zoom_x", True),
        ("pan_x", True),
        ("submit_edit", True),
    ),
)
def test_plot_view_changes_cancel_phase_two_job_before_next_settle(
        monkeypatch, action, restore_auto_scale):
    class DiscardedFrameBuffer:
        def pixel(self, *_args):
            raise AssertionError("cancelled curve buffer was sampled")

        def line(self, *_args):
            raise AssertionError("cancelled curve buffer was sampled")

    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "x"
    plot._state[2][1] = bytearray(1)
    plot._state[2][0] = DiscardedFrameBuffer()
    plot._state[3][1] = _curve_job()
    plot._state[3][1].phase = 2
    plot._state[3][2] = plot_module.CURVE_GC_SLICE_INTERVAL

    if action == "zoom_y":
        plot._zoom_y(0.5)
    elif action == "zoom_x":
        plot._zoom_x(0.5)
    elif action == "pan_x":
        plot._pan_x(0.25)
    else:
        plot._state[1][3] = 1
        plot.input_box.set_str("x+1")
        plot._leave_edit(plot=True)

    assert plot._state[3][1] is None
    assert plot._state[2][0] is None
    assert plot._state[2][1] is None
    assert plot._state[2][2] is True
    assert plot._state[2][3] is restore_auto_scale

    starts = []

    def begin(screen, auto_scale):
        starts.append(auto_scale)
        # A stale phase-2 job would otherwise request a collection before it
        # reaches its old FrameBuffer on the next quiet step.
        screen._state[3][2] = plot_module.CURVE_GC_SLICE_INTERVAL
        return False

    monkeypatch.setattr(PlotScreen, "_begin_curve_job", begin)
    monkeypatch.setattr(
        PlotScreen, "_eval", lambda _screen, _x_value: (0.0, True, ""))

    assert plot.settle_step() == SETTLE_REDRAW
    assert starts == [restore_auto_scale]
    assert plot.settle_step() == 0


def test_plot_error_timeout_is_settled_outside_draw(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot._state[1][3] = 2
    plot.error_popup.active = True
    monkeypatch.setattr(
        type(plot.error_popup), "expired", lambda _popup: True)
    graph_calls = []
    popup_calls = []
    monkeypatch.setattr(
        PlotScreen, "_draw_graph",
        lambda _screen, display: graph_calls.append("graph"))
    monkeypatch.setattr(
        PlotScreen, "_draw_overlay", lambda _screen, display: None)
    monkeypatch.setattr(
        PlotScreen, "_draw_hint", lambda _screen, display: None)
    monkeypatch.setattr(
        type(plot.error_popup), "draw",
        lambda _popup, display: popup_calls.append("popup"))

    plot.draw(None)

    assert plot._state[1][3] == 2
    assert plot.error_popup.active is True
    assert graph_calls == []
    assert popup_calls == ["popup"]

    assert plot.settle_step() == SETTLE_REDRAW
    assert plot._state[1][3] == 0
    assert plot.error_popup.active is False

    plot.draw(None)
    assert graph_calls == ["graph"]


def test_solver_plugin_uses_active_registry():
    registry = build_registry()
    plugin_dir = pathlib.Path(__file__).parents[1] / "source" / "functions"
    report = load_function_files(registry, ["solve"], str(plugin_dir))

    result = evaluate('solve("x^2-4", "x", 1)', EvalContext({}, registry))

    assert not report.errors
    assert result == pytest.approx(2.0, abs=1e-6)
