import pytest

from calc.functions import EvalContext, FunctionRegistry, build_registry
from calc.loader import load_function_files
from calc.parser import evaluate


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
