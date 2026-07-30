from calc.functions import EvalContext, build_registry
from calc.loader import load_function_files
from calc.parser import evaluate
from screens.function_panel import FunctionPanel
from calc import loader


class DisplayStub:
    def __init__(self):
        self.text = []

    def draw_text8x8(self, x, y, text, **kwargs):
        self.text.append(text)

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        pass

    def draw_hline(self, *args):
        pass


def test_loader_auto_loads_dependency_and_exposes_explicit_exports(tmp_path):
    (tmp_path / "base.py").write_text(
        "def double_value(value):\n"
        "    return value * 2\n"
        "EXPORTS = {'double_value': double_value}\n"
        "def double(value, context):\n"
        "    return double_value(value)\n"
        "def register(registry):\n"
        "    registry.prefix('double', double)\n",
        encoding="utf-8")
    (tmp_path / "dependent.py").write_text(
        "DEPENDENCIES = ('base',)\n"
        "def plus_one(value, context):\n"
        "    return context.plugin('base')['double_value'](value) + 1\n"
        "def register(registry):\n"
        "    assert callable(registry.plugin('base')['double_value'])\n"
        "    registry.prefix('plus_one', plus_one)\n",
        encoding="utf-8")
    registry = build_registry()

    report = load_function_files(registry, ["dependent"], str(tmp_path))

    assert [item[0] for item in report.loaded] == ["base", "dependent"]
    assert report.auto_enabled == ["base"]
    assert report.dependencies["dependent"] == ("base",)
    assert evaluate("plus_one(6)", EvalContext({}, registry)) == 13


def test_loader_reports_missing_and_cyclic_dependencies_without_partial_registration(tmp_path):
    (tmp_path / "missing.py").write_text(
        "DEPENDENCIES = ('not_here',)\n"
        "def register(registry): pass\n", encoding="utf-8")
    (tmp_path / "left.py").write_text(
        "DEPENDENCIES = ('right',)\n"
        "def register(registry): pass\n", encoding="utf-8")
    (tmp_path / "right.py").write_text(
        "DEPENDENCIES = ('left',)\n"
        "def register(registry): pass\n", encoding="utf-8")
    registry = build_registry()

    report = load_function_files(registry, ["missing", "left"], str(tmp_path))

    errors = dict(report.errors)
    assert "not_here" in errors
    assert "missing" in errors
    assert "left" in errors
    assert "cycle" in errors["left"].lower()
    assert report.loaded == []


def test_function_panel_auto_enables_saved_addon_dependencies_and_shows_notice(
        monkeypatch):
    panel = FunctionPanel(
        None, {"enabled_functions": ["basic", "plugin:dependent"]},
        {"dependent": ("base",), "base": ()},
        [("base", "base.py"), ("dependent", "dependent.py")])

    panel.activate()
    state = {name: is_on for name, is_on, _ in panel._items}
    display = DisplayStub()
    panel.draw(display)

    assert state["plugin:dependent"] is True
    assert state["plugin:base"] is True
    assert "plugin:base" in panel.get_enabled_list()
    assert any("Auto on: base" in text for text in display.text)
