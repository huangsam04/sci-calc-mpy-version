import pytest
from pathlib import Path

import main
from calc import loader
from calc.functions import EvalContext, FunctionRegistry, build_registry
from calc.loader import load_function_files
from calc.parser import evaluate


SOURCE = Path(__file__).parents[1] / "source"


def test_plugin_registers_functions_and_broken_file_is_isolated(tmp_path):
    (tmp_path / "good.py").write_text(
        "def twice(value, context):\n"
        "    return value * 2\n"
        "def register(registry):\n"
        "    registry.prefix('twice', twice)\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text(
        "def broken(value, context): return value\n"
        "def register(registry):\n"
        "    registry.prefix('half_registered', broken)\n"
        "    raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    registry = build_registry()

    report = load_function_files(registry, func_dir=str(tmp_path))

    assert [item[0] for item in report.loaded] == ["good"]
    assert report.errors[0][0] == "broken"
    assert "half_registered" not in registry
    assert evaluate("twice(6)", EvalContext({}, registry)) == 12


def test_registry_rejects_ambiguous_names_and_plugin_conflicts():
    registry = FunctionRegistry()
    callback = lambda value, context: value

    with pytest.raises(ValueError, match="Invalid identifier"):
        registry.prefix("bad+", callback)
    with pytest.raises(ValueError, match="require an identifier"):
        registry.prefix("!", callback)

    registry.prefix("same", callback)
    with pytest.raises(ValueError, match="already registered"):
        registry.prefix("same", callback)
    staging = FunctionRegistry()
    staging.prefix("same", callback)
    with pytest.raises(ValueError, match="already registered"):
        registry.merge(staging)


def test_registry_hot_reload_replaces_in_place():
    live = FunctionRegistry()
    live.prefix("old", lambda value, context: value)
    replacement = FunctionRegistry()
    replacement.prefix("new", lambda value, context: value + 1)

    live.replace(replacement)

    assert "old" not in live
    assert evaluate("new(2)", EvalContext({}, live)) == 3


def test_registry_hot_reload_keeps_plugin_errors_for_ui():
    live = FunctionRegistry()
    replacement = FunctionRegistry()
    replacement.plugin_errors = [("broken", "boom")]

    live.replace(replacement)

    assert live.plugin_errors == [("broken", "boom")]


def test_reload_releases_live_plugin_callbacks_before_loading_replacements(
        monkeypatch):
    live = build_registry(["basic"])
    live.prefix("old_plugin", lambda value, context: value)
    live.angle_mode = 1
    observations = []

    class Report:
        errors = []

    def load_replacement(registry, enabled_files):
        observations.append((registry is live, "old_plugin" in registry,
                             list(enabled_files)))
        registry.prefix("fresh_plugin", lambda value, context: value)
        return Report()

    monkeypatch.setattr(loader, "load_function_files", load_replacement)

    assert main._reload_functions(
        {"enabled_functions": ["basic", "plugin:fresh"]}, live) is live

    assert observations == [(True, False, ["fresh"])]
    assert "old_plugin" not in live
    assert "fresh_plugin" in live
    assert live.angle_mode == 1


def test_package_initializers_are_not_listed_as_plugins(tmp_path):
    from calc.loader import list_function_files

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "visible.py").write_text("def register(registry): pass\n", encoding="utf-8")

    assert list_function_files(str(tmp_path)) == [("visible", "visible.py")]


def test_plugin_summaries_follow_registered_function_names(tmp_path):
    (tmp_path / "multiple.py").write_text(
        "def unary(value, context): return value\n"
        "def binary(left, right, context): return left + right\n"
        "def listed(args, context): return args[0]\n"
        "def register(registry):\n"
        "    registry.prefix('unary', unary)\n"
        "    registry.infix('%%', binary)\n"
        "    registry.list_function('listed', listed, min_args=1)\n",
        encoding="utf-8",
    )

    assert loader.describe_function_files(str(tmp_path)) == {
        "multiple": ["unary", "%%", "listed"],
    }


def test_shipped_addons_have_dynamic_function_summaries():
    summaries = loader.describe_function_files(str(SOURCE / "functions"))

    assert summaries["basic"] == ["%"]
    assert summaries["solve"] == ["solve"]
    assert summaries["trig"] == [
        "sinh", "cosh", "tanh", "sind", "cosd", "tand", "PI",
    ]
