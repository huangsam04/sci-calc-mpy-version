import pathlib

import pytest

from calc.functions import EvalContext, build_registry
from calc.loader import load_function_files
from calc.parser import evaluate
from screens.plot import PlotScreen
from screens import plot as plot_module
from ui.element import SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW
from ui.memory import MemoryManager


def _finish_curve(plot, auto_scale=True):
    plot._needs_curve_restore = True
    plot._curve_restore_auto_scale = auto_scale
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

    assert plot.mode != 2
    assert plot._curve_fb is not None
    assert plot._program is not None


def test_resident_plot_keeps_parameters_while_rebuilding_released_curve():
    memory = MemoryManager()
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "x^2"
    plot.x_min = -3.0
    plot.x_max = 7.0
    _finish_curve(plot)
    plot.release_memory()
    memory.release_plot_workspace()
    plot.activate()

    assert plot.expr == "x^2"
    assert plot.x_min == -3.0
    assert plot.x_max == 7.0
    assert plot._curve_fb is None
    assert plot._needs_curve_restore
    assert _finish_curve(plot, auto_scale=False) == SETTLE_REDRAW
    assert plot._curve_fb is not None


def test_plot_defers_curve_work_to_bounded_idle_steps(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot.mode = 1
    plot.input_box.set_str("x+1")
    starts = []

    def begin(auto_scale):
        starts.append(auto_scale)
        plot._curve_job = {"phase": 2}
        plot._curve_gc_countdown = plot_module.CURVE_GC_SLICE_INTERVAL
        return True

    monkeypatch.setattr(plot, "_begin_curve_job", begin)
    monkeypatch.setattr(plot, "_advance_curve_job", lambda: 1)

    plot._leave_edit(plot=True)

    assert starts == []
    assert plot._needs_curve_restore is True
    assert plot.settle_step() == SETTLE_MORE
    assert starts == [True]
    assert plot.settle_step() == SETTLE_REDRAW


def test_plot_editor_uses_overlay_and_footer_rows_after_its_slide_settles():
    plot = PlotScreen(None, registry=build_registry())
    plot.mode = 1
    plot._overlay_y = 0
    plot.input_box.activate()
    plot.input_box.y = 1
    plot.input_box.cursor.y = 2
    plot.mark_presented()

    plot.input_box.insert_str("x")
    assert plot.get_present_rows() == ((0, 14), (54, 10))

    plot._overlay_y = -1
    assert plot.get_present_rows() is None


def test_plot_restore_limits_each_quiet_step_to_one_sampling_slice(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "x+1"
    plot._needs_curve_restore = True
    plot._curve_restore_auto_scale = True
    evaluations = []
    monkeypatch.setattr(
        plot, "_eval",
        lambda x: evaluations.append(x) or (x + 1, True, ""))

    per_step = []
    for _ in range(40):
        before = len(evaluations)
        flags = plot.settle_step()
        per_step.append(len(evaluations) - before)
        if plot._curve_fb is not None and not flags & SETTLE_MORE:
            break

    assert plot._curve_fb is not None
    assert max(per_step) <= plot_module.CURVE_WORK_SLICE


def test_plot_defers_full_redraw_until_after_final_sampling_slice(monkeypatch):
    class CurveBuffer:
        def pixel(self, *args):
            pass

        def line(self, *args):
            pass

    plot = PlotScreen(None, registry=build_registry())
    plot._curve_fb = CurveBuffer()
    plot._curve_job = {
        "phase": 2,
        "index": 0,
        "n": 1,
        "graph_w": 1,
        "graph_h": 1,
        "prev_x": None,
        "prev_y": None,
    }
    plot._curve_gc_countdown = plot_module.CURVE_GC_SLICE_INTERVAL
    monkeypatch.setattr(plot, "_eval", lambda x: (0.0, True, ""))

    assert plot.settle_step() == SETTLE_MORE
    assert plot._curve_job["phase"] == 3
    assert plot.settle_step() == SETTLE_REDRAW
    assert plot._curve_job is None


def test_plot_schedules_gc_by_bounded_work_without_polling_heap(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot._curve_job = {"phase": 2}
    plot._curve_gc_countdown = plot_module.CURVE_GC_SLICE_INTERVAL
    advances = []
    monkeypatch.setattr(
        plot, "_advance_curve_job", lambda: advances.append(1) or 0)

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
    compiled = plot._program
    plot._pan_x(0.25)
    _finish_curve(plot)
    plot._zoom_x(0.5)
    _finish_curve(plot)
    plot._zoom_y(0.5)
    _finish_curve(plot, auto_scale=False)

    assert calls == [("x^2", registry)]
    assert plot._program is compiled


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
    assert plot._program_expr == "x+4"
    assert not hasattr(plot, "_program_cache")


def test_expression_editor_snaps_as_overlay_without_moving_graph():
    plot = PlotScreen(None, registry=build_registry())

    plot._enter_edit()
    assert plot._overlay_y == 0
    plot._leave_edit(plot=False)

    assert plot._overlay_y == -plot_module.OVERLAY_H
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
    old_range = plot._y_max - plot._y_min

    plot._zoom_y(0.5)

    assert plot._y_max - plot._y_min == pytest.approx(old_range * 0.5)


def test_auto_scale_ignores_samples_clustered_around_asymptotes():
    """A few near-pole samples must not flatten the useful tan(x) curve."""
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "tan(x)"

    _finish_curve(plot)

    assert plot._y_min > -30
    assert plot._y_max < 30


def test_auto_scale_reuses_the_curve_sampling_step(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "x^2"
    calls = []

    def evaluate(x_value):
        calls.append(x_value)
        return x_value * x_value, True, ""

    monkeypatch.setattr(plot, "_eval", evaluate)

    _finish_curve(plot)

    graph_width = plot.width - plot_module.GRAPH_PAD_X * 2
    sample_count = len(range(0, graph_width + 1, 2))
    assert len(calls) == sample_count * 2


def test_plot_error_timeout_is_processed_during_draw(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot.mode = 2
    plot.error_popup.active = True
    monkeypatch.setattr(plot.error_popup, "expired", lambda: True)
    calls = []
    monkeypatch.setattr(plot, "_draw_graph", lambda display: calls.append("graph"))
    monkeypatch.setattr(plot, "_draw_overlay", lambda display: None)
    monkeypatch.setattr(plot, "_draw_hint", lambda display: None)

    plot.draw(None)

    assert plot.mode == 0
    assert calls == ["graph"]


def test_solver_plugin_uses_active_registry():
    registry = build_registry()
    plugin_dir = pathlib.Path(__file__).parents[1] / "source" / "functions"
    report = load_function_files(registry, ["solve"], str(plugin_dir))

    result = evaluate('solve("x^2-4", "x", 1)', EvalContext({}, registry))

    assert not report.errors
    assert result == pytest.approx(2.0, abs=1e-6)
