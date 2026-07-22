from pathlib import Path

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


def test_plugin_load_error_is_visible_in_function_panel():
    panel = FunctionPanel(None)
    display = DisplayStub()

    panel.set_load_errors([("broken", "boom")])
    panel.draw(display)

    assert any("broken" in text for text in display.text)
    assert any("boom" in text for text in display.text)
    assert any("ENT off" in text for text in display.text)


def test_builtin_groups_and_addons_have_unambiguous_user_labels(monkeypatch):
    monkeypatch.setattr(
        "screens.function_panel.load_settings",
        lambda: {"enabled_functions": [
            "basic", "trig", "math", "list", "plugin:basic", "plugin:trig",
        ]})
    monkeypatch.setattr(
        loader, "list_function_files",
        lambda: [("basic", "basic.py"), ("trig", "trig.py")])
    monkeypatch.setattr(
        loader, "describe_function_files",
        lambda: {"basic": ["%"], "trig": ["sinh", "cosh", "tanh", "sind"]})
    panel = FunctionPanel(None)

    panel.activate()

    labels = [label for label, _ in panel.menu.items]
    assert any("Arithmetic" in label for label in labels)
    assert any("Trigonometry" in label for label in labels)
    assert "[x] Add-on: basic (%)" in labels
    assert "[x] Add-on: trig (sinh, cosh...)" in labels


def test_function_panel_queues_shared_settings_when_leaving(monkeypatch):
    settings = {"enabled_functions": ["basic", "trig", "math", "list"]}
    queued = []

    def unexpected_load_settings():
        raise AssertionError("unexpected SD read")

    monkeypatch.setattr(
        "screens.function_panel.load_settings", unexpected_load_settings)
    panel = FunctionPanel(
        None,
        request_settings=lambda value, callback=None: queued.append(dict(value)),
        settings=settings)
    panel._items = [("basic", True, True), ("trig", False, True)]
    panel._dirty = True

    assert panel._queue_save() is True
    assert settings == {"enabled_functions": ["basic"]}
    assert queued == [{"enabled_functions": ["basic"]}]


def test_function_panel_keeps_deferred_save_failure_visible_after_reopening(
        monkeypatch):
    settings = {"enabled_functions": ["basic", "trig", "math", "list"]}
    monkeypatch.setattr(loader, "describe_function_files", lambda: {})
    monkeypatch.setattr(loader, "list_function_files", lambda: [])
    panel = FunctionPanel(None, settings=settings)

    panel._on_save_result(False)
    panel.activate()

    assert panel._save_error == "Not saved - check SD"


def test_main_reloads_functions_from_the_shared_in_memory_settings():
    source = (Path(__file__).parents[1] / "source" / "main.py").read_text(
        encoding="utf-8")
    start = source.index('elif result == "FUNC_PANEL_DONE":')
    end = source.index('elif result in ("FUNC_PICKER_DONE"', start)
    handler = source[start:end]

    assert "_reload_functions(settings, registry)" in handler
    assert "load_settings()" not in handler
