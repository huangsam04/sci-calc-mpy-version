from ui.sidebar import Sidebar


class DisplaySpy:
    width = 256
    height = 64

    def __init__(self):
        self.direct_text = []

    def fill_rectangle(self, *_args):
        pass

    def draw_rectangle(self, *_args):
        pass

    def draw_hline(self, *_args):
        pass

    def draw_text(self, *_args, **_kwargs):
        raise AssertionError("sidebar must not allocate cached text frames")

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct_text.append((x, y, bytes(text), font, gs))


def test_sidebar_font_path_draws_without_glyph_or_string_framebuffers():
    font = object()
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplaySpy()
    sidebar = Sidebar(font, registry)

    sidebar.draw(display, refresh=False)

    assert [call[2] for call in display.direct_text] == [
        b"BAT", b"?.?V", b"RAD"]
