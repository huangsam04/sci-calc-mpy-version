# SCI-CALC MicroPython Firmware
# Main entry point — display init FIRST, then lazy-load everything else
"""SCI-CALC: Multifunctional Scientific Calculator (MicroPython Edition)."""
import time
import gc
from machine import Pin, SPI, ADC

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


def _boot_progress(display, step, total, label=""):
    """Draw a simple boot progress bar."""
    display.clear_buffers(0)
    display.draw_text8x8(75, 8, "SCI-CALC", gs=15)
    bar_x, bar_y, bar_w, bar_h = 20, 28, 216, 8
    display.draw_rectangle(bar_x, bar_y, bar_w, bar_h, 8)
    fill_w = int(bar_w * step / total)
    if fill_w > 0:
        display.fill_rectangle(bar_x + 1, bar_y + 1, fill_w - 1, bar_h - 2, 15)
    if label:
        display.draw_text8x8(20, 42, label, gs=10)
    display.present()


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


def main():
    # ============================================================
    # Phase 1: Display FIRST — show splash immediately
    # ============================================================
    display = _init_display()
    _boot_progress(display, 1, 8, "Display OK")

    # ============================================================
    # Phase 2: Lazy-load everything else while showing progress
    # ============================================================
    from input.keyboard import Keyboard
    kb = Keyboard()
    _boot_progress(display, 2, 8, "Keyboard OK")

    # Fonts
    try:
        font_main = XglcdFont("fonts/Bally7x9.c", 7, 9)
    except Exception:
        font_main = None
    try:
        font_small = XglcdFont("fonts/Neato5x7.c", 5, 7)
    except Exception:
        font_small = None
    _boot_progress(display, 3, 8, "Fonts OK")

    # Settings
    from utils.storage import load_settings
    settings = load_settings()
    import calc.functions
    calc.functions.ANGLE_MODE = settings.get("angle_mode", 0)
    _boot_progress(display, 4, 8, "Settings OK")

    # Variables
    from utils.storage import load_vars
    vars_dict = load_vars()
    _boot_progress(display, 5, 8, "Vars OK")

    # Functions
    func_table = _reload_functions(settings)
    _boot_progress(display, 6, 8, "Functions OK")

    # Screens (import + build)
    from screens.main_menu import MainMenu
    from screens.calculator import CalculatorScreen
    from screens.function_panel import FunctionPanel
    from screens.stopwatch import StopwatchScreen
    from screens.about import AboutScreen
    from screens.letter_panel import LetterPanel
    from screens.function_picker import FunctionPicker
    from screens.variable_panel import VariablePanel
    _boot_progress(display, 7, 8, "Screens OK")

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

    main_menu = MainMenu(font_main)
    main_menu.add_screen("Calculator", calc_screen)
    main_menu.add_screen("Function Panel", func_panel)
    main_menu.add_screen("Stopwatch", stopwatch)
    main_menu.add_screen("About", about)
    calc_screen.parent = main_menu
    func_panel.parent = main_menu
    stopwatch.parent = main_menu
    about.parent = main_menu

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
    _frame = 0
    _last_render = 0
    FRAME_MS = 66

    while True:
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

        # Screen switching
        if result == "BACK":
            if current_screen.parent:
                current_screen.deactivate()
                current_screen = current_screen.parent
                current_screen.activate()
        elif result == "FUNC_PANEL_DONE":
            settings = load_settings()
            func_table = _reload_functions(settings)
            calc_screen.func_table = func_table
            current_screen.deactivate()
            current_screen = main_menu
            current_screen.activate()
        elif result == "FUNC_PICKER":
            current_screen.deactivate()
            func_picker.activate()
            current_screen = func_picker
        elif result == "LETTER_PANEL":
            current_screen.deactivate()
            letter_panel.activate()
            current_screen = letter_panel
        elif result == "LETTER_DONE":
            current_screen.deactivate()
            calc_screen.activate()
            current_screen = calc_screen
        elif result == "FUNC_PICKER_DONE":
            current_screen.deactivate()
            calc_screen.activate()
            current_screen = calc_screen
        elif result == "VARIABLE_PANEL":
            current_screen.deactivate()
            var_panel.activate()
            current_screen = var_panel
        elif result == "VAR_PANEL_DONE":
            current_screen.deactivate()
            calc_screen.activate()
            current_screen = calc_screen
        elif isinstance(result, UIElement) and result is not current_screen:
            current_screen.deactivate()
            result.activate()
            current_screen = result

        # Global Shift+RPN → Letter Panel
        rpn_pressed = kb.is_pressed(3, 5)
        shift_held = kb.is_pressed(4, 0)
        if (rpn_pressed and shift_held and not _rpn_shift_was_pressed
                and current_screen is not letter_panel
                and current_screen is not func_picker):
            current_screen.deactivate()
            letter_panel.activate()
            current_screen = letter_panel
        _rpn_shift_was_pressed = rpn_pressed and shift_held

        # ANG key
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


if __name__ == "__main__":
    main()
