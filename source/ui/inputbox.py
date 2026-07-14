# ponytail: single-line text input with cursor and scrolling
"""InputBox widget for expression entry."""
import time
from ui.element import UIElement
from ui.cursor import Cursor
from input.keyboard import get_key_label


class InputBox(UIElement):
    def __init__(self, x=0, y=0, width=200, height=12, max_char=42, font=None):
        super().__init__(x, y, width, height)
        self.max_char = max_char
        self.font = font
        self.str = ""
        self.cursor_pos = 0
        self.view_offset = 0
        self.cursor = Cursor(x + 1, y + 1, mode=1)  # line cursor, aligned with text baseline
        self.cursor.height = height - 2  # fit inside input box border
        self._last_delete = 0
        self._delete_repeat = False
        self.gs = 15
        # ponytail: built-in 8x8 font is monospace 8px, no spacing
        if font:
            self.char_pitch = font.width + 1  # max width + spacing for monospace fallback
            self.visible_chars = max(1, width // self.char_pitch)
        else:
            self.char_pitch = 8  # built-in framebuf.text has no spacing
            self.visible_chars = max(1, width // 8)

    def activate(self):
        self.cursor.is_visible = True

    def deactivate(self):
        self.cursor.is_visible = False

    def get_str(self):
        return self.str

    def set_str(self, s):
        self.str = s[:self.max_char]
        self.cursor_pos = min(self.cursor_pos, len(self.str))
        self._clamp_view()

    def clear_str(self):
        self.str = ""
        self.cursor_pos = 0
        self.view_offset = 0
        self._update_cursor_target()

    def insert_str(self, s):
        pos = self.cursor_pos
        if len(self.str) + len(s) > self.max_char:
            return
        self.str = self.str[:pos] + s + self.str[pos:]
        self.cursor_pos += len(s)
        self._clamp_view()
        self._update_cursor_target()

    def delete_str(self):
        """Backspace: delete character before cursor."""
        if self.cursor_pos > 0:
            pos = self.cursor_pos
            self.str = self.str[:pos - 1] + self.str[pos:]
            self.cursor_pos -= 1
            self._clamp_view()
            self._update_cursor_target()

    def move_cursor_left(self):
        if self.cursor_pos > 0:
            self.cursor_pos -= 1
            self._clamp_view()
            self._update_cursor_target()

    def move_cursor_right(self):
        if self.cursor_pos < len(self.str):
            self.cursor_pos += 1
            self._clamp_view()
            self._update_cursor_target()

    def move_cursor_home(self):
        self.cursor_pos = 0
        self.view_offset = 0
        self._update_cursor_target()

    def move_cursor_end(self):
        self.cursor_pos = len(self.str)
        self._clamp_view()
        self._update_cursor_target()

    def _clamp_view(self):
        # Keep cursor visible
        if self.cursor_pos < self.view_offset:
            self.view_offset = self.cursor_pos
        if self.cursor_pos > self.view_offset + self.visible_chars - 2:
            self.view_offset = self.cursor_pos - self.visible_chars + 2
        # ponytail: don't scroll past end of text
        max_offset = max(0, len(self.str) - self.visible_chars + 1)
        if self.view_offset > max_offset:
            self.view_offset = max_offset
        self.view_offset = max(0, self.view_offset)

    def _update_cursor_target(self):
        # ponytail: measure actual pixel offset of text before cursor
        visible_before = self.str[self.view_offset:self.cursor_pos]
        if self.font and visible_before:
            char_x = self.font.measure_text(visible_before, spacing=1)
        elif self.font:
            char_x = 0
        else:
            # Built-in 8x8 font: exactly 8px per char, no spacing
            char_x = (self.cursor_pos - self.view_offset) * 8
        self.cursor.change_target(
            self.x + 1 + char_x, self.y + 1, 200)

    def draw(self, display):
        display.draw_rectangle(self.x, self.y, self.width, self.height, self.gs)
        visible = self.str[self.view_offset:self.view_offset + self.visible_chars]
        if visible:
            if self.font:
                # raw=True: don't cache — input text changes every keystroke
                display.draw_text(self.x + 1, self.y + 1, visible, self.font,
                                  gs=self.gs, raw=True)
            else:
                display.draw_text8x8(self.x + 1, self.y + 1, visible, gs=self.gs)
        self.cursor.draw(display)

    def update(self, kb):
        # DEL long-press repeat (backspace behavior)
        if kb.is_pressed(4, 3):
            hold = kb.get_hold_time(4, 3)
            if hold > 750:
                if not self._delete_repeat:
                    self._delete_repeat = True
                now = time.ticks_ms()
                if time.ticks_diff(now, self._last_delete) > 100:
                    self.delete_str()
                    self._last_delete = now
        else:
            self._delete_repeat = False

        event = kb.pop_key_event()
        if event is None:
            return None

        r, c, shift = event
        label = get_key_label(r, c, shift)

        # Navigation
        if label == "left":
            self.move_cursor_left()
            return None
        if label == "right":
            self.move_cursor_right()
            return None
        if label == "DEL":
            self.delete_str()  # backspace
            return "DELETE"

        # Special keys: pass through to screen
        if label in ("ENT", "ESC", "tab", "stab", "ang", "rpn"):
            return label

        # Function keys: auto-wrap with (
        func_keys = {
            "sin": "sin(", "cos": "cos(", "tan": "tan(",
            "sec": "sec(", "csc": "csc(", "cot": "cot(",
            "asin": "asin(", "acos": "acos(", "atan": "atan(",
            "ln": "ln(", "exp": "exp(", "sqrt": "sqrt(",
        }
        if label in func_keys:
            self.insert_str(func_keys[label])
            return label

        # Regular character keys
        if label and len(label) == 1:
            self.insert_str(label)
            return label

        return None
