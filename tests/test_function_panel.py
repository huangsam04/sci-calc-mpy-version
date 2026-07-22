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
