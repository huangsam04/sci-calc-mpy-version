from pathlib import Path
import sys
import types

import main
import pytest
from display.xglcd_font import XglcdFont
from runtime_handle import (
    RuntimeHandle,
    get_resident_runtime,
    set_resident_runtime,
)


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


class _BootDisplay:
    height = 64

    def set_brightness(self, value):
        pass


class _BootRegistry:
    angle_mode = 0
    plugin_functions = {}
    plugin_dependencies = {}
    plugin_errors = ()


class _BootScreen:
    def __init__(self, *args, **kwargs):
        self.input_box = object()

    def activate(self):
        pass

    def add_screen(self, label, screen):
        pass

    def set_load_errors(self, errors):
        pass

    def set_display_digits(self, value):
        pass


class _BootStorage:
    def request_settings(self, settings):
        pass


class _BootNav:
    def __init__(self, display, font_small, registry):
        self.current = None
        self.memory = type("Memory", (), {"_buffers": {}})()
        self.renderer = type(
            "Renderer", (), {
                "display": display,
                "_visible_screen": None,
            })()

    def register_screens(self, screens):
        pass

    def boot(self, root):
        self.current = root
        root.activate()

    def present_current(self):
        self.renderer._visible_screen = self.current


def _install_minimal_boot_adapters(
        monkeypatch, metrics, display_power=object):
    keyboard = types.ModuleType("input.keyboard")
    keyboard.Keyboard = lambda: object()
    keyboard.get_key_label = lambda *args: ""
    storage = types.ModuleType("utils.storage")
    storage.load_settings = lambda: {
        "angle_mode": 0,
        "enabled_functions": (),
        "brightness": 100,
        "display_digits": 4,
    }
    storage.load_vars = lambda: {}
    storage.DeferredStorage = _BootStorage
    power = types.ModuleType("utils.power")
    power.AWAKE = 1
    power.WOKE = 2
    power.DisplayPower = display_power
    screen_names = (
        ("screens.main_menu", "MainMenu"),
        ("screens.calculator", "CalculatorScreen"),
        ("screens.about", "AboutScreen"),
        ("screens.letter_panel", "LetterPanel"),
        ("screens.function_picker", "FunctionPicker"),
        ("screens.variable_panel", "VariablePanel"),
        ("screens.function_panel", "FunctionPanel"),
        ("screens.plot", "PlotScreen"),
        ("screens.settings", "SettingsScreen"),
        ("screens.stopwatch", "StopwatchScreen"),
    )

    monkeypatch.setitem(sys.modules, "input.keyboard", keyboard)
    monkeypatch.setitem(sys.modules, "utils.storage", storage)
    monkeypatch.setitem(sys.modules, "utils.power", power)
    for module_name, class_name in screen_names:
        module = types.ModuleType(module_name)
        setattr(module, class_name, _BootScreen)
        monkeypatch.setitem(sys.modules, module_name, module)
    monkeypatch.setattr(main, "_init_display", _BootDisplay)
    monkeypatch.setattr(main, "_boot_progress", lambda *args: None)
    monkeypatch.setattr(main, "XglcdFont", lambda *args: object())
    monkeypatch.setattr(
        main, "_reload_functions", lambda settings: _BootRegistry())
    monkeypatch.setattr(main, "Nav", _BootNav)
    monkeypatch.setattr(main, "metrics", metrics)


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

    # Font files are slot-managed assets and must resolve against the
    # application root, not a hardcoded flat /sd path.
    assert "app_root()" in main_source
    assert "/Bally7x9.xglcd" in main_source
    assert "/Neato5x7.xglcd" in main_source


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


def test_boot_publishes_an_explicit_runtime_handle_not_metrics_state():
    main_source = (SOURCE / "main.py").read_text(encoding="utf-8")

    handle = main_source.index("RuntimeHandle(")
    ready = main_source.index('metrics.mark_boot("ui_ready")', handle)
    return_gate = main_source.index("if not run_loop:", ready)
    diagnostic_publish = main_source.index(
        "set_resident_runtime(runtime)", return_gate)
    returned = main_source.index("return runtime", return_gate)
    power = main_source.index("power = DisplayPower(", returned)
    handler = main_source.index("def _handle_event(", power)
    resident_publish = main_source.index(
        "set_resident_runtime(runtime)", handler)

    assert (handle < ready < return_gate < diagnostic_publish < returned
            < power < handler < resident_publish)
    assert "metrics.bind_runtime" not in main_source
    assert "from runtime_handle import RuntimeHandle" in main_source
    assert "from runtime_acceptance import RuntimeHandle" not in main_source


def test_failed_startup_clears_the_previous_resident_runtime(monkeypatch):
    previous = RuntimeHandle(object(), object(), (), mode="resident")
    set_resident_runtime(previous)

    def fail_display_init():
        raise RuntimeError("display startup failed")

    monkeypatch.setattr(main, "_init_display", fail_display_init)
    try:
        with pytest.raises(RuntimeError, match="display startup failed"):
            main.main(run_loop=False)

        assert get_resident_runtime() is None
    finally:
        set_resident_runtime(None)


def test_late_startup_failure_cannot_publish_a_partial_runtime(monkeypatch):
    class Metrics:
        def start_boot(self):
            pass

        def mark_boot(self, phase):
            if phase == "ui_ready":
                raise RuntimeError("late startup failed")

    _install_minimal_boot_adapters(monkeypatch, Metrics())

    previous = RuntimeHandle(object(), object(), (), mode="resident")
    set_resident_runtime(previous)
    try:
        with pytest.raises(RuntimeError, match="late startup failed"):
            main.main(run_loop=False)

        assert get_resident_runtime() is None
    finally:
        set_resident_runtime(None)


def test_power_setup_failure_cannot_publish_a_partial_runtime(monkeypatch):
    class Metrics:
        def start_boot(self):
            pass

        def mark_boot(self, phase):
            pass

    class FailingPower:
        def __init__(self, display, timeout_ms):
            raise RuntimeError("power startup failed")

    _install_minimal_boot_adapters(
        monkeypatch, Metrics(), display_power=FailingPower)

    previous = RuntimeHandle(object(), object(), (), mode="resident")
    set_resident_runtime(previous)
    try:
        with pytest.raises(RuntimeError, match="power startup failed"):
            main.main(run_loop=True)

        assert get_resident_runtime() is None
    finally:
        set_resident_runtime(None)


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
