import pathlib

import pytest

from calc.functions import EvalContext, build_registry
from calc.loader import load_function_files
from calc.parser import evaluate
from screens.plot import PlotScreen
from screens import plot as plot_module


def test_plot_builds_reusable_curve_for_valid_expression():
    registry = build_registry()
    plot = PlotScreen(None, registry=registry)
    plot.expr = "x^2"

    plot._render_curve()

    assert plot.mode != 2
    assert plot._curve_fb is not None
    assert plot._program is not None


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
