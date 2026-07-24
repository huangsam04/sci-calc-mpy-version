"""Shared 256x64 layout and grayscale helpers."""

SCREEN_W = 256
SCREEN_H = 64
CONTENT_W = 210
SIDEBAR_X = 213
SIDEBAR_W = 42
TITLE_Y = 1
TITLE_LINE_Y = 11
FOOTER_Y = 54
TEXT_X = 3

GS_TEXT = 15
GS_MUTED = 9
GS_LINE = 8
GS_SELECTED = 13
GS_POPUP = 3


def text_width(text, font):
    return font.measure_text(text) if font else len(text) * 8


def fit_text(text, width, font):
    if text_width(text, font) <= width:
        return text
    suffix = "~"
    while text and text_width(text + suffix, font) > width:
        text = text[:-1]
    return text + suffix


def draw_text(display, x, y, text, font=None, gs=GS_TEXT, invert=False,
              raw=False):
    if font:
        display.draw_text(
            x, y, text, font, invert=invert, gs=gs, raw=raw)
    else:
        display.draw_text8x8(x, y, text, gs=0 if invert else gs)


def draw_header(display, title, font=None, raw=False):
    draw_text(display, TEXT_X, TITLE_Y,
              fit_text(title, CONTENT_W - 6, font), font, raw=raw)
    display.draw_hline(0, TITLE_LINE_Y, CONTENT_W, GS_LINE)


def draw_footer(display, hint, font=None, right="", raw=False):
    display.fill_rectangle(0, FOOTER_Y, CONTENT_W, SCREEN_H - FOOTER_Y, 0)
    display.draw_hline(0, FOOTER_Y, CONTENT_W, GS_LINE)
    fitted_hint = fit_text(hint, 126, font)
    direct = getattr(display, "draw_text_direct", None)
    if font and direct is not None:
        direct(TEXT_X, FOOTER_Y + 2, fitted_hint, font, gs=GS_MUTED)
    else:
        draw_text(display, TEXT_X, FOOTER_Y + 2,
                  fitted_hint, font, GS_MUTED, raw=raw)
    if right:
        fitted = fit_text(right, 76, font)
        x = max(130, CONTENT_W - text_width(fitted, font) - 2)
        if font and direct is not None:
            direct(x, FOOTER_Y + 2, fitted, font, gs=GS_TEXT)
        else:
            draw_text(display, x, FOOTER_Y + 2, fitted, font, GS_TEXT,
                      raw=raw)


def draw_footer_fast(display, hint, hint_bytes, font=None, right=""):
    """Draw a static footer hint without building a string framebuffer."""
    if not font or not hint_bytes:
        draw_footer(display, hint, font, right)
        return
    display.fill_rectangle(0, FOOTER_Y, CONTENT_W, SCREEN_H - FOOTER_Y, 0)
    display.draw_hline(0, FOOTER_Y, CONTENT_W, GS_LINE)
    display.draw_text_direct(TEXT_X, FOOTER_Y + 2, hint_bytes, font,
                             gs=GS_MUTED)
    if right:
        fitted = fit_text(right, 76, font)
        x = max(130, CONTENT_W - text_width(fitted, font) - 2)
        display.draw_text_direct(x, FOOTER_Y + 2, fitted.encode(), font,
                                 gs=GS_TEXT)


def draw_empty(display, message, font=None, y=30):
    fitted = fit_text(message, CONTENT_W - 12, font)
    x = max(4, (CONTENT_W - text_width(fitted, font)) // 2)
    draw_text(display, x, y, fitted, font, GS_MUTED)


SHELL_MAIN_MENU = 0
SHELL_CALCULATOR = 1
SHELL_PLOT = 2
SHELL_FUNCTION_PANEL = 3
SHELL_STOPWATCH = 4
SHELL_SETTINGS = 5
SHELL_LETTERS = 6
SHELL_ABOUT = 7
SHELL_FUNCTION_PICKER = 8
SHELL_VARIABLE_PANEL = 9


def _draw_shell_font(display, x, y, text, encoded, font, gs=15):
    if font:
        display.draw_text_direct(x, y, encoded, font, gs=gs)
    else:
        display.draw_text8x8(x, y, text, gs=gs)


def _draw_shell_header(display, text, encoded, font):
    _draw_shell_font(display, 3, 1, text, encoded, font)
    display.draw_hline(0, 11, CONTENT_W, GS_LINE)


def _draw_shell_footer(display):
    display.fill_rectangle(0, 54, CONTENT_W, 10, 0)
    display.draw_hline(0, 54, CONTENT_W, GS_LINE)


def _draw_plot_shell(display):
    graph_h = 54
    display.draw_rectangle(1, 0, 208, graph_h, 8)
    display.draw_hline(2, 27, 207, 6)
    display.draw_vline(105, 0, graph_h + 1, 6)
    display.draw_pixel(103, 27, 12)
    display.draw_pixel(107, 27, 12)
    display.draw_pixel(105, 25, 12)
    display.draw_pixel(105, 29, 12)
    _draw_shell_footer(display)


def _draw_letter_shell(display):
    display.draw_text8x8(2, 1, "[", gs=15)
    display.draw_vline(10, 1, 8, 15)
    display.draw_text8x8(12, 1, "]", gs=15)
    rows = (
        ("ESC", " A ", " B ", " C ", " D ", " E "),
        (" F ", " G ", " H ", " I ", " J ", " K "),
        (" L ", " M ", " N ", " O ", " P ", " Q "),
        (" R ", " S ", " T ", " X ", " Y ", " Z "),
        ("Sh ", "   ", ' " ', " ; ", "Bk ", "OK "),
    )
    row_index = 0
    while row_index < len(rows):
        row = rows[row_index]
        y = 12 + row_index * 8
        column = 0
        while column < len(row):
            display.draw_text8x8(
                4 + column * 32, y, row[column], gs=15)
            column += 1
        row_index += 1
    _draw_shell_footer(display)


def draw_page_shell(display, kind, font=None):
    """Draw real page geometry with state-dependent content omitted."""
    if kind == SHELL_MAIN_MENU:
        _draw_shell_header(display, "SCI-CALC", b"SCI-CALC", font)
        display.draw_rectangle(0, 13, CONTENT_W, 48, 15)
        return
    if kind == SHELL_CALCULATOR:
        display.draw_rectangle(0, 0, CONTENT_W, 12, 15)
        display.draw_vline(1, 1, 10, 15)
        display.draw_hline(0, 13, CONTENT_W, GS_LINE)
        _draw_shell_footer(display)
        return
    if kind == SHELL_PLOT:
        _draw_plot_shell(display)
        return
    if kind == SHELL_FUNCTION_PANEL:
        _draw_shell_header(display, "Functions", b"Functions", None)
        display.draw_rectangle(0, 13, CONTENT_W, 40, 15)
        _draw_shell_footer(display)
        return
    if kind == SHELL_STOPWATCH:
        timer = "00:00:00"
        if font:
            x = max(2, (CONTENT_W - font.measure_text(timer)) // 2)
            display.draw_text_direct(x, 2, b"00:00:00", font, gs=15)
        else:
            display.draw_text8x8(60, 2, timer, gs=15)
        display.draw_hline(0, 12, CONTENT_W, 15)
        _draw_shell_footer(display)
        return
    if kind == SHELL_SETTINGS:
        _draw_shell_header(display, "Settings", b"Settings", font)
        display.draw_rectangle(0, 13, CONTENT_W, 40, 15)
        _draw_shell_font(display, 4, 15, "Version", b"Version", font)
        _draw_shell_font(display, 4, 25, "About", b"About", font)
        _draw_shell_font(
            display, 4, 35, "Brightness", b"Brightness", font)
        _draw_shell_font(
            display, 4, 45, "Display digits", b"Display digits", font)
        _draw_shell_footer(display)
        return
    if kind == SHELL_LETTERS:
        _draw_letter_shell(display)
        return
    if kind == SHELL_ABOUT:
        _draw_shell_font(
            display, 5, 2, "SCI-CALC", b"SCI-CALC", font)
        return
    if kind == SHELL_FUNCTION_PICKER:
        _draw_shell_header(display, "Functions", b"Functions", font)
        display.draw_rectangle(0, 13, CONTENT_W, 40, 15)
        _draw_shell_footer(display)
        return
    if kind == SHELL_VARIABLE_PANEL:
        _draw_shell_header(display, "Variables", b"Variables", font)
        display.draw_rectangle(0, 13, CONTENT_W, 40, 15)
        _draw_shell_footer(display)
        return
    raise ValueError("Unknown page shell")
