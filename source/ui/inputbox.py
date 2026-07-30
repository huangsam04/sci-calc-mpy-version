"""InputBox widget for bounded expression entry."""
import time
from ui.cursor import Cursor
from input.keyboard import get_key_label
from ui.theme import get_direct_text_draw


UPPER_CONTINUATION_CUE = "^"
LOWER_CONTINUATION_CUE = "v"
UPPER_CONTINUATION_CUE_BYTES = b"^"
LOWER_CONTINUATION_CUE_BYTES = b"v"
INPUT_FULL_NOTICE = "Input full"
INPUT_FULL_NOTICE_BYTES = b"Input full"
MAX_VISIBLE_ROWS = 2
_FUNCTION_KEY_INSERTS = {
    "sin": "sin(", "cos": "cos(", "tan": "tan(",
    "sec": "sec(", "csc": "csc(", "cot": "cot(",
    "asin": "asin(", "acos": "acos(", "atan": "atan(",
    "ln": "ln(", "exp": "exp(", "sqrt": "sqrt(",
}


class InputBox:
    """Editable one- or two-row fixed-cell text viewport."""

    __slots__ = (
        "font", "str", "cursor_pos", "view_offset", "cursor", "_state")

    @property
    def x(self):
        return self._state[0] & 511

    @property
    def y(self):
        return (self._state[0] >> 9) & 511

    @y.setter
    def y(self, value):
        self._state[0] = (
            self._state[0] & ~(511 << 9)) | ((value & 511) << 9)

    @property
    def width(self):
        return self._state[1] & 511

    @property
    def height(self):
        return (self._state[1] >> 9) & 511

    @height.setter
    def height(self, value):
        self._state[1] = (
            self._state[1] & ~(511 << 9)) | ((value & 511) << 9)

    def __init__(self, x=0, y=0, width=200, height=12, max_char=42,
                 font=None, visible_rows=1):
        visible_rows = min(MAX_VISIBLE_ROWS, max(1, visible_rows))
        max_char = min(511, max(1, max_char))
        pitch = font.width + 1 if font else 8
        cue_width = 6 if visible_rows > 1 else 0
        visible_chars = max(1, max(1, width - 2 - cue_width) // pitch)
        # Fixed slots: packed geometry/limits, dimensions, delete time, packed
        # shade/layout rows, packed cached window, visible text/bytes, packed
        # cached rows. Visible character count shares the dimensions integer;
        # this seven-slot table needs at most 28 contiguous bytes.
        state = [
            ((x & 511) | ((y & 511) << 9)
             | ((visible_rows - 1) << 18) | (max_char << 19)),
            ((width & 511) | ((height & 511) << 9)
             | ((visible_chars & 511) << 18)),
            0, 15 | (1 << 4) | (1 << 5), 0,
            None, None,
        ]
        state[5] = [""] * visible_rows
        state[6] = [b""] * visible_rows
        self._state = state
        # The device uses the built-in 8x8 path.  A fixed-width host font is
        # still accepted for compatibility tests and direct packed drawing.
        self.font = font
        self.str = ""
        self.cursor_pos = 0
        self.view_offset = 0
        self.cursor = Cursor(x + 1, y + 1, mode=1)
        self.cursor.height = self._cursor_height()

    def activate(self):
        self.cursor.is_visible = True

    def deactivate(self):
        self.cursor.is_visible = False

    def release_memory(self):
        """Release bounded rendered rows without changing input text."""
        released = False
        state = self._state
        visible_text = state[5]
        visible_bytes = state[6]
        visible_rows = ((state[0] >> 18) & 1) + 1
        row = 0
        while row < visible_rows:
            if visible_text[row] or visible_bytes[row]:
                released = True
            visible_text[row] = ""
            visible_bytes[row] = b""
            row += 1
        state[3] |= 1 << 4
        state[4] = 0
        return released

    def get_str(self):
        return self.str

    @property
    def active_rows(self):
        self._ensure_layout()
        return ((self._state[3] >> 12) & 1) + 1

    def set_height(self, height):
        height = max(3, height)
        if self.height != height:
            self.height = height
            self._update_cursor_target(immediate=True)

    def set_str(self, value, immediate=False):
        max_char = (self._state[0] >> 19) & 511
        if len(value) > max_char:
            value = value[:max_char]
        self.str = value
        self.cursor_pos = min(self.cursor_pos, len(value))
        self._invalidate_layout()
        self._clamp_view()
        self._update_cursor_target(immediate=immediate)

    def clear_str(self):
        self.str = ""
        self.cursor_pos = 0
        self.view_offset = 0
        self._invalidate_layout()
        self._update_cursor_target(immediate=True)

    def insert_str(self, value):
        position = self.cursor_pos
        current = self.str
        new_length = len(current) + len(value)
        if new_length > ((self._state[0] >> 19) & 511):
            return False
        if position == len(current):
            # A captured input batch commonly appends several short tokens
            # before one OLED commit.  Keep only the new source string and
            # defer the derived visible-row slice until that draw.
            self.str = current + value
            self.cursor_pos = new_length
            self._invalidate_layout()
            if (new_length <= ((self._state[1] >> 18) & 511)
                    and self.view_offset == 0):
                pitch = self.font.width + 1 if self.font else 8
                self.cursor.change_target(
                    self.x + 1 + new_length * pitch, self.y + 1)
                return True
            self._clamp_view()
            self._update_cursor_target(immediate=True)
            return True
        self.str = current[:position] + value + current[position:]
        self.cursor_pos += len(value)
        self._invalidate_layout()
        self._clamp_view()
        self._update_cursor_target(immediate=True)
        return True

    def try_insert(self, value):
        return self.insert_str(value)

    def delete_str(self):
        if self.cursor_pos <= 0:
            return False
        position = self.cursor_pos
        self.str = self.str[:position - 1] + self.str[position:]
        self.cursor_pos -= 1
        self._invalidate_layout()
        self._clamp_view()
        self._update_cursor_target(immediate=True)
        return True

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

    def move_cursor_end(self):
        self.cursor_pos = len(self.str)
        self._clamp_view()
        self._update_cursor_target()

    def _invalidate_layout(self):
        state = self._state
        state[3] |= 1 << 4
        state[4] &= 127

    def _rebuild_layout(self):
        state = self._state
        length = len(self.str)
        chars = (state[1] >> 18) & 511
        visible_rows = ((state[0] >> 18) & 1) + 1
        line_count = (
            max(1, (length + chars - 1) // chars) if length else 1)
        active_rows = min(visible_rows, line_count)
        state[3] = ((state[3] & ~((127 << 5) | (1 << 12) | (1 << 4)))
                    | (line_count << 5) | ((active_rows - 1) << 12))
        state[4] = 0

    def _cache_visible_rows(self):
        state = self._state
        start = self.view_offset
        length = len(self.str)
        visible_text = state[5]
        visible_bytes = state[6]
        visible_rows = ((state[0] >> 18) & 1) + 1
        active_rows = ((state[3] >> 12) & 1) + 1
        row = 0
        while row < active_rows:
            end = min(length, start + ((state[1] >> 18) & 511))
            visible = self.str[start:end]
            visible_text[row] = visible
            visible_bytes[row] = visible.encode() if self.font else b""
            start = end
            row += 1
        while row < visible_rows:
            visible_text[row] = ""
            visible_bytes[row] = b""
            row += 1

    def _refresh_visible_window(self):
        state = self._state
        chars = (state[1] >> 18) & 511
        line_count = (state[3] >> 5) & 127
        active_rows = ((state[3] >> 12) & 1) + 1
        position = self.cursor_pos
        cursor_line = (0 if position == 0 else
                       min((position - 1) // chars, line_count - 1))
        view_line = max(0, min(
            self.view_offset // chars, line_count - 1))
        if cursor_line < view_line:
            view_line = cursor_line
        elif cursor_line >= view_line + active_rows:
            view_line = cursor_line - active_rows + 1
        state[3] = (state[3] & ~(127 << 13)) | (view_line << 13)
        self.view_offset = view_line * chars
        window = state[4]
        if view_line != (window & 127) - 1:
            self._cache_visible_rows()
            window = (window & ~127) | (view_line + 1)
        state[4] = (window & 127) | ((position + 1) << 7)

    def _ensure_layout(self):
        state = self._state
        changed = False
        if state[3] & (1 << 4):
            self._rebuild_layout()
            changed = True
        expected_offset = (((state[3] >> 13) & 127)
                           * ((state[1] >> 18) & 511))
        if (changed or ((state[4] >> 7) - 1) != self.cursor_pos
                or self.view_offset != expected_offset):
            self._refresh_visible_window()
            changed = True
        return changed

    def _clamp_view(self):
        self._ensure_layout()

    def _text_y(self, row):
        active_rows = ((self._state[3] >> 12) & 1) + 1
        glyph_height = self.font.height if self.font else 8
        if active_rows == 1:
            return self.y + 1
        content_height = glyph_height * active_rows + active_rows - 1
        top = max(1, (self.height - content_height) // 2)
        return self.y + top + row * (glyph_height + 1)

    def _cursor_height(self):
        if not self._state[3] & (1 << 12):
            return max(1, self.height - 2)
        return self.font.height if self.font else 8

    def _update_cursor_target(self, immediate=False):
        self._ensure_layout()
        state = self._state
        chars = (state[1] >> 18) & 511
        line_count = (state[3] >> 5) & 127
        view_line = (state[3] >> 13) & 127
        position = self.cursor_pos
        cursor_line = (0 if position == 0 else
                       min((position - 1) // chars, line_count - 1))
        line_start = cursor_line * chars
        pitch = self.font.width + 1 if self.font else 8
        target_x = self.x + 1 + (position - line_start) * pitch
        target_y = self._text_y(cursor_line - view_line)
        self.cursor.height = self._cursor_height()
        self.cursor.change_target(target_x, target_y)

    def draw(self, display):
        state = self._state
        self._update_cursor_target(immediate=True)

        layout = state[3]
        shade = layout & 15
        active_rows = ((layout >> 12) & 1) + 1
        view_line = (layout >> 13) & 127
        line_count = (layout >> 5) & 127
        display.draw_rectangle(self.x, self.y, self.width, self.height, shade)
        display.fill_rectangle(
            self.x + 1, self.y + 1,
            max(0, self.width - 2), max(0, self.height - 2), 0)

        direct = get_direct_text_draw(display) if self.font else None
        visible_text = state[5]
        visible_bytes = state[6]
        row = 0
        while row < active_rows:
            if visible_text[row]:
                y = self._text_y(row)
                if self.font:
                    if direct is not None:
                        direct(
                            display, self.x + 1, y,
                            visible_bytes[row], self.font, gs=shade)
                    else:
                        display.draw_text(
                            self.x + 1, y, visible_text[row], self.font,
                            gs=shade, raw=True)
                else:
                    display.draw_text8x8(
                        self.x + 1, y, visible_text[row], gs=shade)
            row += 1

        if active_rows > 1 and self.font:
            cue_x = self.x + self.width - 6
            if view_line > 0:
                if direct is not None:
                    direct(
                        display, cue_x, self._text_y(0),
                        UPPER_CONTINUATION_CUE_BYTES, self.font, gs=shade)
                else:
                    display.draw_text(
                        cue_x, self._text_y(0), UPPER_CONTINUATION_CUE,
                        self.font, gs=shade, raw=True)
            if view_line + active_rows < line_count:
                y = self._text_y(active_rows - 1)
                if direct is not None:
                    direct(
                        display, cue_x, y, LOWER_CONTINUATION_CUE_BYTES,
                        self.font, gs=shade)
                else:
                    display.draw_text(
                        cue_x, y, LOWER_CONTINUATION_CUE,
                        self.font, gs=shade, raw=True)
        self.cursor.draw(display)

    def update(self, kb, event=None):
        state = self._state
        if kb.is_pressed(4, 3):
            hold = kb.get_hold_time(4, 3)
            if hold > 750:
                now = time.ticks_ms()
                if time.ticks_diff(now, state[2]) > 100:
                    changed = self.delete_str()
                    state[2] = now
                    if changed:
                        return "DELETE"
        if event is None:
            return None
        row, col, shift = event
        label = get_key_label(row, col, shift)
        if label == "left":
            previous = self.cursor_pos
            self.move_cursor_left()
            return "MOVE" if self.cursor_pos != previous else None
        if label == "right":
            previous = self.cursor_pos
            self.move_cursor_right()
            return "MOVE" if self.cursor_pos != previous else None
        if label == "DEL":
            return "DELETE" if self.delete_str() else None
        if label in ("ENT", "ESC", "tab", "stab", "ang", "rpn"):
            return label
        function_text = _FUNCTION_KEY_INSERTS.get(label)
        if function_text is not None:
            return "CHANGE" if self.insert_str(function_text) else None
        if label and len(label) == 1:
            return "CHANGE" if self.insert_str(label) else None
        return None
