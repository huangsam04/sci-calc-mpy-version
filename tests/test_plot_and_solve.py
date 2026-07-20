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


def test_solver_plugin_uses_active_registry():
    registry = build_registry()
    plugin_dir = pathlib.Path(__file__).parents[1] / "source" / "functions"
    report = load_function_files(registry, ["solve"], str(plugin_dir))

    result = evaluate('solve("x^2-4", "x", 1)', EvalContext({}, registry))

    assert not report.errors
    assert result == pytest.approx(2.0, abs=1e-6)
