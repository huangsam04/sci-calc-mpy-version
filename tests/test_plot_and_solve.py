import pathlib

import pytest

from calc.functions import EvalContext, build_registry
from calc.loader import load_function_files
from calc.parser import evaluate
from screens.plot import PlotScreen
from screens import plot as plot_module
from ui.memory import MemoryManager


def test_plot_builds_reusable_curve_for_valid_expression():
    registry = build_registry()
    plot = PlotScreen(None, registry=registry)
    plot.expr = "x^2"

    plot._render_curve()

    assert plot.mode != 2
    assert plot._curve_fb is not None
    assert plot._program is not None


def test_plot_snapshot_restores_parameters_before_building_curve():
    memory = MemoryManager()
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "x^2"
    plot.x_min = -3.0
    plot.x_max = 7.0
    state = plot.snapshot_state()

    plot.reset_state()
    plot.activate_default()
    plot.restore_state(state)

    assert plot.expr == "x^2"
    assert plot.x_min == -3.0
    assert plot.x_max == 7.0
    assert plot._curve_fb is None

    flags = plot.settle_step()
    assert flags & 1
    while flags != 3 and plot._curve_job is not None:
        flags = plot.settle_step()
    assert flags == 3
    assert plot._curve_fb is not None


def test_plot_waits_for_panel_animation_before_rendering_new_curve(monkeypatch):
    plot = PlotScreen(None, registry=build_registry())
    plot.mode = 1
    plot.input_box.set_str("x+1")
    starts = []

    def begin(auto_scale):
        starts.append(auto_scale)
        plot._curve_job = {}
        return True

    monkeypatch.setattr(plot, "_begin_curve_job", begin)
    monkeypatch.setattr(plot, "_advance_curve_job", lambda: 1)

    plot._leave_edit(plot=True)

    assert starts == []
    assert plot._needs_curve_restore is True
    assert plot.settle_step() == 1
    assert starts == [True]
    assert plot.settle_step() == 3


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
        if plot._curve_fb is not None and flags == 3:
            break

    assert plot._curve_fb is not None
    assert max(per_step) <= plot_module.CURVE_WORK_SLICE


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

    plot._render_curve()
    compiled = plot._program
    plot._pan_x(0.25)
    plot._zoom_x(0.5)
    plot._zoom_y(0.5)

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
    plot._render_curve()
    registry.replace(build_registry())
    plot._render_curve()

    assert calls == ["x^2", "x^2"]


def test_plot_caches_current_expression_after_lru_eviction(monkeypatch):
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
        plot._render_curve()

    plot._pan_x(0.25)

    assert calls == ["x", "x+1", "x+2", "x+3", "x+4"]
    assert "x" not in plot._program_cache
    assert "x+4" in plot._program_cache


def test_expression_editor_animates_as_overlay_without_moving_graph(monkeypatch):
    animated_attributes = []

    def record_animation(target, attribute, *args, **kwargs):
        animated_attributes.append(attribute)

    monkeypatch.setattr(plot_module, "insert_animation", record_animation)
    plot = PlotScreen(None, registry=build_registry())

    plot._enter_edit()
    plot._leave_edit(plot=False)

    assert animated_attributes == ["_overlay_y", "_overlay_y"]
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
    plot._render_curve()
    old_range = plot._y_max - plot._y_min

    plot._zoom_y(0.5)

    assert plot._y_max - plot._y_min == pytest.approx(old_range * 0.5)


def test_auto_scale_ignores_samples_clustered_around_asymptotes():
    """A few near-pole samples must not flatten the useful tan(x) curve."""
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "tan(x)"

    plot._render_curve()

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

    plot._render_curve()

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
