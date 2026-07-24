from ui.theme import draw_footer, draw_footer_fast


class DisplaySpy:
    def __init__(self):
        self.direct = []
        self.text = []

    def fill_rectangle(self, *args):
        pass

    def draw_hline(self, *args):
        pass

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct.append((x, y, text, font, gs))

    def draw_text(self, x, y, text, font, invert=False, gs=15, raw=False):
        self.text.append((x, y, text, font, gs, raw))


class FontStub:
    def measure_text(self, text):
        return len(text) * 6


def test_fast_footer_draws_static_and_dynamic_text_directly_as_bytes():
    display = DisplaySpy()
    font = FontStub()

    draw_footer_fast(display, "Input", b"Input", font, "1/96")

    assert display.direct == [
        (3, 56, b"Input", font, 9),
        (184, 56, b"1/96", font, 15),
    ]
    assert display.text == []


def test_generic_footer_uses_allocation_free_direct_text():
    display = DisplaySpy()
    font = FontStub()

    draw_footer(display, "x:-10~10 y:-5~5", font, "8/2 zoom")

    assert display.direct == [
        (3, 56, "x:-10~10 y:-5~5", font, 9),
        (160, 56, "8/2 zoom", font, 15),
    ]
    assert display.text == []
