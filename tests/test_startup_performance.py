from pathlib import Path

import main
from display.xglcd_font import XglcdFont


SOURCE = Path(__file__).parents[1] / "source"


class SplashDisplay:
    def __init__(self):
        self.present_count = 0
        self.text = []

    def clear_buffers(self, color=0):
        pass

    def draw_text8x8(self, x, y, value, **kwargs):
        self.text.append((x, y, value))

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


def test_boot_presents_core_frame_before_run_loop_can_return():
    main_source = (SOURCE / "main.py").read_text(encoding="utf-8")

    nav_created = main_source.index("nav = Nav(display, font_small, registry)")
    screens = main_source.index("from screens.main_menu import MainMenu")
    first_frame = main_source.index(
        "_present_first_ui_frame(nav, main_menu)", screens)
    return_gate = main_source.index("if not run_loop:", first_frame)

    assert nav_created < screens < first_frame < return_gate
    assert "transition_buffers" not in main_source
    assert "ui.residency" not in main_source
    assert "lazy_screen" not in main_source


def test_boot_progress_avoids_artificial_animation_delay(monkeypatch):
    display = SplashDisplay()
    delays = []
    monkeypatch.setattr(main.time, "sleep_ms", delays.append)

    for step in range(1, 9):
        main._boot_progress(display, step, 8, "Loading...")

    assert display.present_count <= 8
    assert delays == []


def test_boot_progress_shows_the_actual_operation(monkeypatch):
    display = SplashDisplay()
    delays = []
    monkeypatch.setattr(main.time, "sleep_ms", delays.append)

    main._boot_progress(
        display, 3, 8, "Loading settings...", "load_settings()")

    assert any(value == "(load_settings())"
               for _, _, value in display.text)
    assert delays == []


def test_accepted_input_bypasses_idle_render_throttle(monkeypatch):
    monkeypatch.setattr(main.time, "ticks_diff", lambda newer, older: newer - older)

    assert main._needs_render(10, 0, True, False, True) is True
    assert main._needs_render(10, 0, True, False, False) is False


def test_held_direction_key_keeps_requesting_page_updates():
    class Keyboard:
        def is_pressed(self, row, col):
            return (row, col) == (3, 1)

    assert main._page_update_requested(Keyboard(), None) is True


def test_sidebar_refresh_deadline_invalidates_without_polling_each_frame(
        monkeypatch):
    class Renderer:
        def __init__(self):
            self.invalidations = 0

        def invalidate_sidebar(self):
            self.invalidations += 1

    monkeypatch.setattr(
        main.time, "ticks_diff", lambda newer, older: newer - older)
    renderer = Renderer()

    assert main._refresh_sidebar_if_due(renderer, 4999, 0) == 0
    assert renderer.invalidations == 0
    assert main._refresh_sidebar_if_due(renderer, 5000, 0) == 5000
    assert renderer.invalidations == 1


def test_angle_toggle_immediately_invalidates_sidebar():
    class Registry:
        angle_mode = 0

    class Persistence:
        def __init__(self):
            self.saved = None

        def request_settings(self, settings):
            self.saved = settings

    class Renderer:
        def __init__(self):
            self.invalidations = 0

        def invalidate_sidebar(self):
            self.invalidations += 1

    registry = Registry()
    settings = {}
    persistence = Persistence()
    renderer = Renderer()

    main._toggle_angle_mode(registry, settings, persistence, renderer)

    assert registry.angle_mode == 1
    assert settings["angle_mode"] == 1
    assert persistence.saved is settings
    assert renderer.invalidations == 1
