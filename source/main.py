# SCI-CALC MicroPython Firmware
# Main entry point — display init FIRST, then lazy-load everything else
"""SCI-CALC: Multifunctional Scientific Calculator (MicroPython Edition)."""
import time
import gc
from machine import Pin, SPI, ADC
from framebuf import FrameBuffer, GS4_HMSB  # type: ignore

# --- Minimal imports for splash screen ---
from display.ssd1322 import Display as SSD1322
from display.xglcd_font import XglcdFont

# SPI pins for display
SPI_CLK = 18
SPI_DATA = 23
SPI_CS = 5
SPI_DC = 16
SPI_RESET = 17
BAT_PIN = 36


def _init_display():
    """Initialize SSD1322 display — called FIRST for fast splash."""
    spi = SPI(2,
              baudrate=16_000_000,
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

    # Animate bar fill with ease-out (6 frames, ~96ms per step)
    frames = 6 if target_w != _boot_fill_w else 1
    for i in range(frames):
        t = (i + 1) / frames
        eased = 1 - (1 - t) ** 2
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
            time.sleep_ms(16)

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


# --- Cached globals for sidebar ---
_adc = None
_bat_voltage = "?.?V"
_bat_frame = 0


def _draw_sidebar(display, font=None):
    global _adc, _bat_voltage, _bat_frame
    if _bat_frame <= 0:
        try:
            if _adc is None:
                _adc = ADC(Pin(BAT_PIN))
                _adc.atten(ADC.ATTN_11DB)
            raw = _adc.read()
            voltage = raw / 4095.0 * 3.3 * 2.0
            _bat_voltage = f"{voltage:.1f}V"
        except Exception:
            _bat_voltage = "?.?V"
        _bat_frame = 50
    _bat_frame -= 1

    import calc.functions
    ang = "DEG" if calc.functions.ANGLE_MODE else "RAD"

    display.draw_rectangle(213, 0, 42, 63, 15)
    if font:
        display.draw_text(215, 2, "BAT", font, gs=15)
        display.draw_text(215, 14, _bat_voltage, font, gs=15)
        display.draw_hline(215, 26, 28, 6)
        display.draw_text(215, 30, ang, font, gs=12)
    else:
        display.draw_text8x8(215, 2, "BAT", gs=15)
        display.draw_text8x8(215, 14, _bat_voltage, gs=15)
        display.draw_text8x8(215, 30, ang, gs=12)


def _reload_functions(settings):
    import calc.functions
    from calc.functions import build_func_table, DEFAULT_ENABLED_GROUPS
    from calc.loader import load_function_files
    enabled = settings.get("enabled_functions", DEFAULT_ENABLED_GROUPS)
    groups = [g for g in enabled if g in calc.functions.FUNCTION_GROUPS]
    sd_names = [g for g in enabled if g not in calc.functions.FUNCTION_GROUPS]
    func_table = build_func_table(groups)
    if sd_names:
        load_function_files(func_table, sd_names)
    return func_table


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


def _slide_transition(display, old, new, font_small, forward, buf_a, buf_b):
    """Dual-slide INDENT transition. Sidebar rendered fresh each frame
    so it stays fixed — doesn't slide with the screen snapshots."""
    w = display.width
    h = display.height

    # Snapshot old (without sidebar)
    display.clear_buffers(0)
    old.draw(display)
    buf_a[:] = display.gs4_buf

    # Snapshot new (without sidebar)
    new.activate()
    display.clear_buffers(0)
    new.draw(display)
    buf_b[:] = display.gs4_buf

    fb_a = FrameBuffer(buf_a, w, h, GS4_HMSB)
    fb_b = FrameBuffer(buf_b, w, h, GS4_HMSB)

    for i in range(1, 11):
        t = i / 10.0
        eased = 1.0 - pow(2.0, -10.0 * t)  # INDENT

        if forward:
            new_x, old_x = w - int(w * eased), -int(w * eased)
        else:
            new_x, old_x = -w + int(w * eased), int(w * eased)

        display.clear_buffers(0)
        display.gs4_fb.blit(fb_a, old_x, 0)
        display.gs4_fb.blit(fb_b, new_x, 0)
        _draw_sidebar(display, font_small)  # fixed position
        display.present()
        time.sleep_ms(13)

    display.clear_buffers(0)
    new.draw(display)
    _draw_sidebar(display, font_small)
    display.present()


# ── Screen navigation ───────────────────────────────────────────

class Nav:
    """Screen stack with slide transitions. Captures display + font once.
    Pre-allocates transition buffers to avoid 16KB heap alloc on every switch."""
    def __init__(self, display, font_small):
        self.display = display
        self.font_small = font_small
        self.stack = []
        # Pre-allocate transition snapshot buffers (256×64/2 = 8192 bytes each)
        blen = display.buffer_length
        self._buf_a = bytearray(blen)
        self._buf_b = bytearray(blen)

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
        _slide_transition(self.display, old, screen, self.font_small,
                          forward=True, buf_a=self._buf_a, buf_b=self._buf_b)

    def go_back(self):
        if len(self.stack) <= 1:
            return
        old = self.stack.pop()
        old.deactivate()
        _slide_transition(self.display, old, self.stack[-1], self.font_small,
                          forward=False, buf_a=self._buf_a, buf_b=self._buf_b)


def main():
    # ============================================================
    # Phase 1: Display FIRST — show splash immediately
    # ============================================================
    display = _init_display()
    _boot_progress(display, 1, 8, "Display OK")

    # ============================================================
    # Phase 2: Lazy-load everything else while showing progress.
    # Each step is wrapped — failure shows error on screen, then
    # continues with a fallback so the calculator still boots.
    # ============================================================

    # Keyboard (critical — halt on failure)
    try:
        from input.keyboard import Keyboard
        kb = Keyboard()
        _boot_progress(display, 2, 8, "Keyboard OK")
    except Exception as e:
        _boot_fail(display, 2, 8, "Keyboard", e)
        raise  # can't run without keyboard

    # Fonts (fallback: built-in 8x8 font via draw_text8x8)
    try:
        font_main = XglcdFont("fonts/Bally7x9.c", 7, 9)
    except Exception as e:
        _boot_fail(display, 3, 8, "Fonts", e)
        font_main = None
    try:
        font_small = XglcdFont("fonts/Neato5x7.c", 5, 7)
    except Exception:
        font_small = None
    if font_main is not None:
        _boot_progress(display, 3, 8, "Fonts OK")

    # Settings (fallback: defaults)
    try:
        from utils.storage import load_settings
        settings = load_settings()
        _boot_progress(display, 4, 8, "Settings OK")
    except Exception as e:
        _boot_fail(display, 4, 8, "Settings", e)
        settings = {"angle_mode": 0, "enabled_functions": ["basic", "trig", "math", "list"], "version": "1.0.2"}
    import calc.functions
    calc.functions.ANGLE_MODE = settings.get("angle_mode", 0)

    # Variables (fallback: empty dict)
    try:
        from utils.storage import load_vars
        vars_dict = load_vars()
        _boot_progress(display, 5, 8, "Vars OK")
    except Exception as e:
        _boot_fail(display, 5, 8, "Vars", e)
        vars_dict = {}

    # Functions (fallback: built-in groups only)
    try:
        func_table = _reload_functions(settings)
        _boot_progress(display, 6, 8, "Functions OK")
    except Exception as e:
        _boot_fail(display, 6, 8, "Functions", e)
        from calc.functions import build_func_table
        func_table = build_func_table(["basic", "trig", "math", "list"])
    calc.functions._current_func_table = func_table

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
        _boot_progress(display, 7, 8, "Screens OK")
    except Exception as e:
        _boot_fail(display, 7, 8, "Screens", e)
        # If imports failed, we can't continue — the error screen already showed
        raise

    try:
        about = AboutScreen(font_main, settings.get("version", "1.0.2"))
        func_panel = FunctionPanel(font_main)
        stopwatch = StopwatchScreen(font_main)
        calc_screen = CalculatorScreen(font_main, font_small)
        calc_screen.vars = vars_dict
        calc_screen.func_table = func_table
        letter_panel = LetterPanel(font_main, calc_screen.input_box)
        func_picker = FunctionPicker(font_main, calc_screen)
        var_panel = VariablePanel(font_main, calc_screen)
        plot_screen = PlotScreen(font_main, font_small)

        main_menu = MainMenu(font_main)
        main_menu.add_screen("Calculator", calc_screen)
        main_menu.add_screen("Plot", plot_screen)
        main_menu.add_screen("Function Panel", func_panel)
        main_menu.add_screen("Stopwatch", stopwatch)
        main_menu.add_screen("About", about)
    except Exception as e:
        _boot_fail(display, 7, 8, "Init", e)
        raise

    _boot_progress(display, 8, 8, "Ready.")
    time.sleep_ms(400)

    # ============================================================
    # Phase 3: Main loop
    # ============================================================
    from ui.element import UIElement
    from anim.engine import animate_all, update_tmp, has_active_animations
    from utils.storage import save_settings, save_vars

    nav = Nav(display, font_small)
    nav.boot(main_menu)
    _angle_was_pressed = False
    _rpn_shift_was_pressed = False
    _frame = 0
    _last_render = 0
    FRAME_MS = 66

    while True:
        try:
            if _frame % 100 == 0:
                gc.collect()
            _frame += 1

            kb.scan()
            animate_all()
            update_tmp()

            cur = nav.current
            result = cur.update(kb)

            now = time.ticks_ms()
            needs_render = (time.ticks_diff(now, _last_render) >= FRAME_MS
                            or has_active_animations()
                            or result is not None)

            if needs_render:
                _last_render = now
                display.clear_buffers(0)
                cur.draw(display)
                _draw_sidebar(display, font_small)
                display.present()

            # ── Screen switching ──
            if result == "BACK":
                nav.go_back()
            elif result == "FUNC_PANEL_DONE":
                settings = load_settings()
                func_table = _reload_functions(settings)
                calc.functions._current_func_table = func_table
                calc_screen.func_table = func_table
                nav.go_back()
            elif result in ("FUNC_PICKER_DONE", "LETTER_DONE", "VAR_PANEL_DONE"):
                nav.go_back()
            elif result == "FUNC_PICKER":
                nav.go_to(func_picker)
            elif result == "VARIABLE_PANEL":
                nav.go_to(var_panel)
            elif isinstance(result, UIElement) and result is not cur:
                nav.go_to(result)

            # Global Shift+RPN → Letter Panel
            rpn_pressed = kb.is_pressed(3, 5)
            shift_held = kb.is_pressed(4, 0)
            if (rpn_pressed and shift_held and not _rpn_shift_was_pressed
                    and nav.current in (calc_screen, plot_screen)):
                letter_panel.input_box = nav.current.input_box
                nav.go_to(letter_panel)
            _rpn_shift_was_pressed = rpn_pressed and shift_held

            # ANG key
            ang_pressed = kb.is_pressed(4, 4)
            if ang_pressed and not _angle_was_pressed:
                calc.functions.ANGLE_MODE = 1 - calc.functions.ANGLE_MODE
                settings["angle_mode"] = calc.functions.ANGLE_MODE
                save_settings(settings)
            _angle_was_pressed = ang_pressed

            # Persist vars
            if nav.current is calc_screen:
                if calc_screen.vars != vars_dict:
                    vars_dict = dict(calc_screen.vars)
                    save_vars(vars_dict)

            time.sleep_ms(10)

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
