from pathlib import Path
import sys
import types

import main
import pytest
from display.xglcd_font import XglcdFont
from runtime_handle import (
    get_resident_runtime,
    set_resident_runtime,
)
from runtime_materialize import RuntimeHandle


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

    def __init__(self):
        self.sleep_calls = 0

    def set_brightness(self, value):
        pass

    def sleep(self):
        self.sleep_calls += 1


class _BootRegistry:
    angle_mode = 0
    plugin_functions = {}
    plugin_dependencies = {}
    plugin_files = ()
    plugin_errors = ()


class _BootMenu:
    cursor = object()


class _BootScreen:
    menu = _BootMenu()

    def __init__(self, *args, **kwargs):
        self.input_box = object()
        self._state = [None] * 8

    def activate(self):
        pass

    def _reserve_menu_rows(self):
        pass

    def _build_rows(self):
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

    def detach_callbacks(self, owner):
        pass


class _BootNav:
    def __init__(self, display, font_small, registry):
        self.current = None
        self.stack = []
        self.page_context = None
        self.memory = type(
            "Memory", (), {"_buffers": {}, "_plot_curve": bytearray(104)})()
        self.renderer = type(
            "Renderer", (), {
                "display": display,
                "_visible_screen": None,
            })()

    def configure_pages(self, *context):
        self.page_context = context

    def boot(self, root):
        self.current = root
        self.stack[:] = [root]
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
        ("screens.variable_panel", "VariablePanel"),
        ("screens.function_picker", "FunctionPicker"),
        ("screens.about", "AboutScreen"),
        ("screens.letter_panel", "LetterPanel"),
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
        if module_name == "screens.stopwatch":
            module.STOPWATCH_FRAME_MS = 50
        monkeypatch.setitem(sys.modules, module_name, module)
    monkeypatch.setattr(main, "_init_display", _BootDisplay)
    monkeypatch.setattr(main, "_boot_progress", lambda *args: None)
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
        offset = (ord("A") - font.start_letter) * font.bytes_per_letter
        assert (font.letters[offset], font.height) == (
            letter_width, font_height)


def test_boot_uses_the_bounded_builtin_font_path():
    main_source = (SOURCE / "main.py").read_text(encoding="utf-8")

    assert "font_main = None" in main_source
    assert "font_small = None" in main_source
    assert "XglcdFont(" not in main_source


def test_boot_releases_the_rebuildable_function_loader(monkeypatch):
    calc_package = types.ModuleType("calc")
    loader_module = types.ModuleType("calc.loader")
    app_root_module = types.ModuleType("approot")
    calc_package.loader = loader_module
    monkeypatch.setitem(sys.modules, "calc", calc_package)
    monkeypatch.setitem(sys.modules, "calc.loader", loader_module)
    monkeypatch.setitem(sys.modules, "approot", app_root_module)
    monkeypatch.setattr(main.gc, "mem_free", lambda: 1, raising=False)
    for helper_name in (
            "_init_display", "_boot_progress", "_boot_fail",
            "_release_function_loader"):
        # Device boot deliberately deletes these globals.  Record them with
        # monkeypatch so the host module is restored for the remaining tests.
        monkeypatch.setattr(main, helper_name, getattr(main, helper_name))

    main._release_function_loader()

    assert "calc.loader" not in sys.modules
    assert "approot" not in sys.modules
    assert not hasattr(calc_package, "loader")


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


def test_boot_preloads_frozen_code_but_leaves_page_instances_to_nav():
    main_source = (SOURCE / "main.py").read_text(encoding="utf-8")
    main_body = main_source[main_source.index("def main("):]
    boot = main_body[:main_body.index('metrics.mark_boot("ui_ready")')]

    assert "from screens.main_menu import MainMenu" in boot
    assert "nav.configure_pages(" in boot
    for name in (
            "calculator", "plot", "function_panel", "stopwatch", "settings",
            "about", "letter_panel", "function_picker", "variable_panel"):
        assert "from screens." + name + " import" not in boot
        assert '__import__("screens.' + name + '")' in boot
    assert "def _build_page" in main_source


def test_boot_uses_binding_normally_and_builds_a_diagnostic_handle_on_request():
    main_source = (SOURCE / "main.py").read_text(encoding="utf-8")

    binding = main_source.index("application_binding = ApplicationBinding(")
    handle = main_source.index(
        "runtime = application_binding if run_loop else RuntimeHandle(",
        binding)
    ready = main_source.index('metrics.mark_boot("ui_ready")', handle)
    first_frame = main_source.index(
        "_present_first_ui_frame(nav, main_menu)", ready)
    return_gate = main_source.index("if not run_loop:", first_frame)
    diagnostic_publish = main_source.index(
        "set_resident_runtime(runtime)", handle)
    returned = main_source.index("return runtime", return_gate)
    power = main_source.index("power = DisplayPower(", returned)
    handler = main_source.index("def _handle_event(", power)
    resident_publish = main_source.index(
        '__import__("runtime_handle")._resident_runtime = runtime',
        handler)

    assert (binding < handle < ready < first_frame < return_gate
            < diagnostic_publish < returned < power < handler
            < resident_publish)
    assert "metrics.bind_runtime" not in main_source
    assert "from runtime_materialize import RuntimeHandle" in main_source
    assert "if not run_loop:" in main_source[:binding]
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


def test_function_startup_memory_error_is_not_converted_to_a_fallback(
        monkeypatch):
    class Metrics:
        def start_boot(self):
            pass

        def mark_boot(self, phase):
            pass

    _install_minimal_boot_adapters(monkeypatch, Metrics())

    def exhaust_heap(settings):
        raise MemoryError("injected function startup")

    monkeypatch.setattr(main, "_reload_functions", exhaust_heap)
    monkeypatch.setattr(
        main, "_boot_fail",
        lambda *args: pytest.fail("boot fallback must not handle MemoryError"))

    with pytest.raises(MemoryError, match="injected function startup"):
        main.main(run_loop=False)


def test_page_startup_memory_error_sleeps_oled_before_reraising(monkeypatch):
    class Metrics:
        def start_boot(self):
            pass

        def mark_boot(self, phase):
            pass

    _install_minimal_boot_adapters(monkeypatch, Metrics())
    display = _BootDisplay()
    failure = MemoryError("injected page startup")

    class FailingMainMenu:
        def __init__(self, *args, **kwargs):
            raise failure

    monkeypatch.setattr(main, "_init_display", lambda: display)
    monkeypatch.setattr(
        sys.modules["screens.main_menu"],
        "MainMenu", FailingMainMenu)

    with pytest.raises(MemoryError) as caught:
        main.main(run_loop=False)

    assert caught.value is failure
    assert display.sleep_calls == 1


def test_late_page_startup_memory_error_sleeps_oled_before_reraising(
        monkeypatch):
    class Metrics:
        def start_boot(self):
            pass

        def mark_boot(self, phase):
            pass

    _install_minimal_boot_adapters(monkeypatch, Metrics())
    display = _BootDisplay()
    failure = MemoryError("injected late page startup")

    class FailingMainMenu(_BootScreen):
        def add_screen(self, label, screen):
            raise failure

    monkeypatch.setattr(main, "_init_display", lambda: display)
    monkeypatch.setattr(
        sys.modules["screens.main_menu"], "MainMenu", FailingMainMenu)

    with pytest.raises(MemoryError) as caught:
        main.main(run_loop=False)

    assert caught.value is failure
    assert display.sleep_calls == 1


def test_first_ui_frame_memory_error_sleeps_oled_before_reraising(
        monkeypatch):
    class Metrics:
        def start_boot(self):
            pass

        def mark_boot(self, phase):
            pass

    _install_minimal_boot_adapters(monkeypatch, Metrics())
    display = _BootDisplay()
    failure = MemoryError("injected first UI frame")

    monkeypatch.setattr(main, "_init_display", lambda: display)

    def fail_first_frame(nav, root):
        raise failure

    monkeypatch.setattr(main, "_present_first_ui_frame", fail_first_frame)

    with pytest.raises(MemoryError) as caught:
        main.main(run_loop=False)

    assert caught.value is failure
    assert display.sleep_calls == 1


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


def test_runtime_keeps_only_root_and_lazy_page_context(
        monkeypatch):
    class Metrics:
        def start_boot(self):
            pass

        def mark_boot(self, phase):
            pass

    _install_minimal_boot_adapters(monkeypatch, Metrics())

    runtime = main.main(run_loop=False, publish_runtime=False)

    assert runtime.nav.stack == [runtime.root]
    assert runtime.targets == ()
    assert runtime.application_binding.root is runtime.root
    assert runtime.nav.page_context is not None
    assert runtime.optional_buffer_target is runtime.nav.memory


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


def test_boot_progress_does_not_render_internal_operation_details(monkeypatch):
    display = SplashDisplay()
    delays = []
    monkeypatch.setattr(main.time, "sleep_ms", delays.append)

    main._boot_progress(
        display, 3, 8, "Loading settings...", "load_settings()")

    assert not any(value == "load_settings()"
                   for _, _, value in display.text)
    assert not any("import" in value.lower()
                   for _, _, value in display.text)
    assert delays == []


def test_screen_import_detail_is_not_shown_during_boot():
    main_source = (SOURCE / "main.py").read_text(encoding="utf-8")

    assert '_boot_progress(display, 6, 8, "Loading screens...")' in main_source
    assert '"import screens.*"' not in main_source


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

    class Plot:
        def __init__(self):
            self.invalidations = 0

        def on_angle_mode_changed(self):
            self.invalidations += 1

    class Nav:
        def __init__(self, plot):
            self.plot = plot

        def find_page(self, page_id):
            assert page_id == main.PAGE_PLOT
            return self.plot

    registry = Registry()
    settings = {}
    persistence = Persistence()
    renderer = Renderer()
    plot = Plot()

    main._toggle_angle_mode(
        registry, settings, persistence, renderer, Nav(plot))

    assert registry.angle_mode == 1
    assert settings["angle_mode"] == 1
    assert persistence.saved is settings
    assert renderer.invalidations == 1
    assert plot.invalidations == 1


def test_context_letter_routing_respects_modal_and_visible_editor_contracts():
    class Screen:
        def __init__(self, modal=False, target=None):
            self.modal = modal
            self.target = target

        def blocks_global_shortcuts(self):
            return self.modal

        def letter_input_target(self):
            return self.target

    class Nav:
        def __init__(self):
            self.calls = []

        def open(self, page_id, event):
            self.calls.append((page_id, event))

    target = object()
    nav = Nav()

    assert main._open_context_letter_panel(
        Screen(target=target), (3, 5, True), nav) is True
    assert nav.calls == [(main.PAGE_LETTERS, (3, 5, True))]

    assert main._open_context_letter_panel(
        Screen(modal=True, target=target), (3, 5, True), nav) is False
    assert main._open_context_letter_panel(
        Screen(target=None), (3, 5, True), nav) is False


def test_global_angle_waits_for_non_modal_page_context_to_decline_the_event():
    angle = (4, 4, False)

    assert main._global_angle_allowed(angle, False, None) is True
    assert main._global_angle_allowed(angle, True, None) is False
    assert main._global_angle_allowed(angle, False, "REDRAW") is False
