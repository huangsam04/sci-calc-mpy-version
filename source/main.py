# SCI-CALC MicroPython Firmware
# Main entry point — display init FIRST, then lazy-load everything else
"""SCI-CALC: Multifunctional Scientific Calculator (MicroPython Edition)."""
import time
import gc
from machine import Pin, SPI

# --- Minimal imports for splash screen ---
from display.ssd1322 import Display as SSD1322
from display.xglcd_font import XglcdFont

# SPI pins for display
SPI_CLK = 18
SPI_DATA = 23
SPI_CS = 5
SPI_DC = 16
SPI_RESET = 17
TRANSITION_MS = 260


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


# --- Boot animation state ---
_boot_fill_w = 0       # current animated bar fill width (pixels)
_boot_title_gs = 0     # title grayscale for fade-in


def _boot_progress(display, step, total, label=""):
    """Draw boot screen with smooth animated progress bar."""
    global _boot_fill_w, _boot_title_gs

    bar_x, bar_y, bar_w, bar_h = 20, 34, 216, 5
    target_w = int((bar_w - 2) * step / total)

    # Title fade-in: gradually brighten from 0→15 over first few steps
    if _boot_title_gs < 15:
        _boot_title_gs = min(15, _boot_title_gs + 3)

    # Animate each progress segment with an exact cubic ease-out.
    frames = 8 if target_w != _boot_fill_w else 1
    for i in range(frames):
        t = (i + 1) / frames
        eased = 1 - (1 - t) ** 3
        current_w = _boot_fill_w + int((target_w - _boot_fill_w) * eased)

        display.clear_buffers(0)

        # Title — centered on full 256px display (8 chars × 8px = 64px, (256-64)/2 = 96)
        display.draw_text8x8(96, 10, "SCI-CALC", gs=_boot_title_gs)
        # Decorative separator
        display.draw_hline(60, 20, 136, min(_boot_title_gs, 6))

        # Progress bar track
        display.draw_rectangle(bar_x, bar_y, bar_w, bar_h, 6)
        # Progress bar fill
        if current_w > 0:
            display.fill_rectangle(bar_x + 1, bar_y + 1,
                                   max(1, current_w), bar_h - 2, 15)
        # Glint — bright pixel at the leading edge of the fill
        if current_w > 2:
            gx = bar_x + 1 + current_w - 2
            display.draw_vline(gx, bar_y + 1, bar_h - 2, 15)

        # Step label
        if label:
            display.draw_text8x8(20, 44, label, gs=10)

        # Step counter (right-aligned)
        progress = f"{step}/{total}"
        px = 210 - len(progress) * 8
        display.draw_text8x8(px, 44, progress, gs=8)

        display.present()
        if frames > 1:
            time.sleep_ms(12)

    _boot_fill_w = target_w


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


def _reload_functions(settings, registry=None):
    from calc.functions import build_registry, DEFAULT_ENABLED_GROUPS, FUNCTION_GROUPS
    from calc.loader import load_function_files
    enabled = settings.get("enabled_functions", DEFAULT_ENABLED_GROUPS)
    groups = [g for g in enabled if g in FUNCTION_GROUPS]
    sd_names = [g[7:] for g in enabled if g.startswith("plugin:")]
    staged = build_registry(groups)
    if sd_names:
        load_function_files(staged, sd_names)
    if registry is not None:
        staged.angle_mode = registry.angle_mode
        registry.replace(staged)
        return registry
    return staged


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
    """Screen stack and non-blocking transition state.

    Frame capture, composition, fixed chrome and presentation are delegated to
    one Renderer instance with reusable buffers.
    """
    def __init__(self, display, font_small, registry):
        from ui.renderer import Renderer
        from ui.sidebar import Sidebar
        self.renderer = Renderer(display, Sidebar(font_small, registry))
        self.stack = []
        self._transition = None
        self._input_locked = False
        from anim.engine import easing_out_quad
        self._transition_easing = easing_out_quad

    def boot(self, screen):
        """Set root screen (no transition)."""
        self.stack.append(screen)
        screen.activate()

    @property
    def current(self):
        return self.stack[-1]

    def go_to(self, screen):
        old = self.stack[-1]
        old.deactivate()
        self.stack.append(screen)
        self._start_transition(old, screen, True)

    def go_back(self):
        if len(self.stack) <= 1:
            return
        old = self.stack.pop()
        old.deactivate()
        self._start_transition(old, self.stack[-1], False)

    def _start_transition(self, old, new, forward):
        from anim.engine import cancel_animations
        cancel_animations(old)
        new.activate()
        self.renderer.capture_transition(old, new)
        self._transition = (time.ticks_ms(), forward)
        self._input_locked = True

    def is_transitioning(self):
        return self._transition is not None

    def filter_event(self, keyboard, event):
        if self._transition is not None:
            return None
        if self._input_locked:
            if keyboard.any_pressed():
                return None
            self._input_locked = False
        return event

    def draw_transition(self, now):
        if self._transition is None:
            return False
        started, forward = self._transition
        elapsed = max(0, time.ticks_diff(now, started))
        t = min(1.0, elapsed / TRANSITION_MS)
        if t >= 1.0:
            # Finish on the same canonical composition used by an idle frame.
            # This prevents a stale captured page/chrome frame from lingering
            # until the 500 ms keepalive refresh.
            self.renderer.present(self.current)
            self._transition = None
            return True
        eased = self._transition_easing(t)
        self.renderer.present_transition(eased, forward)
        return True

    def present_current(self):
        self.renderer.present(self.current)


def main():
    # ============================================================
    # Phase 1: Display FIRST — show splash immediately
    # ============================================================
    display = _init_display()
    _boot_progress(display, 1, 8, "Loading keyboard...")

    # ============================================================
    # Phase 2: Lazy-load everything else while showing progress.
    # Each step is wrapped — failure shows error on screen, then
    # continues with a fallback so the calculator still boots.
    # ============================================================

    # Keyboard (critical — halt on failure)
    try:
        from input.keyboard import Keyboard
        kb = Keyboard()
        _boot_progress(display, 2, 8, "Loading fonts...")
    except Exception as e:
        _boot_fail(display, 2, 8, "Keyboard", e)
        raise  # can't run without keyboard

    # Fonts (fallback: built-in 8x8 font via draw_text8x8)
    try:
        font_main = XglcdFont("/sd/fonts/Bally7x9.c", 7, 9)
    except Exception as e:
        _boot_fail(display, 3, 8, "Fonts", e)
        font_main = None
    try:
        font_small = XglcdFont("/sd/fonts/Neato5x7.c", 5, 7)
    except Exception:
        font_small = None
    _boot_progress(display, 3, 8, "Loading settings...")

    # Settings (fallback: defaults)
    try:
        from utils.storage import load_settings
        settings = load_settings()
        _boot_progress(display, 4, 8, "Loading variables...")
    except Exception as e:
        _boot_fail(display, 4, 8, "Settings", e)
        settings = {"angle_mode": 0, "enabled_functions": ["basic", "trig", "math", "list"], "version": "1.1.0", "diagnostics": False}
    # Variables (fallback: empty dict)
    try:
        from utils.storage import load_vars
        vars_dict = load_vars()
        _boot_progress(display, 5, 8, "Loading functions...")
    except Exception as e:
        _boot_fail(display, 5, 8, "Vars", e)
        vars_dict = {}

    # Functions (fallback: built-in groups only)
    try:
        registry = _reload_functions(settings)
        registry.angle_mode = settings.get("angle_mode", 0)
        _boot_progress(display, 6, 8, "Loading screens...")
    except Exception as e:
        _boot_fail(display, 6, 8, "Functions", e)
        from calc.functions import build_registry
        registry = build_registry(["basic", "trig", "math", "list"])
        registry.angle_mode = settings.get("angle_mode", 0)

    # Screens (import + build — skip broken ones)
    try:
        from screens.main_menu import MainMenu
        from screens.calculator import CalculatorScreen
        from screens.function_panel import FunctionPanel
        from screens.stopwatch import StopwatchScreen
        from screens.about import AboutScreen
        from screens.letter_panel import LetterPanel
        from screens.function_picker import FunctionPicker
        from screens.variable_panel import VariablePanel
        from screens.plot import PlotScreen
        _boot_progress(display, 7, 8, "Building interface...")
    except Exception as e:
        _boot_fail(display, 7, 8, "Screens", e)
        # If imports failed, we can't continue — the error screen already showed
        raise

    try:
        about = AboutScreen(font_main, settings.get("version", "1.1.0"))
        func_panel = FunctionPanel(font_main)
        stopwatch = StopwatchScreen(font_main)
        calc_screen = CalculatorScreen(font_main, font_small, registry, vars_dict)
        letter_panel = LetterPanel(font_main, calc_screen.input_box)
        func_picker = FunctionPicker(font_main, calc_screen)
        var_panel = VariablePanel(font_main, calc_screen)
        plot_screen = PlotScreen(font_main, font_small, registry)

        main_menu = MainMenu(font_main)
        main_menu.add_screen("Calculator", calc_screen)
        main_menu.add_screen("Plot", plot_screen)
        main_menu.add_screen("Function Panel", func_panel)
        main_menu.add_screen("Stopwatch", stopwatch)
        main_menu.add_screen("About", about)
    except Exception as e:
        _boot_fail(display, 7, 8, "Init", e)
        raise

    _boot_progress(display, 8, 8, "Starting SCI-CALC...")
    time.sleep_ms(180)

    # ============================================================
    # Phase 3: Main loop
    # ============================================================
    from ui.element import UIElement
    from anim.engine import animate_all, update_tmp, has_active_animations, active_animation_count
    from utils.storage import save_settings, save_vars

    nav = Nav(display, font_small, registry)
    nav.boot(main_menu)
    _frame = 0
    _last_render = time.ticks_add(time.ticks_ms(), -500)
    IDLE_FRAME_MS = 66
    ACTIVE_FRAME_MS = 20
    diagnostics = bool(settings.get("diagnostics", False))
    _diag_last = time.ticks_ms()
    _diag_render_us = 0
    _diag_present_us = 0
    _diag_frames = 0
    _dirty = True
    _next_var_save = 0

    while True:
        try:
            if _frame % 100 == 0:
                gc.collect()
            _frame += 1

            kb.scan()
            animate_all()
            update_tmp()

            event = kb.pop_key_event()
            event = nav.filter_event(kb, event)
            had_event = event is not None

            cur = nav.current
            result = None
            if not nav.is_transitioning() and event is not None:
                erow, ecol, eshift = event
                if (erow, ecol) == (3, 5) and eshift and cur in (calc_screen, plot_screen):
                    letter_panel.input_box = cur.input_box
                    nav.go_to(letter_panel)
                    cur = nav.current
                    event = None
                elif (erow, ecol) == (4, 4):
                    registry.angle_mode = 1 - registry.angle_mode
                    settings["angle_mode"] = registry.angle_mode
                    if not save_settings(settings):
                        calc_screen.set_storage_error("Not saved - check SD")
                    event = None
            if (not nav.is_transitioning()
                    and (event is not None or kb.is_pressed(0, 0) or kb.is_pressed(4, 3))):
                result = cur.update(kb, event)

            now = time.ticks_ms()
            if had_event or result is not None:
                _dirty = True
            active = nav.is_transitioning() or has_active_animations()
            frame_ms = ACTIVE_FRAME_MS if active else IDLE_FRAME_MS
            needs_render = (time.ticks_diff(now, _last_render) >= frame_ms
                            and (active or _dirty
                                 or cur is stopwatch and stopwatch._running
                                 or time.ticks_diff(now, _last_render) >= 500))

            if needs_render:
                _last_render = now
                render_started = time.ticks_us()
                if not nav.draw_transition(now):
                    present_started = time.ticks_us()
                    nav.present_current()
                    _diag_present_us += time.ticks_diff(time.ticks_us(), present_started)
                _diag_render_us += time.ticks_diff(time.ticks_us(), render_started)
                _diag_frames += 1
                _dirty = False

            if diagnostics and time.ticks_diff(now, _diag_last) >= 5000:
                heap_before = gc.mem_free() if hasattr(gc, "mem_free") else -1
                gc.collect()
                heap_after = gc.mem_free() if hasattr(gc, "mem_free") else -1
                divisor = max(1, _diag_frames)
                print("PERF frames=" + str(_diag_frames)
                      + " render_us=" + str(_diag_render_us // divisor)
                      + " present_us=" + str(_diag_present_us // divisor)
                      + " heap_before=" + str(heap_before)
                      + " heap_after=" + str(heap_after)
                      + " animations=" + str(active_animation_count()))
                _diag_last = now
                _diag_render_us = 0
                _diag_present_us = 0
                _diag_frames = 0

            # ── Screen switching ──
            if result == "BACK":
                nav.go_back()
            elif result == "FUNC_PANEL_DONE":
                settings = load_settings()
                _reload_functions(settings, registry)
                nav.go_back()
            elif result in ("FUNC_PICKER_DONE", "LETTER_DONE", "VAR_PANEL_DONE"):
                nav.go_back()
            elif result == "FUNC_PICKER":
                nav.go_to(func_picker)
            elif result == "VARIABLE_PANEL":
                nav.go_to(var_panel)
            elif isinstance(result, UIElement) and result is not cur:
                nav.go_to(result)

            # Persist vars
            if (calc_screen.context.dirty
                    and time.ticks_diff(now, _next_var_save) >= 0
                    and calc_screen.context.consume_dirty()):
                if not save_vars(calc_screen.vars):
                    calc_screen.context.mark_dirty()
                    calc_screen.set_storage_error("Not saved - check SD")
                    # Avoid hammering a missing or unhealthy SD card.
                    _next_var_save = time.ticks_add(now, 2000)
                    _dirty = True
                else:
                    _next_var_save = now

            # Keep the scheduler close to the 20 ms animation deadline. A
            # fixed 10 ms sleep plus rendering/present time otherwise drops
            # the effective transition rate well below the requested 50 FPS.
            time.sleep_ms(2 if active else 10)

        except Exception as e:
            # Crash landing: draw error screen, wait for key, then recover
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

            nav.stack[:] = [main_menu]
            main_menu.activate()
            _last_render = 0


if __name__ == "__main__":
    main()
