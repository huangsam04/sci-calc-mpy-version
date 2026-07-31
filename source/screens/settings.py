"""User settings screen with persistent, immediately applied controls."""
from input.keyboard import get_key_label
from ui.element import UIElement
from ui.menu import Menu
from ui.motion import DAMAGE_PARTIAL
from ui.theme import draw_footer, draw_header_fast
from version import VERSION
from calc.number import (DEFAULT_DISPLAY_DIGITS, MAX_DISPLAY_DIGITS,
                         MIN_DISPLAY_DIGITS)


BRIGHTNESS_MIN = 10
BRIGHTNESS_MAX = 100
SettingsScenarioTransaction = None


class SettingsScreen(UIElement):
    """Show firmware information and small hardware-facing preferences."""

    transition_title = "Settings"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("_state",)

    def __init__(self, font, display, settings, about_screen,
                 request_save=None, on_display_digits_change=None,
                 build_rows=True, menu_cursor=None, menu=None):
        # Acquire the fixed Menu block before this instance's scalar writes
        # split the constrained target heap.  Rows remain deferred at boot.
        if menu is None:
            menu = Menu(0, 13, 210, 4, 10, menu_cursor)
        # Keep the resident instance at one key.  Row 0 owns service/object
        # references; row 1 owns the scenario lease and packed save flags
        # (pending=1, failed=2, persistence visual dirty=4).
        self._state = [
            display, about_screen, request_save, on_display_digits_change,
            settings, menu, None, 0]
        if build_rows:
            self._build_rows()

    def _build_rows(self):
        menu = self._state[5]
        menu.clear_items()
        brightness = self._brightness()
        menu.add_item("Version  " + VERSION, None)
        menu.add_item("About", self._state[1])
        menu.add_item("Brightness  " + str(brightness) + "%", None)
        menu.add_item("Display digits  " + str(self._display_digits()), None)

    def _brightness(self):
        value = self._state[4].get("brightness", 100)
        if not isinstance(value, int) or value < BRIGHTNESS_MIN:
            return 100
        return min(BRIGHTNESS_MAX, value)

    def _display_digits(self):
        value = self._state[4].get(
            "display_digits", DEFAULT_DISPLAY_DIGITS)
        if not isinstance(value, int):
            return DEFAULT_DISPLAY_DIGITS
        return max(MIN_DISPLAY_DIGITS, min(MAX_DISPLAY_DIGITS, value))

    def _request_persist(self):
        state = self._state
        state[7] = (state[7] & 4) | 1
        if state[2] is None:
            state[7] = (state[7] & 4) | 2
        else:
            state[2](state[4], self._on_save_result, self)

    def _change_brightness(self, delta, wrap=False):
        value = self._brightness() + delta
        if wrap and value > BRIGHTNESS_MAX:
            value = BRIGHTNESS_MIN
        else:
            value = max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, value))
        if value == self._brightness():
            return False
        state = self._state
        state[4]["brightness"] = value
        state[0].set_brightness(value)
        self._request_persist()
        # Only the value changes; replacing the row avoids resetting selection.
        state[5].replace_item(
            2, "Brightness  " + str(value) + "%", None)
        return True

    def _change_display_digits(self, delta, wrap=False):
        value = self._display_digits() + delta
        if wrap and value > MAX_DISPLAY_DIGITS:
            value = MIN_DISPLAY_DIGITS
        else:
            value = max(MIN_DISPLAY_DIGITS, min(MAX_DISPLAY_DIGITS, value))
        if value == self._display_digits():
            return False
        state = self._state
        state[4]["display_digits"] = value
        if state[3] is not None:
            state[3](value)
        self._request_persist()
        state[5].replace_item(
            3, "Display digits  " + str(value), None)
        return True

    def _on_save_result(self, success):
        save_state = 0 if success else 3
        state = self._state
        if save_state != state[7] & 3:
            state[7] = save_state | 4
            state[5].invalidate_presented()

    def consume_persist_visual_change(self):
        """Let the quiet storage seam request one frame after its callback."""
        state = self._state
        changed = bool(state[7] & 4)
        state[7] &= ~4
        return changed

    def activate(self):
        if self._state[6] is not None:
            raise RuntimeError("Settings scenario transaction is active")
        menu = self._state[5]
        if (not isinstance(menu._state[5], list)
                or len(menu._state[5]) != 4):
            self._build_rows()
        menu.activate()

    def open_scenario_transaction(self):
        """Open the controller-only bounded Settings visible-state lease."""
        transaction_type = SettingsScenarioTransaction
        if transaction_type is None:
            from screens.settings_scenario import (
                SettingsScenarioTransaction as transaction_type)
        return transaction_type(self)

    def _invalidate_scenario_visible_state(self):
        """Forget only redraw caches after a prepared Settings lease closes."""
        self._state[5].invalidate_presented()

    def collect_present_damage(self, damage):
        result = self._state[5].collect_present_damage(
            self.height, damage)
        if result == DAMAGE_PARTIAL:
            damage.add(54, 10)
        return result

    def mark_presented(self):
        self._state[5].mark_presented()

    def _draw_footer(self, display):
        state = self._state
        save_state = state[7]
        menu = state[5]
        if save_state & 2:
            hint = "Save failed"
            right = ""
        elif save_state & 1:
            hint = "Saving..."
            right = ""
        elif menu.cursor_pos in (2, 3):
            hint = "4/6 adjust"
            right = "ENT next"
        elif menu.cursor_pos == 1:
            hint = "ENT open"
            right = ""
        else:
            hint = "Firmware ver."
            right = ""
        draw_footer(display, hint, None, right)

    def draw_present_rows(self, display):
        self._state[5].draw_present_rows(display)
        self._draw_footer(display)

    @property
    def motion_active(self):
        return self._state[5].motion_active

    def advance_motion(self, now):
        return self._state[5].advance_motion(now)

    def draw(self, display):
        draw_header_fast(display, "Settings", b"", None)
        self._state[5].draw(display)
        self._draw_footer(display)

    def update(self, kb, event=None):
        state = self._state
        if state[6] is not None:
            raise RuntimeError("Settings scenario transaction is active")
        menu = state[5]
        if event is None:
            return ("REDRAW"
                    if menu.update(kb, None) == "MOVE" else None)
        row, col, shift = event
        label = get_key_label(row, col, shift)
        if menu.cursor_pos == 2:
            if label in ("4", "left"):
                return ("REDRAW" if self._change_brightness(
                    -10) else None)
            if label in ("6", "right"):
                return ("REDRAW" if self._change_brightness(
                    10) else None)
        elif menu.cursor_pos == 3:
            if label in ("4", "left"):
                return ("REDRAW" if self._change_display_digits(-1)
                        else None)
            if label in ("6", "right"):
                return ("REDRAW" if self._change_display_digits(1)
                        else None)

        action = menu.update(kb, event)
        if action == "MOVE":
            return "REDRAW"
        if action == "BACK":
            return "BACK"
        if action == "ENTER":
            if menu.cursor_pos == 1:
                return state[1]
            if menu.cursor_pos == 2:
                return ("REDRAW" if self._change_brightness(
                    10, wrap=True) else None)
            if menu.cursor_pos == 3:
                return ("REDRAW" if self._change_display_digits(
                    1, wrap=True) else None)
        return None
