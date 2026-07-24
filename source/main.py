# SCI-CALC MicroPython Firmware
# Main entry point — display init FIRST, then lazy-load everything else
"""SCI-CALC: Multifunctional Scientific Calculator (MicroPython Edition)."""
import time
import gc
from machine import Pin, SPI

# --- Minimal imports for splash screen ---
from display.ssd1322 import Display as SSD1322
from display.xglcd_font import XglcdFont
from ui.motion import (PAGE_TRANSITION_MS, ACTIVE_FRAME_MS, IDLE_FRAME_MS,
                       ACTIVE_LOOP_SLEEP_MS, IDLE_LOOP_SLEEP_MS,
                       SLEEP_SCAN_MS)
from version import VERSION
from performance import metrics

# SPI pins for display
SPI_CLK = 18
SPI_DATA = 23
SPI_CS = 5
SPI_DC = 16
SPI_RESET = 17
TRANSITION_MS = PAGE_TRANSITION_MS
TRANSITION_PROGRESS_SCALE = 1024
BOOT_PROGRESS_FRAMES = 1
BOOT_FINAL_HOLD_MS = 40


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
    """Draw the completed state of one blocking boot phase."""
    global _boot_fill_w, _boot_title_gs

    bar_x, bar_y, bar_w, bar_h = 20, 34, 216, 5
    target_w = int((bar_w - 2) * step / total)

    # Title fade-in: gradually brighten from 0→15 over first few steps
    if _boot_title_gs < 15:
        _boot_title_gs = min(15, _boot_title_gs + 3)

    # Imports are synchronous, so intermediate splash frames cannot report
    # real progress. One frame per completed phase gets to the usable UI much
    # sooner while preserving meaningful stage feedback.
    frames = BOOT_PROGRESS_FRAMES
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


def _needs_render(now, last_render, active, dirty, stopwatch_running,
                  input_changed):
    """Decide whether the current loop should submit a full display frame."""
    if input_changed:
        return True
    elapsed = time.ticks_diff(now, last_render)
    frame_ms = ACTIVE_FRAME_MS if active else IDLE_FRAME_MS
    return (elapsed >= frame_ms
            and (active or dirty or stopwatch_running or elapsed >= 500))


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
    """Reload plug-ins only after reclaiming every inactive UI cache.

    A function-panel exit is the largest transient allocation in the app.
    It now runs only after the return animation, keeping the visible target
    intact while every inactive page and optional animation buffer is freed.
    """
    nav.prepare_memory_intensive_operation(active_screen)
    return _reload_functions(settings, registry)


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
    def __init__(self, display, font_small, registry, memory=None,
                 residency=None):
        from ui.memory import MemoryManager
        from ui.renderer import Renderer
        from ui.residency import PageResidency
        from ui.sidebar import Sidebar
        self.memory = memory or MemoryManager()
        self.renderer = Renderer(display, Sidebar(font_small, registry),
                                 memory=self.memory)
        self.residency = residency or PageResidency(memory=self.memory)
        self.stack = []
        self._transition = None
        self._transition_cleanup_pending = False
        self._post_transition_collect = False
        self._transition_gc_locked = False
        self._fade_switched = False
        self._fade_brightness = 100
        self._input_locked = False
        self._optional_resources_pending = False
        self.last_present_us = 0

    def boot(self, screen):
        """Set root screen (no transition)."""
        self.stack.append(screen)
        self.residency.recover(screen)
        screen.activate()

    def reserve_transition_buffers(self):
        """Legacy explicit opt-in used by standalone diagnostics only."""
        return self.renderer.enable_transition_buffers()

    def enable_optional_resources(self):
        """Enable the reveal strip only when the active page is memory-safe."""
        if (self.stack
                and getattr(self.current, "requires_serial_memory", False)):
            return False
        return self.renderer.enable_transition_buffers()

    def mark_first_frame_presented(self):
        """Permit one idle attempt to enable optional transition layers."""
        self._optional_resources_pending = True

    def restore_optional_resources(self):
        """Attempt optional transition allocation after a stable live frame."""
        if (not self._optional_resources_pending
                or self._transition is not None
                or not self.stack
                or getattr(self.current, "requires_serial_memory", False)):
            return False
        self._optional_resources_pending = False
        return self.enable_optional_resources()

    def prepare_memory_intensive_operation(self, active_screen):
        """Release optional buffers before compiling/reloading large state."""
        self._optional_resources_pending = False
        self.renderer.release_transition_buffers()
        self.memory.reclaim_for(active_screen, aggressive=True)
        self.memory.release_plot_workspace()
        # ``release_plot_workspace`` owns a raw bytearray rather than a page
        # cache, so force coalescing even when no screen reported a release.
        self.memory.collect()
        self._optional_resources_pending = True

    def register_screens(self, screens):
        """Register pages whose rebuildable caches can be reclaimed safely."""
        self.memory.register_screens(screens)

    @property
    def current(self):
        return self.stack[-1]

    def go_to(self, screen):
        old = self.stack[-1]
        self.stack.append(screen)
        self._start_transition(old, screen, True)

    def go_back(self):
        if len(self.stack) <= 1:
            return
        old = self.stack.pop()
        self._start_transition(old, self.stack[-1], False)

    def _requires_plot_workspace(self, screen):
        return bool(getattr(screen, "requires_plot_workspace", False))

    def _lock_transition_gc(self):
        if (hasattr(gc, "mem_free")
                and hasattr(gc, "disable")
                and not self._transition_gc_locked):
            gc.disable()
            self._transition_gc_locked = True

    def _unlock_transition_gc(self):
        if self._transition_gc_locked:
            gc.enable()
            self._transition_gc_locked = False

    def _start_transition(self, old, new, forward):
        from anim.engine import cancel_animations
        self._transition_cleanup_pending = False
        cancel_animations(old)
        # The old pixels already live in controller RAM.  Establish that fact
        # before releasing any Python state, then acquire optional reveal RAM
        # only after the outgoing page and plot workspace are gone.
        try:
            self.renderer.hold_outgoing(old)
        except MemoryError:
            # The last presented page is still a valid outgoing visual.
            pass

        released = self.residency.leave(old)
        released = (self.memory.reclaim_for(
            new, exclude=(old,), collect=False) or released)
        if self._requires_plot_workspace(old):
            if self.memory.release_plot_workspace():
                released = True
        # Integer-only transition frames stay within the live heap until the
        # wipe completes. Reclaim released page/module objects immediately
        # afterward, before SWAP restore or target-page construction.
        self._post_transition_collect = True
        self._lock_transition_gc()
        self.residency.prepare(new)

        if not self.renderer.can_start_transition():
            self.renderer.enable_transition_buffers(allow_collect=False)

        captured = False
        if self.renderer.can_start_transition():
            try:
                captured = self.renderer.capture_incoming(new, default=True)
            except MemoryError:
                captured = False

        self._input_locked = True
        started = time.ticks_ms()
        if not captured:
            self.renderer.release_transition_buffers(collect=False)
            self._optional_resources_pending = True
            self._fade_switched = False
            self._fade_brightness = getattr(
                self.renderer.display, "brightness", 100)
            self._transition = (started, forward, "fade")
            return

        self._transition = (started, forward, "wipe")

    def is_transitioning(self):
        return self._transition is not None

    def filter_event(self, keyboard, event):
        if self._transition is not None:
            return None
        if self._input_locked:
            if keyboard.any_pressed():
                return None
            self._input_locked = False
        if self.residency.is_restoring(self.current):
            # The default shell remains safe to leave immediately, but other
            # edits must wait or restore_state() could overwrite them.
            if event is None or (event[0], event[1]) != (0, 0):
                return None
        if event is not None and getattr(self.current,
                                         "_residency_error", ""):
            self.current.clear_residency_error()
            return None
        return event

    def allows_page_update(self, event):
        """Block held-key polling while a default page is still restoring."""
        return (event is not None
                or not self.residency.is_restoring(self.current))

    def draw_transition(self, now):
        if self._transition is None:
            return False
        started, forward, kind = self._transition
        elapsed = max(0, time.ticks_diff(now, started))
        progress = min(
            TRANSITION_PROGRESS_SCALE,
            elapsed * TRANSITION_PROGRESS_SCALE // TRANSITION_MS)
        if kind == "fade":
            half = TRANSITION_PROGRESS_SCALE // 2
            maximum = max(1, min(15,
                (int(self._fade_brightness) * 15 + 50) // 100))
            setter = getattr(self.renderer.display,
                             "set_transition_current", None)
            if progress < half:
                level = maximum * (half - progress) // half
            else:
                if not self._fade_switched:
                    if setter is not None:
                        setter(0)
                    self.renderer.present_default(self.current)
                    self._fade_switched = True
                    self.last_present_us = self.renderer.last_present_us
                level = maximum * (progress - half) // half
            if setter is not None:
                setter(level)
            if progress >= TRANSITION_PROGRESS_SCALE:
                restore = getattr(self.renderer.display, "set_brightness", None)
                if restore is not None:
                    restore(self._fade_brightness)
                self._transition_cleanup_pending = False
                self._transition = None
                self._unlock_transition_gc()
            return True
        if progress >= TRANSITION_PROGRESS_SCALE:
            # A delayed loop never catches up by issuing several OLED writes
            # in one frame. Keep the transition live until one fixed strip per
            # frame has exposed the complete default page.
            if not self.renderer.finish_transition(self.current, forward):
                self.last_present_us = self.renderer.last_present_us
                return True
            self.last_present_us = self.renderer.last_present_us
            self._transition_cleanup_pending = True
            self._transition = None
            self._unlock_transition_gc()
            return True
        remaining = TRANSITION_PROGRESS_SCALE - progress
        eased = (TRANSITION_PROGRESS_SCALE
                 - remaining * remaining // TRANSITION_PROGRESS_SCALE)
        self.renderer.present_transition_progress(eased, forward)
        self.last_present_us = self.renderer.last_present_us
        return True

    def present_current(self):
        self.renderer.present(self.current)
        self.last_present_us = self.renderer.last_present_us

    def settle_current(self):
        """Run one post-transition restore step and report pending work."""
        from ui.residency import SETTLE_MORE, SETTLE_REDRAW
        if self._transition is not None:
            return False
        if self._transition_cleanup_pending:
            self._transition_cleanup_pending = False
            self.memory.release_font_caches()
            if not self.renderer.release_transition_buffers():
                self.memory.collect()
            self._post_transition_collect = False
        elif self._post_transition_collect:
            self._post_transition_collect = False
            self.memory.release_font_caches()
            self.memory.collect()
        flags = self.residency.settle(self.current)
        if flags & SETTLE_REDRAW:
            self.present_current()
        return bool(flags & SETTLE_MORE)

    def reset(self, root):
        """Recover to one root page without retaining failed UI state."""
        from anim.engine import cancel_all_animations
        cancel_all_animations()
        self._unlock_transition_gc()
        self._transition = None
        self._transition_cleanup_pending = False
        self._post_transition_collect = False
        self._input_locked = True
        self._optional_resources_pending = False
        self.renderer.release_transition_buffers()
        self.stack[:] = [root]
        self.memory.reclaim_for(root, aggressive=True)
        self.memory.release_plot_workspace()
        # A recovery reset is deliberately rare.  Compact the heap even when
        # all cache owners were already empty, so a failed temporary
        # allocation cannot strand the UI on a fragmented heap.
        self.memory.collect()
        self.residency.recover(root)
        root.activate()
        self._optional_resources_pending = True


def main():
    # ============================================================
    # Phase 1: Display FIRST — show splash immediately
    # ============================================================
    metrics.start_boot()
    display = _init_display()
    metrics.mark_boot("display")
    _boot_progress(display, 1, 8, "Loading keyboard...")

    # ============================================================
    # Phase 2: Lazy-load everything else while showing progress.
    # Each step is wrapped — failure shows error on screen, then
    # continues with a fallback so the calculator still boots.
    # ============================================================

    # Keyboard (critical — halt on failure)
    try:
        from input.keyboard import Keyboard, get_key_label
        kb = Keyboard()
        _boot_progress(display, 2, 8, "Loading fonts...")
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
    _boot_progress(display, 3, 8, "Loading settings...")

    # Settings (fallback: defaults)
    try:
        from utils.storage import load_settings
        settings = load_settings()
        _boot_progress(display, 4, 8, "Loading variables...")
    except Exception as e:
        _boot_fail(display, 4, 8, "Settings", e)
        settings = {"angle_mode": 0, "enabled_functions": ["basic", "trig", "math", "list"], "diagnostics": False, "brightness": 100, "display_digits": 4}
    display.set_brightness(settings.get("brightness", 100))
    metrics.mark_boot("settings")
    # Variables (fallback: empty dict)
    try:
        from utils.storage import load_vars
        vars_dict = load_vars()
        _boot_progress(display, 5, 8, "Loading functions...")
    except Exception as e:
        _boot_fail(display, 5, 8, "Vars", e)
        vars_dict = {}
    metrics.mark_boot("variables")

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
    metrics.mark_boot("functions")

    # ``utils.power`` is a compiled module whose initial load reserves a
    # 1.25 KiB code object on this ESP32 build.  Plug-ins must load first so
    # their configured function set retains its startup budget, but this
    # module must still arrive before the reveal strip and screen objects
    # fragment the heap.
    from utils.power import AWAKE, WOKE, DisplayPower

    # Keep optional reveal and graph buffers out of the core boot phase.  The
    # first real page frame must fit before any non-essential allocation runs.
    nav = Nav(display, font_small, registry)
    nav.residency.swap.start_session()

    # Screens (import + build — skip broken ones)
    try:
        from screens.main_menu import MainMenu
        from screens.calculator import CalculatorScreen
        from ui.lazy_screen import (
            BUILD_ABOUT, BUILD_FUNCTION_PANEL, BUILD_FUNCTION_PICKER,
            BUILD_LETTERS, BUILD_PLOT, BUILD_SETTINGS, BUILD_STOPWATCH,
            BUILD_VARIABLE_PANEL,
            DEFAULT_ABOUT, DEFAULT_FUNCTION_PANEL, DEFAULT_FUNCTION_PICKER,
            DEFAULT_LETTERS, DEFAULT_PLOT, DEFAULT_SETTINGS,
            DEFAULT_STOPWATCH, DEFAULT_VARIABLE_PANEL, LazyScreen,
            ScreenFactory)
        _boot_progress(display, 7, 8, "Building interface...")
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
        screen_factory = ScreenFactory(
            font_main, font_small, display, settings, persistence,
            calc_screen, registry, nav.memory)

        about = LazyScreen(
            "about", "About", DEFAULT_ABOUT,
            screen_factory, BUILD_ABOUT, font=font_main)
        screen_factory.set_about(about)
        settings_screen = LazyScreen(
            "settings", "Settings", DEFAULT_SETTINGS,
            screen_factory, BUILD_SETTINGS, font=font_main)

        func_panel = LazyScreen(
            "function_panel", "Functions", DEFAULT_FUNCTION_PANEL,
            screen_factory, BUILD_FUNCTION_PANEL)
        stopwatch = LazyScreen(
            "stopwatch", "Stopwatch", DEFAULT_STOPWATCH,
            screen_factory, BUILD_STOPWATCH, font=font_main)
        letter_panel = LazyScreen(
            "letter_panel", "Letters", DEFAULT_LETTERS,
            screen_factory, BUILD_LETTERS, font=font_main)
        func_picker = LazyScreen(
            "function_picker", "Functions", DEFAULT_FUNCTION_PICKER,
            screen_factory, BUILD_FUNCTION_PICKER, font=font_main)
        var_panel = LazyScreen(
            "variable_panel", "Variables", DEFAULT_VARIABLE_PANEL,
            screen_factory, BUILD_VARIABLE_PANEL, font=font_main)
        plot_screen = LazyScreen(
            "plot", "Plot", DEFAULT_PLOT,
            screen_factory, BUILD_PLOT,
            requires_plot_workspace=True,
            font=font_main)

        main_menu = MainMenu(font_main)
        main_menu.add_screen("Calculator", calc_screen)
        main_menu.add_screen("Plot", plot_screen)
        main_menu.add_screen("Function Panel", func_panel)
        main_menu.add_screen("Stopwatch", stopwatch)
        main_menu.add_screen("Settings", settings_screen)
    except Exception as e:
        _boot_fail(display, 7, 8, "Init", e)
        raise

    _boot_progress(display, 8, 8, "Starting SCI-CALC...")
    if BOOT_FINAL_HOLD_MS:
        time.sleep_ms(BOOT_FINAL_HOLD_MS)

    # ============================================================
    # Phase 3: Main loop
    # ============================================================
    from ui.element import UIElement
    from anim.engine import animate_all, update_tmp, has_active_animations, active_animation_count

    nav.memory.register_fonts((font_main, font_small))
    nav.register_screens((main_menu, calc_screen, plot_screen, func_panel,
                          stopwatch, settings_screen, letter_panel,
                          func_picker, var_panel, about))
    nav.boot(main_menu)
    first_frame_pending = True
    metrics.bind_runtime(nav, main_menu,
                         (calc_screen, plot_screen, func_panel, stopwatch,
                          settings_screen))
    metrics.mark_boot("ui_ready")
    _frame = 0
    _last_render = time.ticks_add(time.ticks_ms(), -500)
    diagnostics = bool(settings.get("diagnostics", False))
    _diag_last = time.ticks_ms()
    _diag_render_us = 0
    _diag_present_us = 0
    _diag_frames = 0
    _dirty = True
    _function_reload_pending = False
    power = DisplayPower(
        display, int(settings.get("sleep_timeout_s", 180)) * 1000)

    while True:
        try:
            kb.scan()
            event = kb.pop_key_event()
            now = time.ticks_ms()
            power_state = power.update(now, kb.any_pressed())
            if power_state != AWAKE:
                kb.discard_pending_events()
                if power_state == WOKE:
                    _dirty = True
                    _last_render = time.ticks_add(now, -500)
                # Matrix keys cannot wake ESP32 deep sleep reliably, so keep a
                # low-cost scan loop while the OLED controller is asleep.
                time.sleep_ms(SLEEP_SCAN_MS)
                continue

            if (_frame % 100 == 0
                    and not nav.is_transitioning()
                    and not has_active_animations()):
                if diagnostics:
                    gc_started = time.ticks_us()
                gc.collect()
                if diagnostics:
                    metrics.record_gc(
                        time.ticks_diff(time.ticks_us(), gc_started))
            _frame += 1

            animate_all()
            update_tmp()

            event = nav.filter_event(kb, event)
            had_event = event is not None
            if diagnostics and had_event:
                metrics.record_input()

            cur = nav.current
            result = None
            if diagnostics and event is not None:
                print("INPUT page=" + cur.__class__.__name__
                      + " row=" + str(event[0])
                      + " col=" + str(event[1])
                      + " shift=" + str(int(event[2]))
                      + " key=" + get_key_label(event[0], event[1], event[2]))
            if not nav.is_transitioning() and event is not None:
                erow, ecol, eshift = event
                if ((erow, ecol) == (3, 5) and eshift
                        and (cur is calc_screen or cur is plot_screen)):
                    input_box = (calc_screen.input_box
                                 if cur is calc_screen
                                 else plot_screen.get_loaded_attr("input_box"))
                    if input_box is not None:
                        screen_factory.set_letter_input(input_box)
                        nav.go_to(letter_panel)
                        cur = nav.current
                        event = None
                elif (erow, ecol) == (4, 4):
                    registry.angle_mode = 1 - registry.angle_mode
                    settings["angle_mode"] = registry.angle_mode
                    persistence.request_settings(settings)
                    event = None
            if (not nav.is_transitioning()
                    and nav.allows_page_update(event)
                    and (event is not None or kb.is_pressed(0, 0) or kb.is_pressed(4, 3))):
                result = cur.update(kb, event)
                navigation_results = (
                    "BACK", "FUNC_PANEL_DONE", "FUNC_PANEL_CANCEL",
                    "FUNC_PICKER_DONE", "LETTER_DONE", "VAR_PANEL_DONE",
                    "FUNC_PICKER", "VARIABLE_PANEL")
                if (result not in navigation_results
                        and not isinstance(result, UIElement)
                        and not isinstance(result, LazyScreen)):
                    nav.residency.mark_dirty(cur)
                if diagnostics and result is not None:
                    print("ACTION page=" + cur.__class__.__name__
                          + " result=" + str(result))

            # Apply navigation before deciding whether to render. This makes
            # the input frame the first transition frame instead of leaving
            # the captured pages dormant for one active-frame interval.
            if result == "BACK":
                nav.go_back()
            elif result == "FUNC_PANEL_DONE":
                nav.go_back()
                _function_reload_pending = True
            elif result in ("FUNC_PICKER_DONE", "LETTER_DONE", "VAR_PANEL_DONE"):
                nav.go_back()
            elif result == "FUNC_PANEL_CANCEL":
                nav.go_back()
            elif result == "FUNC_PICKER":
                nav.go_to(func_picker)
            elif result == "VARIABLE_PANEL":
                nav.go_to(var_panel)
            elif ((isinstance(result, UIElement)
                   or isinstance(result, LazyScreen))
                  and result is not cur):
                nav.go_to(result)

            cur = nav.current
            now = time.ticks_ms()
            if had_event or result is not None:
                _dirty = True
            active = nav.is_transitioning() or has_active_animations()
            needs_render = _needs_render(
                now, _last_render, active, _dirty,
                (cur is stopwatch
                 and stopwatch.get_loaded_attr("_running", False)),
                had_event or result is not None)

            if needs_render:
                _last_render = now
                render_started = time.ticks_us()
                if not nav.draw_transition(now):
                    nav.present_current()
                if first_frame_pending:
                    nav.mark_first_frame_presented()
                    first_frame_pending = False
                _diag_present_us += nav.last_present_us
                render_elapsed = time.ticks_diff(time.ticks_us(), render_started)
                _diag_render_us += render_elapsed
                if diagnostics:
                    metrics.record_frame(render_elapsed)
                _diag_frames += 1
                _dirty = False

            if (diagnostics
                    and not active
                    and time.ticks_diff(now, _diag_last) >= 5000):
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

            if calc_screen.context.dirty and calc_screen.context.consume_dirty():
                persistence.request_vars(calc_screen.vars)

            # SD writes are deliberately delayed until a quiet loop. The
            # underlying storage functions retain their atomic backup scheme.
            if not active and not had_event and result is None:
                settling = nav.settle_current()
                # A settle step can itself start a curve/menu animation. Read
                # the scheduler state again before permitting any SD or
                # plugin work in this same loop iteration.
                active = nav.is_transitioning() or has_active_animations()
                if not settling and not active and _function_reload_pending:
                    _reload_functions_after_reclaim(
                        nav, nav.current, settings, registry)
                    loaded_panel = func_panel.loaded()
                    if loaded_panel is not None:
                        loaded_panel.set_plugin_catalog(
                            registry.plugin_functions,
                            registry.plugin_dependencies)
                        loaded_panel.set_load_errors(registry.plugin_errors)
                    _function_reload_pending = False
                    _dirty = True
                elif not settling and not active:
                    nav.restore_optional_resources()
                    persisted = persistence.flush(now)
                    if persisted is not None and not persisted[1]:
                        calc_screen.set_storage_error("Not saved - check SD")
                        _dirty = True

            # Leave enough scheduler headroom to sustain the 16 ms (~60 FPS)
            # active deadline after a full-frame SPI transfer.
            time.sleep_ms(ACTIVE_LOOP_SLEEP_MS if active else IDLE_LOOP_SLEEP_MS)

        except MemoryError as e:
            # Memory pressure should return to a usable root page rather than
            # leaving a crash overlay on top of a half-transitioned screen.
            # Nav.reset cancels animations, frees rebuildable inactive state,
            # compacts the heap, and locks input until the pressed key lifts.
            if diagnostics:
                print("MEMORY_RECOVER " + str(e))
            try:
                power.reset(time.ticks_ms())
            except Exception:
                pass
            nav.reset(main_menu)
            _last_render = 0
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


if __name__ == "__main__":
    main()
