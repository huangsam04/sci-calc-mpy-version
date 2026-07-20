import pathlib

import pytest

from calc.functions import EvalContext, build_registry
from calc.loader import load_function_files
from calc.parser import evaluate
from screens.plot import PlotScreen


def test_plot_builds_reusable_curve_for_valid_expression():
    registry = build_registry()
    plot = PlotScreen(None, registry=registry)
    plot.expr = "x^2"

    plot._render_curve()

    assert plot.mode != 2
    assert plot._curve_fb is not None
    assert plot._program is not None


def test_solver_plugin_uses_active_registry():
    registry = build_registry()
    plugin_dir = pathlib.Path(__file__).parents[1] / "source" / "functions"
    report = load_function_files(registry, ["solve"], str(plugin_dir))

    result = evaluate('solve("x^2-4", "x", 1)', EvalContext({}, registry))

    assert not report.errors
    assert result == pytest.approx(2.0, abs=1e-6)
