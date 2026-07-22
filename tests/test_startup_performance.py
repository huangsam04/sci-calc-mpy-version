from pathlib import Path

import main
from display.xglcd_font import XglcdFont


SOURCE = Path(__file__).parents[1] / "source"


class SplashDisplay:
    def __init__(self):
        self.present_count = 0

    def clear_buffers(self, color=0):
        pass

    def draw_text8x8(self, *args, **kwargs):
        pass

    def draw_hline(self, *args, **kwargs):
        pass

    def draw_rectangle(self, *args, **kwargs):
        pass

    def fill_rectangle(self, *args, **kwargs):
        pass

    def draw_vline(self, *args, **kwargs):
        pass

    def present(self):
        self.present_count += 1


def test_shipped_fonts_load_despite_legacy_non_utf8_comments():
    fonts = (
        ("Bally7x9.c", 7, 9, 6),
        ("Neato5x7.c", 5, 7, 5),
        ("FixedFont5x8.c", 5, 8, 5),
    )

    for filename, font_width, font_height, letter_width in fonts:
        font = XglcdFont(str(SOURCE / "fonts" / filename),
                          font_width, font_height)
        _, width, height = font.get_letter("A")
        assert (width, height) == (letter_width, font_height)


def test_boot_uses_generated_binary_font_assets():
    main_source = (SOURCE / "main.py").read_text(encoding="utf-8")

    assert "/sd/fonts/Bally7x9.xglcd" in main_source
    assert "/sd/fonts/Neato5x7.xglcd" in main_source


def test_boot_progress_avoids_artificial_animation_delay(monkeypatch):
    display = SplashDisplay()
    delays = []
    monkeypatch.setattr(main, "_boot_fill_w", 0)
    monkeypatch.setattr(main, "_boot_title_gs", 0)
    monkeypatch.setattr(main.time, "sleep_ms", delays.append)

    for step in range(1, 9):
        main._boot_progress(display, step, 8, "Loading...")

    assert display.present_count <= 8
    assert delays == []


def test_accepted_input_bypasses_idle_render_throttle(monkeypatch):
    monkeypatch.setattr(main.time, "ticks_diff", lambda newer, older: newer - older)

    assert main._needs_render(10, 0, False, True, False, True) is True
    assert main._needs_render(10, 0, False, True, False, False) is False
