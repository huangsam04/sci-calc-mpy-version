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
    display.draw_rectangle(213, 0, 42, 63, 15)
    if font:
        display.draw_text(215, 2, "BAT", font, gs=15)
        display.draw_text(215, 14, _bat_voltage, font, gs=15)
    else:
        display.draw_text8x8(215, 2, "BAT", gs=15)
        display.draw_text8x8(215, 14, _bat_voltage, gs=15)


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


def _screen_transition(display, from_screen, to_screen, font_small):
    """Dual-slide transition matching CPP_VERSION: both screens move simultaneously
    with INDENT easing. Direction-aware — forward vs back use opposite directions.

    FORWARD: old exits LEFT, new enters from RIGHT (push-left feel).
    BACK:    old exits RIGHT, new enters from LEFT (pop-back feel).
    """
    going_back = (getattr(from_screen, 'parent', None) is to_screen)

    # Render old screen → snapshot
    display.clear_buffers(0)
    from_screen.draw(display)
    _draw_sidebar(display, font_small)
    old_buf = bytearray(display.gs4_buf)

    # Activate new screen, render it → snapshot
    to_screen.activate()
    display.clear_buffers(0)
    to_screen.draw(display)
    _draw_sidebar(display, font_small)
    new_buf = bytearray(display.gs4_buf)

    w = display.width
    frames = 10  # ~130ms total
    for i in range(1, frames + 1):
        t = i / frames
        eased = 1.0 - pow(2.0, -10.0 * t)  # INDENT — same as CPP_VERSION

        if going_back:
            # BACK: new enters from LEFT, old exits RIGHT
            new_x = -w + int(w * eased)
            old_x = int(w * eased)
        else:
            # FORWARD: new enters from RIGHT, old exits LEFT
            new_x = w - int(w * eased)
            old_x = -int(w * eased)

        display.clear_buffers(0)

        # Old underneath, new on top — new slides over old
        old_fb = FrameBuffer(old_buf, w, display.height, GS4_HMSB)
        display.gs4_fb.blit(old_fb, old_x, 0)
        new_fb = FrameBuffer(new_buf, w, display.height, GS4_HMSB)
        display.gs4_fb.blit(new_fb, new_x, 0)

        display.present()
        time.sleep_ms(13)

    # Final clean frame
    display.clear_buffers(0)
    to_screen.draw(display)
    _draw_sidebar(display, font_small)
    display.present()


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
        settings = {"angle_mode": 0, "enabled_functions": ["basic", "trig", "math", "list"], "version": "1.0.0"}
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
        about = AboutScreen(font_main, settings.get("version", "1.0.0"))
        func_panel = FunctionPanel(font_main)
        stopwatch = StopwatchScreen(font_main)
        calc_screen = CalculatorScreen(font_main, font_small)
        calc_screen.vars = vars_dict
        calc_screen.func_table = func_table
        letter_panel = LetterPanel(font_main, calc_screen.input_box)
        letter_panel.parent = calc_screen
        func_picker = FunctionPicker(font_main, calc_screen)
        func_picker.parent = calc_screen
        var_panel = VariablePanel(font_main, calc_screen)
        var_panel.parent = calc_screen
        plot_screen = PlotScreen(font_main, font_small)

        main_menu = MainMenu(font_main)
        main_menu.add_screen("Calculator", calc_screen)
        main_menu.add_screen("Plot", plot_screen)
        main_menu.add_screen("Function Panel", func_panel)
        main_menu.add_screen("Stopwatch", stopwatch)
        main_menu.add_screen("About", about)
        calc_screen.parent = main_menu
        plot_screen.parent = main_menu
        func_panel.parent = main_menu
        stopwatch.parent = main_menu
        about.parent = main_menu
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

    current_screen = main_menu
    current_screen.activate()
    _angle_was_pressed = False
    _rpn_shift_was_pressed = False
    _letter_parent = calc_screen  # screen to return to after letter panel closes
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

            result = current_screen.update(kb)

            now = time.ticks_ms()
            needs_render = (time.ticks_diff(now, _last_render) >= FRAME_MS
                            or has_active_animations()
                            or result is not None)

            if needs_render:
                _last_render = now
                display.clear_buffers(0)
                current_screen.draw(display)
                _draw_sidebar(display, font_small)
                display.present()

            # ── Screen switching (collect target, transition at end) ──
            next_screen = None

            if result == "BACK":
                if current_screen.parent:
                    next_screen = current_screen.parent
            elif result == "FUNC_PANEL_DONE":
                settings = load_settings()
                func_table = _reload_functions(settings)
                calc.functions._current_func_table = func_table
                calc_screen.func_table = func_table
                next_screen = main_menu
            elif result == "FUNC_PICKER":
                next_screen = func_picker
            elif result == "LETTER_PANEL":
                next_screen = letter_panel
            elif result == "LETTER_DONE":
                next_screen = _letter_parent
            elif result == "FUNC_PICKER_DONE":
                next_screen = calc_screen
            elif result == "VARIABLE_PANEL":
                next_screen = var_panel
            elif result == "VAR_PANEL_DONE":
                next_screen = calc_screen
            elif isinstance(result, UIElement) and result is not current_screen:
                next_screen = result

            # Global Shift+RPN → Letter Panel (calculator or plot screen)
            rpn_pressed = kb.is_pressed(3, 5)
            shift_held = kb.is_pressed(4, 0)
            if (rpn_pressed and shift_held and not _rpn_shift_was_pressed
                    and (current_screen is calc_screen or current_screen is plot_screen)):
                letter_panel.input_box = current_screen.input_box
                _letter_parent = current_screen
                next_screen = letter_panel
            _rpn_shift_was_pressed = rpn_pressed and shift_held

            # Execute transition if screen changed
            if next_screen is not None and next_screen is not current_screen:
                current_screen.deactivate()
                _screen_transition(display, current_screen, next_screen, font_small)
                current_screen = next_screen

            # ANG key (global — toggles deg/rad, shows on status line everywhere)
            ang_pressed = kb.is_pressed(4, 4)
            if ang_pressed and not _angle_was_pressed:
                calc.functions.ANGLE_MODE = 1 - calc.functions.ANGLE_MODE
                settings["angle_mode"] = calc.functions.ANGLE_MODE
                save_settings(settings)
            _angle_was_pressed = ang_pressed

            # Persist vars
            if current_screen is calc_screen:
                if calc_screen.vars != vars_dict:
                    vars_dict = dict(calc_screen.vars)
                    save_vars(vars_dict)

            time.sleep_ms(10)

        except Exception as e:
            # Crash landing: draw error screen, wait for key, then recover
            _draw_crash(display, e)

            # Emergency memory recovery — clear font caches + GC
            for f in (font_main, font_small):
                if f:
                    f._cache.clear()
            gc.collect()

            # Debounce: wait for key release + new press
            time.sleep_ms(300)
            while True:
                kb.scan()
                if kb.pop_key_event() is not None:
                    break
                time.sleep_ms(20)

            # Recover to main menu
            try:
                current_screen.deactivate()
            except Exception:
                pass
            current_screen = main_menu
            try:
                current_screen.activate()
            except Exception:
                pass
            _last_render = 0


if __name__ == "__main__":
    main()
