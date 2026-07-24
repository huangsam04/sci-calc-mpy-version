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
