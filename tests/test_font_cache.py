from pathlib import Path

from calc.functions import build_registry
from display import xglcd_font as font_module
from display.xglcd_font import XglcdFont, emergency_reclaim, trim_caches
from screens.calculator import CalculatorScreen
from screens.stopwatch import StopwatchScreen
from ui.error_popup import ErrorPopup


SOURCE = Path(__file__).parents[1] / "source"


class FontStub:
    width = 5
    height = 7

    def measure_text(self, text, spacing=1):
        return len(text) * (5 + spacing)


class DirectOnlyDisplay:
    def __init__(self):
        self.direct = []

    def fill_rectangle(self, *args):
        pass

    def draw_rectangle(self, *args):
        pass

    def draw_hline(self, *args):
        pass

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct.append((x, y, text, font, gs))

    def draw_text(self, *args, **kwargs):
        raise AssertionError("text must use the packed direct path")


def test_packed_font_retains_no_optional_raster_cache():
    font = XglcdFont(
        str(SOURCE / "fonts" / "Bally7x9.c"), 7, 9,
        cache_bytes=160)

    offset = (ord("A") - font.start_letter) * font.bytes_per_letter
    assert font.letters[offset] == 6
    assert font.measure_text("A") == 7
    assert not hasattr(font, "_cache")
    assert trim_caches(font, None, 0) == 0


def test_font_reclaim_trims_compatible_fonts_then_collects_once(monkeypatch):
    events = []

    class Cache:
        def __init__(self, name):
            self.name = name

        def trim_cache(self, target):
            events.append((self.name, target))
            return 7

    first = Cache("first")
    second = Cache("second")
    monkeypatch.setattr(font_module.gc, "collect", lambda: events.append("gc"))

    assert trim_caches(first, second, 12) == 14
    assert emergency_reclaim(first, second) == 14
    assert events == [
        ("first", 12), ("second", 12),
        ("first", 0), ("second", 0), "gc",
    ]


def test_history_error_and_stopwatch_text_use_direct_packed_path():
    font = FontStub()
    display = DirectOnlyDisplay()

    calculator = CalculatorScreen(font, font, build_registry(), {})
    calculator._state[0] = [("x+123", 123.45)]
    calculator._ensure_history_cache(4, encoded=True)
    calculator._draw_cached_history_row(
        display, 15, 0, False, DirectOnlyDisplay.draw_text_direct)

    popup = ErrorPopup(font, font)
    popup.show("bad(x)", "Unknown variable")
    popup.draw(display)

    stopwatch = StopwatchScreen(font)
    stopwatch._clock[2][3] = [(1, 1_234)]
    stopwatch.draw(display)

    rendered = [entry[2] for entry in display.direct]
    assert b"x+123" in rendered
    assert "bad(x)" in rendered
    assert any(isinstance(text, str) and text.startswith("Lap1:")
               for text in rendered)
