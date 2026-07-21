from screens.function_panel import FunctionPanel


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
