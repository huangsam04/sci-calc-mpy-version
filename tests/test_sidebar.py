import pytest

import ui.sidebar as sidebar_module
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


def test_sidebar_propagates_memory_error_from_lazy_adc_construction(
        monkeypatch):
    class ExhaustedADC:
        ATTN_11DB = object()

        def __init__(self, _pin):
            raise MemoryError("injected lazy adc allocation")

    monkeypatch.setattr(sidebar_module, "Pin", lambda _pin: object())
    monkeypatch.setattr(sidebar_module, "ADC", ExhaustedADC)
    sidebar = Sidebar(None, type("Registry", (), {"angle_mode": 0})())

    with pytest.raises(MemoryError, match="injected lazy adc allocation"):
        sidebar.refresh_needed(now=1)

    assert sidebar._adc is None


def test_input_draw_synchronizes_angle_cache_without_polling_adc(monkeypatch):
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplaySpy()
    sidebar = Sidebar(object(), registry)
    monkeypatch.setattr(Sidebar, "_update_battery", lambda _self, _now: False)

    sidebar.draw(display, refresh=False)
    registry.angle_mode = 1
    sidebar.draw(display, refresh=False)

    assert display.direct_text[-1][2] == b"DEG"
    assert sidebar._last_angle is True
    assert sidebar.refresh_needed(now=2) is False
