# SCI-CALC MicroPython Firmware
# Main entry point — display init FIRST, then lazy-load everything else
"""SCI-CALC: Multifunctional Scientific Calculator (MicroPython Edition)."""
import time
import gc
from machine import Pin, SPI

# --- Minimal imports for splash screen ---
from display.ssd1322 import Display as SSD1322
from display.xglcd_font import XglcdFont
from ui.motion import IDLE_FRAME_MS, IDLE_LOOP_SLEEP_MS, SLEEP_SCAN_MS
from version import VERSION
from performance import metrics

# SPI pins for display
SPI_CLK = 18
SPI_DATA = 23
SPI_CS = 5
SPI_DC = 16
SPI_RESET = 17
INPUT_BATCH_LIMIT = 5
BACKGROUND_IDLE_MS = 750
SIDEBAR_REFRESH_MS = 5000


def _init_display():
    """Initialize SSD1322 display — called FIRST for fast splash."""
    spi = SPI(2,
              baudrate=10_000_000,
              polarity=0, phase=0, bits=8,
              sck=Pin(SPI_CLK), mosi=Pin(SPI_DATA))
    cs = Pin(SPI_CS, Pin.OUT)
    dc = Pin(SPI_DC, Pin.OUT)
    rst = Pin(SPI_RESET, Pin.OUT)
    return SSD1322(spi, cs, dc, rst)


def _boot_progress(display, step, total, label="", operation=""):
    """Draw one truthful, allocation-light boot checkpoint."""
    bar_x, bar_y, bar_w, bar_h = 20, 34, 216, 5
    fill_w = (bar_w - 2) * step // max(1, total)
    display.clear_buffers(0)
    display.draw_text8x8(96, 10, "SCI-CALC", gs=15)
    display.draw_hline(60, 20, 136, 6)
    display.draw_rectangle(bar_x, bar_y, bar_w, bar_h, 6)
    if fill_w:
        display.fill_rectangle(
            bar_x + 1, bar_y + 1, fill_w, bar_h - 2, 15)
    if label:
        display.draw_text8x8(20, 44, label, gs=10)
    progress = str(step) + "/" + str(total)
    display.draw_text8x8(
        210 - len(progress) * 8, 44, progress, gs=8)
    if operation:
        detail = "(" + operation + ")"
        if len(detail) > 29:
            detail = detail[:28] + "~"
        display.draw_text8x8(12, 54, detail, gs=7)
    display.present()


def _boot_fail(display, step, total, label, error):
    """Show boot error briefly, then continue with fallback."""
    bar_x, bar_y, bar_w, bar_h = 20, 34, 216, 5
    fill_w = int((bar_w - 2) * step / total)

    err_str = str(error)
    if len(err_str) > 28:
        err_str = err_str[:27] + "~"

    display.clear_buffers(0)
    display.draw_text8x8(96, 10, "SCI-CALC", gs=15)
    display.draw_hline(60, 20, 136, 6)

    # Progress bar at current step
    display.draw_rectangle(bar_x, bar_y, bar_w, bar_h, 6)
    if fill_w > 0:
        display.fill_rectangle(bar_x + 1, bar_y + 1, fill_w, bar_h - 2, 15)

    # Error
    display.draw_text8x8(16, 44, f"FAIL: {label}", gs=15)
    display.draw_text8x8(16, 52, err_str, gs=10)
    display.present()
    time.sleep(2)


def _needs_render(now, last_render, dirty, stopwatch_running, input_changed):
    """Decide whether the current loop should submit a full display frame."""
    if input_changed:
        return True
    elapsed = time.ticks_diff(now, last_render)
    return (elapsed >= IDLE_FRAME_MS
            and (dirty or stopwatch_running
                 or elapsed >= SIDEBAR_REFRESH_MS))


def _drain_input_batch(nav, keyboard, handler):
    """Dispatch at most five queued edges before the next display write."""
    count = 0
    while count < INPUT_BATCH_LIMIT:
        event = nav.poll_event(keyboard)
        if event is None:
            break
        handler(event)
        count += 1
    return count


def _page_update_requested(keyboard, event):
    """Keep menu direction hold-repeat alive without repeating calculator input."""
    return (event is not None
            or keyboard.is_pressed(0, 0)
            or keyboard.is_pressed(4, 3)
            or keyboard.is_pressed(1, 1)
            or keyboard.is_pressed(3, 1))


def _refresh_sidebar_if_due(renderer, now, last_refresh):
    """Invalidate slow status pixels independently from page rendering."""
    if time.ticks_diff(now, last_refresh) < SIDEBAR_REFRESH_MS:
        return last_refresh
    renderer.invalidate_sidebar()
    return now


def _toggle_angle_mode(registry, settings, persistence, renderer):
    """Update calculation state and its sidebar pixels in one input step."""
    registry.angle_mode = 1 - registry.angle_mode
    settings["angle_mode"] = registry.angle_mode
    renderer.invalidate_sidebar()
    persistence.request_settings(settings)


def _reload_functions(settings, registry=None):
    from calc.functions import build_registry, DEFAULT_ENABLED_GROUPS, FUNCTION_GROUPS
    from calc.loader import load_function_files
    enabled = settings.get("enabled_functions", DEFAULT_ENABLED_GROUPS)
    groups = [g for g in enabled if g in FUNCTION_GROUPS]
    sd_names = [g[7:] for g in enabled if g.startswith("plugin:")]
    if registry is None:
        staged = build_registry(groups)
        if sd_names:
            report = load_function_files(staged, sd_names)
            staged.plugin_errors = report.errors
            staged.plugin_functions = getattr(report, "functions", {})
            staged.plugin_dependencies = getattr(report, "dependencies", {})
        return staged

    # The existing registry owns the previous plug-in callbacks and their
    # module namespaces.  Drop those references before compiling replacements;
    # otherwise both plug-in generations coexist at the allocation peak and a
    # fragmented ESP32 heap can fail even when total free memory looks ample.
    angle_mode = registry.angle_mode
    registry.clear()
    gc.collect()

    # Keep the registry identity stable: calculator and plot screens retain
    # references to this object and use its revision to invalidate caches.
    staged = build_registry(groups)
    registry.replace(staged)
    registry.angle_mode = angle_mode
    if sd_names:
        report = load_function_files(registry, sd_names)
        registry.plugin_errors = report.errors
        registry.plugin_functions = getattr(report, "functions", {})
        registry.plugin_dependencies = getattr(report, "dependencies", {})
    return registry


def _reload_functions_after_reclaim(nav, active_screen, settings, registry):
    """Reload plug-ins only after reclaiming the optional plot workspace."""
    nav.prepare_memory_intensive_operation(active_screen)
    return _reload_functions(settings, registry)


def _present_first_ui_frame(nav, root):
    """Replace the 8/8 splash before diagnostics may return."""
    nav.boot(root)
    nav.present_current()


def _draw_crash(display, error):
    """Render crash info directly to display. Uses only 8x8 text — no font dependency."""
    import sys
    try:
        sys.print_exception(error)
    except Exception:
        pass

    display.clear_buffers(0)
    display.fill_rectangle(0, 0, 210, 64, 3)
    display.draw_rectangle(4, 2, 202, 60, 15)

    err_type = type(error).__name__
    if len(err_type) > 26:
        err_type = err_type[:25] + "~"
    display.draw_text8x8(8, 5, "CRASH: " + err_type, gs=15)

    # Word-wrap message across 4 lines, 25 chars each
    msg = str(error) or "(no message)"
    y = 18
    for _ in range(4):
        if not msg:
            break
        chunk = msg[:25]
        if len(msg) > 25:
            space = msg.rfind(' ', 0, 25)
            if space > 10:
                chunk = msg[:space]
                msg = msg[space:].lstrip()
            else:
                msg = msg[25:]
        else:
            msg = ""
        display.draw_text8x8(8, y, chunk, gs=15)
        y += 10

    display.draw_text8x8(8, 54, "Press any key to restart", gs=10)
    display.present()


# ── Screen navigation ───────────────────────────────────────────

class Nav:
    """Immediate navigation across resident pages."""

    __slots__ = (
        "memory", "renderer", "stack", "_managed", "_input_locked",
        "_collect_pending", "last_present_us")

    def __init__(self, display, font_small, registry, memory=None):
        from ui.memory import MemoryManager
        from ui.renderer import Renderer
        from ui.sidebar import Sidebar
        self.memory = memory or MemoryManager()
        self.renderer = Renderer(
            display, Sidebar(font_small, registry), memory=self.memory)
        self.stack = []
        self._managed = ()
        self._input_locked = False
        self._collect_pending = False
        self.last_present_us = 0

    def register_screens(self, screens):
        self._managed = tuple(screens)

    @property
    def current(self):
        return self.stack[-1]

    def boot(self, screen):
        self.stack[:] = [screen]
        screen.activate()

    def go_to(self, screen, trigger_event=None):
        if screen is self.current:
            return
        old = self.current
        old.deactivate()
        self.stack.append(screen)
        try:
            screen.activate()
        except Exception:
            self.stack.pop()
            old.activate()
            raise

    def go_back(self, trigger_event=None):
        if len(self.stack) <= 1:
            return
        old = self.stack.pop()
        old.deactivate()
        released = False
        if getattr(old, "requires_plot_workspace", False):
            releaser = getattr(old, "release_memory", None)
            if releaser is not None:
                released = bool(releaser())
            released = self.memory.release_plot_workspace() or released
        if released:
            self._collect_pending = True
        self.current.activate()

    def poll_event(self, keyboard):
        if self._input_locked:
            if keyboard.any_pressed():
                return None
            self._input_locked = False
        return keyboard.pop_key_event()

    def present_current(self):
        self.renderer.present(self.current)
        self.last_present_us = self.renderer.last_present_us

    def settle_current(self):
        return self.current.settle_step() or 0

    def prepare_memory_intensive_operation(self, active_screen):
        for screen in self._managed:
            if screen is active_screen:
                continue
            releaser = getattr(screen, "release_memory", None)
            if releaser is not None:
                releaser()
        self.memory.release_plot_workspace()
        self.memory.collect()
        self._collect_pending = False

    def collect_pending(self):
        if not self._collect_pending:
            return False
        self.memory.collect()
        self._collect_pending = False
        return True

    def reset(self, root):
        for screen in self._managed:
            releaser = getattr(screen, "release_memory", None)
            if releaser is not None:
                releaser()
        self.stack[:] = [root]
        self.memory.release_plot_workspace()
        self.memory.collect()
        self._collect_pending = False
        self.renderer.invalidate()
        self._input_locked = True
        root.activate()


def main(run_loop=True, runtime_mode="resident", publish_runtime=True):
    from runtime_handle import RuntimeHandle, set_resident_runtime
    if publish_runtime:
        set_resident_runtime(None)

    # ============================================================
    # Phase 1: Display FIRST — show splash immediately
    # ============================================================
    metrics.start_boot()
    display = _init_display()
    metrics.mark_boot("display")
    _boot_progress(
        display, 1, 8, "Loading keyboard...", "Keyboard()")

    # ============================================================
    # Phase 2: Build the resident interface while showing progress.
    # Each step is wrapped — failure shows error on screen, then
    # continues with a fallback so the calculator still boots.
    # ============================================================

    # Keyboard (critical — halt on failure)
    try:
        from input.keyboard import Keyboard, get_key_label
        kb = Keyboard()
        _boot_progress(
            display, 2, 8, "Loading fonts...", "XglcdFont(/sd/fonts)")
    except Exception as e:
        _boot_fail(display, 2, 8, "Keyboard", e)
        raise  # can't run without keyboard
    metrics.mark_boot("keyboard")

    # Fonts (fallback: built-in 8x8 font via draw_text8x8)
    try:
        font_main = XglcdFont("/sd/fonts/Bally7x9.xglcd", 7, 9)
    except Exception as e:
        _boot_fail(display, 3, 8, "Fonts", e)
        font_main = None
    try:
        font_small = XglcdFont("/sd/fonts/Neato5x7.xglcd", 5, 7)
    except Exception:
        font_small = None
    metrics.mark_boot("fonts")
    _boot_progress(
        display, 3, 8, "Loading settings...", "load_settings()")

    # Settings (fallback: defaults)
    try:
        from utils.storage import load_settings
        settings = load_settings()
        _boot_progress(
            display, 4, 8, "Loading variables...", "load_vars()")
    except Exception as e:
        _boot_fail(display, 4, 8, "Settings", e)
        settings = {"angle_mode": 0, "enabled_functions": ["basic", "trig", "math", "list"], "diagnostics": False, "brightness": 100, "display_digits": 4}
    display.set_brightness(settings.get("brightness", 100))
    metrics.mark_boot("settings")
    # Variables (fallback: empty dict)
    try:
        from utils.storage import load_vars
        vars_dict = load_vars()
        _boot_progress(
            display, 5, 8, "Loading functions...", "_reload_functions()")
    except Exception as e:
        _boot_fail(display, 5, 8, "Vars", e)
        vars_dict = {}
    metrics.mark_boot("variables")

    # Functions (fallback: built-in groups only)
    try:
        registry = _reload_functions(settings)
        registry.angle_mode = settings.get("angle_mode", 0)
        _boot_progress(
            display, 6, 8, "Loading screens...", "import screens.*")
    except Exception as e:
        _boot_fail(display, 6, 8, "Functions", e)
        from calc.functions import build_registry
        registry = build_registry(["basic", "trig", "math", "list"])
        registry.angle_mode = settings.get("angle_mode", 0)
    metrics.mark_boot("functions")

    from utils.power import AWAKE, WOKE, DisplayPower

    nav = Nav(display, font_small, registry)

    # Screens (import + build — skip broken ones)
    try:
        from screens.main_menu import MainMenu
        from screens.calculator import CalculatorScreen
        from screens.about import AboutScreen
        from screens.letter_panel import LetterPanel
        from screens.function_picker import FunctionPicker
        from screens.variable_panel import VariablePanel
        from screens.function_panel import FunctionPanel
        from screens.plot import PlotScreen
        from screens.settings import SettingsScreen
        from screens.stopwatch import StopwatchScreen
        _boot_progress(
            display, 7, 8, "Building interface...", "construct screens")
    except Exception as e:
        _boot_fail(display, 7, 8, "Screens", e)
        # If imports failed, we can't continue — the error screen already showed
        raise
    metrics.mark_boot("screen_imports")

    try:
        from utils.storage import DeferredStorage
        persistence = DeferredStorage()
        calc_screen = CalculatorScreen(
            font_main, font_small, registry, vars_dict,
            display_digits=settings.get("display_digits", 4))
        # Auxiliary pages use the display's built-in 8x8 font. This avoids SD
        # glyph reads and cache growth on the latency-sensitive input path.
        about = AboutScreen(None, VERSION)
        letter_panel = LetterPanel(None, calc_screen.input_box)
        func_picker = FunctionPicker(None, calc_screen)
        var_panel = VariablePanel(None, calc_screen)
        settings_screen = SettingsScreen(
            None, display, settings, about,
            request_save=persistence.request_settings,
            on_display_digits_change=calc_screen.set_display_digits)
        func_panel = FunctionPanel(
            None, request_settings=persistence.request_settings,
            settings=settings,
            plugin_functions=registry.plugin_functions,
            plugin_dependencies=registry.plugin_dependencies)
        func_panel.set_load_errors(registry.plugin_errors)
        # Build dynamic menus during boot, never on the first input frame.
        func_picker.activate()
        func_panel.activate()
        stopwatch = StopwatchScreen(None)
        plot_screen = PlotScreen(
            None, None, registry, memory=nav.memory)

        main_menu = MainMenu(None)
        main_menu.add_screen("Calculator", calc_screen)
        main_menu.add_screen("Plot", plot_screen)
        main_menu.add_screen("Function Panel", func_panel)
        main_menu.add_screen("Stopwatch", stopwatch)
        main_menu.add_screen("Settings", settings_screen)
    except Exception as e:
        _boot_fail(display, 7, 8, "Init", e)
        raise

    _boot_progress(
        display, 8, 8, "Starting SCI-CALC...",
        "_present_first_ui_frame()")

    # ============================================================
    # Phase 3: Main loop
    # ============================================================
    from ui.element import (
        SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW, UIElement)

    runtime_targets = (
        calc_screen, plot_screen, func_panel, stopwatch, settings_screen)
    nav.register_screens(runtime_targets)
    from ui.memory import plot_curve_buffer_size
    runtime = RuntimeHandle(
        nav,
        main_menu,
        runtime_targets,
        mode=runtime_mode,
        version=VERSION,
        optional_buffers=(
            ("plot_curve", plot_curve_buffer_size(display.height)),
        ),
        optional_buffer_target=plot_screen,
    )
    try:
        _present_first_ui_frame(nav, main_menu)
    except Exception as e:
        _draw_crash(display, e)
        raise
    metrics.mark_boot("ui_ready")
    if not run_loop:
        if publish_runtime:
            set_resident_runtime(runtime)
        return runtime
    _frame = 0
    _last_render = time.ticks_ms()
    _last_input = _last_render
    _last_sidebar_refresh = _last_render
    diagnostics = bool(settings.get("diagnostics", False))
    _diag_last = time.ticks_ms()
    _diag_render_us = 0
    _diag_present_us = 0
    _diag_frames = 0
    _dirty = False
    _function_reload_pending = False
    power = DisplayPower(
        display, int(settings.get("sleep_timeout_s", 180)) * 1000)

    def _handle_event(event):
        nonlocal _function_reload_pending, _last_sidebar_refresh
        cur = nav.current
        if event is not None:
            if diagnostics:
                metrics.record_input()
                print("INPUT page=" + cur.__class__.__name__
                      + " row=" + str(event[0])
                      + " col=" + str(event[1])
                      + " shift=" + str(int(event[2]))
                      + " key=" + get_key_label(
                          event[0], event[1], event[2]))
            erow, ecol, eshift = event
            if ((erow, ecol) == (3, 5) and eshift
                    and (cur is calc_screen or cur is plot_screen)):
                input_box = (calc_screen.input_box
                             if cur is calc_screen
                             else plot_screen.input_box)
                if input_box is not None:
                    letter_panel.input_box = input_box
                    nav.go_to(letter_panel, event)
                    return True
            if (erow, ecol) == (4, 4):
                _toggle_angle_mode(
                    registry, settings, persistence, nav.renderer)
                _last_sidebar_refresh = time.ticks_ms()
                return True
        elif not _page_update_requested(kb, None):
            return False

        result = cur.update(kb, event)
        if diagnostics and result is not None:
            print("ACTION page=" + cur.__class__.__name__
                  + " result=" + str(result))

        if result == "BACK":
            nav.go_back(event)
        elif result == "FUNC_PANEL_DONE":
            nav.go_back(event)
            _function_reload_pending = True
        elif result in (
                "FUNC_PICKER_DONE", "LETTER_DONE", "VAR_PANEL_DONE",
                "FUNC_PANEL_CANCEL"):
            nav.go_back(event)
        elif result == "FUNC_PICKER":
            nav.go_to(func_picker, event)
        elif result == "VARIABLE_PANEL":
            nav.go_to(var_panel, event)
        elif isinstance(result, UIElement) and result is not cur:
            nav.go_to(result, event)
        return event is not None or result is not None

    if publish_runtime:
        set_resident_runtime(runtime)
    while True:
        try:
            kb.scan()
            now = time.ticks_ms()
            power_state = power.update(now, kb.any_pressed())
            if power_state != AWAKE:
                kb.discard_pending_events()
                if power_state == WOKE:
                    _dirty = True
                    _last_render = time.ticks_add(now, -500)
                    nav.renderer.invalidate_sidebar()
                    _last_sidebar_refresh = now
                # Matrix keys cannot wake ESP32 deep sleep reliably, so keep a
                # low-cost scan loop while the OLED controller is asleep.
                time.sleep_ms(SLEEP_SCAN_MS)
                continue

            _frame += 1
            batch_count = _drain_input_batch(nav, kb, _handle_event)
            hold_changed = False
            if batch_count == 0:
                hold_changed = _handle_event(None)
            input_changed = bool(batch_count or hold_changed)
            now = time.ticks_ms()
            if input_changed:
                _last_input = now
                _dirty = True

            cur = nav.current
            sidebar_refresh = _refresh_sidebar_if_due(
                nav.renderer, now, _last_sidebar_refresh)
            if sidebar_refresh != _last_sidebar_refresh:
                _last_sidebar_refresh = sidebar_refresh
                _dirty = True
            needs_render = _needs_render(
                now, _last_render, _dirty,
                (cur is stopwatch and stopwatch._running),
                input_changed)

            if needs_render:
                _last_render = now
                render_started = time.ticks_us()
                nav.present_current()
                _diag_present_us += nav.last_present_us
                render_elapsed = time.ticks_diff(time.ticks_us(), render_started)
                _diag_render_us += render_elapsed
                if diagnostics:
                    metrics.record_frame(render_elapsed)
                _diag_frames += 1
                _dirty = False
                # Capture edges that occurred during the OLED transfer before
                # any GC, SD write or lazy rebuild is allowed to start.
                kb.scan()
                now = time.ticks_ms()

            if diagnostics and time.ticks_diff(now, _diag_last) >= 5000:
                heap_free = gc.mem_free() if hasattr(gc, "mem_free") else -1
                divisor = max(1, _diag_frames)
                print("PERF frames=" + str(_diag_frames)
                      + " render_us=" + str(_diag_render_us // divisor)
                      + " present_us=" + str(_diag_present_us // divisor)
                      + " heap_free=" + str(heap_free))
                _diag_last = now
                _diag_render_us = 0
                _diag_present_us = 0
                _diag_frames = 0

            if calc_screen.context.dirty and calc_screen.context.consume_dirty():
                persistence.request_vars(calc_screen.vars)

            quiet = (batch_count == 0
                     and not hold_changed
                     and not kb.has_pending_events()
                     and not kb.any_pressed())
            settle_flags = nav.settle_current() if quiet else 0
            if settle_flags & SETTLE_COLLECT:
                gc_started = time.ticks_us()
                gc.collect()
                if diagnostics:
                    metrics.record_gc(
                        time.ticks_diff(time.ticks_us(), gc_started))
            if settle_flags & SETTLE_REDRAW:
                _dirty = True

            # Potentially blocking work gets a grace period after input.
            if (quiet
                    and not (settle_flags & SETTLE_MORE)
                    and time.ticks_diff(now, _last_input)
                        >= BACKGROUND_IDLE_MS):
                if _function_reload_pending:
                    _reload_functions_after_reclaim(
                        nav, nav.current, settings, registry)
                    func_panel.set_plugin_catalog(
                        registry.plugin_functions,
                        registry.plugin_dependencies)
                    func_panel.set_load_errors(registry.plugin_errors)
                    _function_reload_pending = False
                    _dirty = True
                elif nav.collect_pending():
                    pass
                elif (_frame % 256 == 0
                      and (not hasattr(gc, "mem_free")
                           or gc.mem_free() < 12 * 1024)):
                    gc_started = time.ticks_us()
                    gc.collect()
                    if diagnostics:
                        metrics.record_gc(
                            time.ticks_diff(time.ticks_us(), gc_started))
                else:
                    persisted = persistence.flush(now)
                    if persisted is not None and not persisted[1]:
                        calc_screen.set_storage_error("Not saved - check SD")
                        _dirty = True

            time.sleep_ms(IDLE_LOOP_SLEEP_MS)

        except MemoryError as e:
            # Memory pressure returns to a usable root and forgets snapshots.
            if diagnostics:
                print("MEMORY_RECOVER " + str(e))
            try:
                power.reset(time.ticks_ms())
            except Exception:
                pass
            nav.reset(main_menu)
            _last_render = 0
            _last_input = time.ticks_ms()
            _dirty = True

        except Exception as e:
            # Crash landing: draw error screen, wait for key, then recover
            try:
                power.reset(time.ticks_ms())
            except Exception:
                pass
            _draw_crash(display, e)

            for f in (font_main, font_small):
                if f:
                    f._cache.clear()
            gc.collect()

            time.sleep_ms(300)
            while True:
                kb.scan()
                if kb.pop_key_event() is not None:
                    break
                time.sleep_ms(20)

            # The acknowledgement key and any simultaneous keys must be fully
            # released before the root page can receive input again.
            while kb.any_pressed():
                kb.scan()
                kb.discard_pending_events()
                time.sleep_ms(20)

            nav.reset(main_menu)
            _last_render = 0
            _last_input = time.ticks_ms()
            _dirty = True


if __name__ == "__main__":
    main()
