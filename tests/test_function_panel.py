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
    panel = FunctionPanel(
        None,
        plugin_functions={"basic": ["%"],
                          "trig": ["sinh", "cosh", "tanh", "sind"]})
    display = DisplayStub()

    panel.set_load_errors([("broken", "boom")])
    panel.draw(display)

    assert any("broken" in text for text in display.text)
    assert any("boom" in text for text in display.text)
    assert any("ENT off" in text for text in display.text)


def test_function_panel_reuses_loaded_catalog_without_reexecuting_plugins(
        monkeypatch):
    calls = []
    monkeypatch.setattr(
        loader, "list_function_files",
        lambda: calls.append("list") or [("solve", "solve.py")])
    monkeypatch.setattr(
        loader, "describe_function_files",
        lambda: (_ for _ in ()).throw(
            AssertionError("boot must not re-execute plugin source")))

    panel = FunctionPanel(
        None,
        settings={"enabled_functions": ["basic", "plugin:solve"]},
        plugin_functions={"solve": ["solve"]},
        plugin_dependencies={"solve": ()})
    panel.activate()

    assert calls == ["list"]
    assert "[x] Add-on: solve (solve)" in [
        label for label, _ in panel.menu.items]


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
    panel = FunctionPanel(
        None,
        plugin_functions={"basic": ["%"],
                          "trig": ["sinh", "cosh", "tanh", "sind"]})

    panel.activate()

    labels = [label for label, _ in panel.menu.items]
    assert any("Arithmetic" in label for label in labels)
    assert any("Trigonometry" in label for label in labels)
    assert "[x] Add-on: basic (%)" in labels
    assert "[x] Add-on: trig (sinh, cosh...)" in labels


def test_function_panel_uses_preloaded_plugin_catalog_during_activation(
        monkeypatch):
    calls = []
    monkeypatch.setattr(
        loader, "list_function_files",
        lambda: calls.append("list") or [("basic", "basic.py")])
    monkeypatch.setattr(
        loader, "describe_function_files",
        lambda: calls.append("describe") or {"basic": ["%"]})

    panel = FunctionPanel(
        None,
        settings={"enabled_functions": ["basic"]},
        plugin_functions={"basic": ["%"]})

    assert calls == ["list"]
    panel.activate()
    assert calls == ["list"]
    assert panel._items[-1] == ("plugin:basic", False, False)


def test_function_panel_builds_deferred_menu_on_direct_activation(
        monkeypatch):
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    refreshes = []
    monkeypatch.setattr(panel, "_refresh", lambda: refreshes.append(True))

    panel.activate()

    assert refreshes == [True]


def test_function_panel_cancels_unchanged_exit(monkeypatch):
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    panel.activate()
    saves = []
    monkeypatch.setattr(panel, "_queue_save", lambda: saves.append(True))

    # (0, 0) is the physical ESC key, so this follows the production menu
    # key-to-action path rather than faking a menu return value.
    assert panel.update(None, (0, 0, False)) == "FUNC_PANEL_CANCEL"
    assert saves == []


def test_function_panel_only_rescans_plugins_after_explicit_shift_enter(
        monkeypatch):
    catalog = [("basic", "basic.py")]
    descriptions = {"basic": ["%"]}
    calls = []
    monkeypatch.setattr(
        loader, "list_function_files",
        lambda: calls.append("list") or list(catalog))
    monkeypatch.setattr(
        loader, "describe_function_files",
        lambda: calls.append("describe") or dict(descriptions))
    monkeypatch.setattr(
        loader, "describe_plugin_dependencies",
        lambda files=None: calls.append("dependencies") or {})
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    panel.activate()
    catalog[:] = [("solve", "solve.py")]
    descriptions.clear()
    descriptions["solve"] = ["solve"]
    monkeypatch.setattr(panel.menu, "update", lambda kb, event: "ENTER")

    panel.update(None, (3, 3, True))

    assert calls == ["list", "list", "describe", "dependencies"]
    assert panel._items[-1] == ("plugin:solve", False, False)

    panel.menu.cursor_pos = len(panel._items) - 1
    catalog[:] = []
    descriptions.clear()
    panel.update(None, (3, 3, True))

    assert panel.menu.cursor_pos == len(panel._items) - 1


def test_function_panel_queues_shared_settings_when_leaving(monkeypatch):
    settings = {"enabled_functions": ["basic", "trig", "math", "list"]}
    queued = []

    def unexpected_load_settings():
        raise AssertionError("unexpected SD read")

    monkeypatch.setattr(
        "screens.function_panel.load_settings", unexpected_load_settings)
    panel = FunctionPanel(
        None,
        request_settings=lambda value, callback=None, owner=None:
            queued.append(dict(value)),
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
    end = source.index("elif result in (", start)
    handler = source[start:end]
    helper_start = source.index("def _reload_functions_after_reclaim(")
    helper_end = source.index("def _draw_crash", helper_start)
    helper = source[helper_start:helper_end]
    panel_start = source.index("func_panel = FunctionPanel(")
    panel_end = source.index("func_panel.set_load_errors", panel_start)
    panel_init = source[panel_start:panel_end]

    assert "nav.go_back(event)" in handler
    assert "_function_reload_pending = True" in handler
    background = source[source.index("# Potentially blocking work"):
                        source.index("time.sleep_ms(IDLE_LOOP_SLEEP_MS)")]
    assert "_reload_functions_after_reclaim(" in background
    assert "nav, nav.current, settings, registry" in background
    assert "BACKGROUND_IDLE_MS" in background
    assert "return _reload_functions(settings, registry)" in helper
    assert "plugin_functions=registry.plugin_functions" in panel_init
    assert "plugin_dependencies=registry.plugin_dependencies" in panel_init
    assert "FunctionPanel(\n            None," in panel_init
    assert "func_panel.set_plugin_catalog(" in background
    assert "load_settings()" not in handler
