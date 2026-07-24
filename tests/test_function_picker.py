from screens.function_picker import FunctionPicker


class FontStub:
    width = 5
    height = 7

    def measure_text(self, text, spacing=1):
        return len(text) * (self.width + spacing)


class InputStub:
    def insert_str(self, value):
        pass


class CalculatorStub:
    def __init__(self, count=32):
        registry = {
            "function_" + str(index): (None, None, "prefix")
            for index in range(count)
        }
        self.context = type("Context", (), {"registry": registry})()
        self.input_box = InputStub()


class LowMemoryDisplay:
    """Simulate the bounded string-frame cache available on the device."""

    def __init__(self, dynamic_limit=8):
        self.dynamic_limit = dynamic_limit
        self.dynamic_draws = 0
        self.direct_draws = 0

    def draw_text(self, x, y, text, font, **kwargs):
        if 15 <= y < 54:
            self.dynamic_draws += 1
            if self.dynamic_draws > self.dynamic_limit:
                raise MemoryError("function label cache exhausted")

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct_draws += 1

    def draw_hline(self, *args):
        pass

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        pass


def test_distinct_rapid_right_edges_are_never_throttled():
    picker = FunctionPicker(FontStub(), CalculatorStub())
    picker.activate()

    for _ in range(3):
        picker.update(None, (2, 2, False))

    assert picker._cursor == 12


def test_repeated_right_page_changes_do_not_grow_string_frame_cache():
    picker = FunctionPicker(FontStub(), CalculatorStub())
    display = LowMemoryDisplay()
    picker.activate()

    for _ in range(5):
        picker.draw(display)
        picker.update(None, (2, 2, False))

    assert display.dynamic_draws == 0
    assert display.direct_draws > 0
