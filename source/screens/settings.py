"""User settings screen with persistent, immediately applied controls."""
from input.keyboard import get_key_label
from ui.element import UIElement
from ui.menu import Menu
from ui.theme import draw_footer, draw_header
from version import VERSION
from calc.number import (DEFAULT_DISPLAY_DIGITS, MAX_DISPLAY_DIGITS,
                         MIN_DISPLAY_DIGITS)


BRIGHTNESS_MIN = 10
BRIGHTNESS_MAX = 100
BRIGHTNESS_STEP = 10


class SettingsScreen(UIElement):
    """Show firmware information and small hardware-facing preferences."""

    def __init__(self, font, display, settings, about_screen,
                 request_save=None, on_display_digits_change=None):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.display = display
        self.settings = settings
        self.about_screen = about_screen
        self._request_save = request_save
        self._on_display_digits_change = on_display_digits_change
        self._save_failed = False
        self._save_pending = False
        self.menu = Menu(0, 13, 210, 4, 10, font)
        self._build_rows()

    def _build_rows(self):
        self.menu.clear_items()
        brightness = self._brightness()
        self.menu.add_item("Version  " + VERSION, None)
        self.menu.add_item("About", self.about_screen)
        self.menu.add_item("Brightness  " + str(brightness) + "%", None)
        self.menu.add_item("Display digits  " + str(self._display_digits()), None)

    def _brightness(self):
        value = self.settings.get("brightness", 100)
        if not isinstance(value, int) or value < BRIGHTNESS_MIN:
            return 100
        return min(BRIGHTNESS_MAX, value)

    def _display_digits(self):
        value = self.settings.get("display_digits", DEFAULT_DISPLAY_DIGITS)
        if not isinstance(value, int):
            return DEFAULT_DISPLAY_DIGITS
        return max(MIN_DISPLAY_DIGITS, min(MAX_DISPLAY_DIGITS, value))

    def _request_persist(self):
        self._save_pending = True
        self._save_failed = False
        if self._request_save is None:
            self._save_pending = False
            self._save_failed = True
        else:
            self._request_save(self.settings, self._on_save_result)

    def _change_brightness(self, delta, wrap=False):
        value = self._brightness() + delta
        if wrap and value > BRIGHTNESS_MAX:
            value = BRIGHTNESS_MIN
        else:
            value = max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, value))
        if value == self._brightness():
            return
        self.settings["brightness"] = value
        self.display.set_brightness(value)
        self._request_persist()
        # Only the value changes; replacing the row avoids resetting selection.
        self.menu.items[2] = ("Brightness  " + str(value) + "%", None)

    def _change_display_digits(self, delta, wrap=False):
        value = self._display_digits() + delta
        if wrap and value > MAX_DISPLAY_DIGITS:
            value = MIN_DISPLAY_DIGITS
        else:
            value = max(MIN_DISPLAY_DIGITS, min(MAX_DISPLAY_DIGITS, value))
        if value == self._display_digits():
            return
        self.settings["display_digits"] = value
        if self._on_display_digits_change is not None:
            self._on_display_digits_change(value)
        self._request_persist()
        self.menu.items[3] = ("Display digits  " + str(value), None)

    def _on_save_result(self, success):
        self._save_pending = not success
        self._save_failed = not success

    def activate(self):
        self.menu.activate()

    def animation_children(self):
        return (self.menu,)

    def draw(self, display):
        draw_header(display, "Settings", self.font)
        self.menu.draw(display)
        if self._save_failed:
            draw_footer(display, "Save failed - check SD", self.font)
        elif self._save_pending:
            draw_footer(display, "Saving...", self.font)
        elif self.menu.cursor_pos in (2, 3):
            draw_footer(display, "4/6 adjust", self.font, "ENT next")
        elif self.menu.cursor_pos == 1:
            draw_footer(display, "ENT open", self.font)
        else:
            draw_footer(display, "Firmware version", self.font)

    def update(self, kb, event=None):
        if event is None:
            return None
        row, col, shift = event
        label = get_key_label(row, col, shift)
        if self.menu.cursor_pos == 2:
            if label in ("4", "left"):
                self._change_brightness(-BRIGHTNESS_STEP)
                return "REDRAW"
            if label in ("6", "right"):
                self._change_brightness(BRIGHTNESS_STEP)
                return "REDRAW"
        elif self.menu.cursor_pos == 3:
            if label in ("4", "left"):
                self._change_display_digits(-1)
                return "REDRAW"
            if label in ("6", "right"):
                self._change_display_digits(1)
                return "REDRAW"

        action = self.menu.update(kb, event)
        if action == "BACK":
            return "BACK"
        if action == "ENTER":
            if self.menu.cursor_pos == 1:
                return self.about_screen
            if self.menu.cursor_pos == 2:
                self._change_brightness(BRIGHTNESS_STEP, wrap=True)
                return "REDRAW"
            if self.menu.cursor_pos == 3:
                self._change_display_digits(1, wrap=True)
                return "REDRAW"
        return None
