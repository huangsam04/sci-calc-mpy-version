"""Consistent, actionable error presentation for calculator screens."""
import time

from anim.engine import cancel_animations, insert_animation
from ui.motion import DIALOG_ENTER_MS, MOTION_EASING
from ui.theme import CONTENT_W, GS_MUTED, fit_text, draw_text


ERROR_TIMEOUT_MS = 10_000
PANEL_X = 5
PANEL_Y = 4
PANEL_START_Y = 8
PANEL_W = CONTENT_W - PANEL_X * 2
PANEL_H = 56


def friendly_error(message):
    """Convert internal errors into a short title and a useful next action."""
    text = str(message) if message else "Unknown error"
    lower = text.lower()
    if "division by zero" in lower or "divide by zero" in lower:
        return "Cannot divide by zero", "Change the denominator"
    if "undefined variable" in lower:
        return "Unknown variable", "Define it first, e.g. x=2"
    if "math domain" in lower or "domain error" in lower:
        return "Outside valid range", "Check the function input"
    if ("overflow" in lower or "result too large" in lower
            or "decimal exponent is too large" in lower
            or "outside float range" in lower):
        return "Result is too large", "Use a smaller value"
    if "non-finite" in lower:
        return "Invalid numeric result", "Check the add-on calculation"
    if "angle is too large" in lower or "exponent is too large" in lower:
        return "Input is too large", "Use a smaller value"
    if "convert" in lower and "float" in lower:
        return "Expected a number", "Check the entered value"
    if "missing closing" in lower or "unterminated" in lower:
        return "Incomplete expression", "Check brackets or quotes"
    if "needs at least" in lower or "missing argument" in lower:
        return "Not enough arguments", "Check commas and parameters"
    if "no longer loaded" in lower:
        return "Function disabled", "Enable it in Functions"
    if "invalid character" in lower or "unexpected operator" in lower:
        return "Unsupported symbol", "Check the highlighted position"
    if "too deeply nested" in lower:
        return "Expression too complex", "Use fewer nested brackets"
    if "did not converge" in lower or "derivative is too small" in lower:
        return "No solution found", "Try another starting value"
    if "cannot evaluate" in lower:
        return "Cannot draw function", "Check x and the expression"
    return "Calculation error", text


class ErrorPopup:
    """Own error text, timeout and rendering behind one small interface."""

    def __init__(self, font=None, small_font=None):
        self.font = font
        self.small_font = small_font or font
        self.expr = ""
        self.position = 0
        self.title = ""
        self.detail = ""
        self.started = 0
        self.active = False
        self._shade = 0
        self._panel_y = PANEL_START_Y

    def show(self, expr, message, position=None):
        cancel_animations(self)
        self.expr = str(expr or "")
        self.position = -1 if position is None else max(0, int(position))
        self.title, self.detail = friendly_error(message)
        self.started = time.ticks_ms()
        self.active = True
        self._shade = 0
        self._panel_y = PANEL_START_Y
        insert_animation(self, "_shade", 0, 15,
                         DIALOG_ENTER_MS, MOTION_EASING)
        insert_animation(self, "_panel_y", PANEL_START_Y, PANEL_Y,
                         DIALOG_ENTER_MS, MOTION_EASING)

    def dismiss(self):
        cancel_animations(self)
        self.active = False
        self._shade = 0
        self._panel_y = PANEL_START_Y

    def release_memory(self):
        """Drop retained error strings once the owning page is inactive."""
        released = bool(self.expr or self.title or self.detail or self.active)
        self.dismiss()
        self.expr = ""
        self.title = ""
        self.detail = ""
        self.position = 0
        return released

    def expired(self, now=None):
        if not self.active:
            return False
        if now is None:
            now = time.ticks_ms()
        return time.ticks_diff(now, self.started) >= ERROR_TIMEOUT_MS

    def draw(self, display):
        shade = max(0, min(15, int(self._shade)))
        muted = max(1, shade - 5)
        panel_y = int(self._panel_y)
        display.fill_rectangle(0, 0, CONTENT_W, 64, max(1, (shade + 4) // 5))
        display.fill_rectangle(PANEL_X, panel_y, PANEL_W, PANEL_H, 0)
        display.draw_rectangle(PANEL_X, panel_y, PANEL_W, PANEL_H,
                               max(1, shade))

        expression = fit_text(self.expr, CONTENT_W - 20, self.font)
        draw_text(display, 10, panel_y + 4, expression, self.font, shade)
        if self.position >= 0:
            prefix = self.expr[:self.position]
            if self.font:
                caret_x = 10 + self.font.measure_text(prefix)
            else:
                caret_x = 10 + len(prefix) * 8
            if caret_x < CONTENT_W - 12:
                draw_text(display, caret_x, panel_y + 14, "^", self.font,
                          shade)

        draw_text(display, 10, panel_y + 25,
                  fit_text(self.title, CONTENT_W - 20, self.small_font),
                  self.small_font, shade)
        draw_text(display, 10, panel_y + 35,
                  fit_text(self.detail, CONTENT_W - 20, self.small_font),
                  self.small_font, min(GS_MUTED, muted))
        draw_text(display, 10, panel_y + 46, "Any key: dismiss",
                  self.small_font, min(GS_MUTED, muted))
