"""InputBox widget for expression entry."""
import time
from ui.element import UIElement
from ui.cursor import Cursor
from ui.motion import TEXT_CURSOR_MS
from input.keyboard import get_key_label


UPPER_CONTINUATION_CUE = "^"
LOWER_CONTINUATION_CUE = "v"


class InputBox(UIElement):
    """Editable expression field with a cursor-following text viewport.

    ``visible_rows`` is the maximum number of rows.  The calculator uses up
    to two rows: short expressions stay compact on one row, then expand when
    text wraps by pixel width.  Proportional-font text and the cursor use the
    same measurement.
    """

    def __init__(self, x=0, y=0, width=200, height=12, max_char=42,
                 font=None, visible_rows=1):
        super().__init__(x, y, width, height)
        self.max_char = max(1, max_char)
        self.font = font
        self.visible_rows = max(1, visible_rows)
        self.str = ""
        self.cursor_pos = 0
        self.view_offset = 0
        self.cursor = Cursor(x + 1, y + 1, mode=1)
        self._last_delete = 0
        self._delete_repeat = False
        self.gs = 15
        self._layout_dirty = True
        self._layout_width = width
        self._line_ranges_cache = [(0, 0)]
        self._cursor_origin = None

        # Built-in 8x8 font is monospace with no extra spacing.
        if font:
            self.char_pitch = font.width + 1
            self.glyph_height = font.height
        else:
            self.char_pitch = 8
            self.glyph_height = 8
        self.line_pitch = self.glyph_height + (1 if self.visible_rows > 1 else 0)
        self.visible_chars = max(1, self._text_width() // self.char_pitch)
        self.cursor.height = self._cursor_height()

    def activate(self):
        self.cursor.is_visible = True

    def deactivate(self):
        self.cursor.is_visible = False

    def animation_children(self):
        return (self.cursor,)

    def release_memory(self):
        """Forget only the derived line-layout cache, never the input text."""
        released = len(self._line_ranges_cache) > 1
        self._line_ranges_cache = ()
        self._layout_dirty = True
        return released

    def get_str(self):
        return self.str

    @property
    def active_rows(self):
        """Number of rows currently needed, bounded by ``visible_rows``."""
        return min(self.visible_rows, max(1, len(self._line_ranges())))

    def set_height(self, height):
        """Resize the field and immediately realign its cursor."""
        height = max(3, height)
        if self.height != height:
            self.height = height
            self._update_cursor_target(immediate=True)

    def set_str(self, s, immediate=False):
        self.str = s[:self.max_char]
        self.cursor_pos = min(self.cursor_pos, len(self.str))
        self._invalidate_layout()
        self._clamp_view()
        self._update_cursor_target(immediate=immediate)

    def clear_str(self):
        self.str = ""
        self.cursor_pos = 0
        self.view_offset = 0
        self._invalidate_layout()
        self._update_cursor_target()

    def insert_str(self, s):
        pos = self.cursor_pos
        if len(self.str) + len(s) > self.max_char:
            return False
        self.str = self.str[:pos] + s + self.str[pos:]
        self.cursor_pos += len(s)
        self._invalidate_layout()
        self._clamp_view()
        self._update_cursor_target()
        return True

    def delete_str(self):
        """Backspace: delete character before cursor."""
        if self.cursor_pos > 0:
            pos = self.cursor_pos
            self.str = self.str[:pos - 1] + self.str[pos:]
            self.cursor_pos -= 1
            self._invalidate_layout()
            self._clamp_view()
            self._update_cursor_target()
            return True
        return False

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

    def _invalidate_layout(self):
        self._layout_dirty = True

    def _text_width(self):
        # The two-row editor reserves a narrow gutter for continuation cues.
        cue_width = 6 if self.visible_rows > 1 else 0
        return max(1, self.width - 2 - cue_width)

    def _character_width(self, char):
        if self.font:
            return self.font.measure_text(char, spacing=1)
        return 8

    def _line_ranges(self):
        """Return wrapped (start, end) ranges for the current expression.

        The result is cached until text or width changes.  This avoids a new
        list allocation on every rendered frame while the user is idle.
        """
        if not self._layout_dirty and self._layout_width == self.width:
            return self._line_ranges_cache

        text = self.str
        if not text:
            ranges = [(0, 0)]
        else:
            ranges = []
            start = 0
            limit = self._text_width()
            text_len = len(text)
            while start < text_len:
                end = start
                used = 0
                while end < text_len:
                    char_width = self._character_width(text[end])
                    if end > start and used + char_width > limit:
                        break
                    used += char_width
                    end += 1
                ranges.append((start, end))
                start = end

        self._line_ranges_cache = ranges
        self._layout_dirty = False
        self._layout_width = self.width
        return ranges

    def _cursor_line_index(self, ranges):
        if self.cursor_pos >= len(self.str):
            return len(ranges) - 1
        for index, (_, end) in enumerate(ranges):
            if self.cursor_pos < end:
                return index
        return len(ranges) - 1

    def _view_line_index(self, ranges):
        for index, (start, _) in enumerate(ranges):
            if start == self.view_offset:
                return index
        return 0

    def _clamp_view(self):
        """Keep the cursor in the visible wrapped line window."""
        ranges = self._line_ranges()
        cursor_line = self._cursor_line_index(ranges)
        view_line = self._view_line_index(ranges)
        active_rows = self.active_rows

        if cursor_line < view_line:
            view_line = cursor_line
        elif cursor_line >= view_line + active_rows:
            view_line = cursor_line - active_rows + 1

        self.view_offset = ranges[view_line][0]

    def _visible_ranges(self):
        self._clamp_view()
        ranges = self._line_ranges()
        view_line = self._view_line_index(ranges)
        return ranges[view_line:view_line + self.active_rows]

    def _text_y(self, row):
        active_rows = self.active_rows
        if active_rows == 1:
            return self.y + 1
        content_height = (self.glyph_height * active_rows
                          + (active_rows - 1))
        top = max(1, (self.height - content_height) // 2)
        return self.y + top + row * self.line_pitch

    def _cursor_height(self):
        if self.active_rows == 1:
            return max(1, self.height - 2)
        return self.glyph_height

    def _update_cursor_target(self, immediate=False):
        self._clamp_view()
        ranges = self._line_ranges()
        cursor_line = self._cursor_line_index(ranges)
        view_line = self._view_line_index(ranges)
        visible_row = cursor_line - view_line
        line_start = ranges[cursor_line][0]
        visible_before = self.str[line_start:self.cursor_pos]
        char_x = (self.font.measure_text(visible_before, spacing=1)
                  if self.font else len(visible_before) * 8)
        target_x = self.x + 1 + char_x
        target_y = self._text_y(visible_row)

        self.cursor.height = self._cursor_height()
        self._cursor_origin = (self.x, self.y)
        if immediate:
            self.cursor.x = target_x
            self.cursor.y = target_y
            self.cursor.target_x = target_x
            self.cursor.target_y = target_y
        else:
            self.cursor.change_target(target_x, target_y, TEXT_CURSOR_MS)

    def draw(self, display):
        # PlotScreen moves this widget during its editor animation.  Its text
        # and caret must move together instead of retaining a stale y target.
        if self._cursor_origin != (self.x, self.y):
            self._update_cursor_target(immediate=True)

        display.draw_rectangle(self.x, self.y, self.width, self.height, self.gs)
        display.fill_rectangle(self.x + 1, self.y + 1,
                               max(0, self.width - 2), max(0, self.height - 2), 0)

        visible_ranges = self._visible_ranges()
        for row, (start, end) in enumerate(visible_ranges):
            visible = self.str[start:end]
            if not visible:
                continue
            if self.font:
                # raw=True: don't cache — input text changes every keystroke
                display.draw_text(self.x + 1, self._text_y(row), visible, self.font,
                                  gs=self.gs, raw=True)
            else:
                display.draw_text8x8(self.x + 1, self._text_y(row), visible, gs=self.gs)

        if self.active_rows > 1 and self.font:
            ranges = self._line_ranges()
            view_line = self._view_line_index(ranges)
            cue_x = self.x + self.width - 6
            if view_line > 0:
                display.draw_text(cue_x, self._text_y(0),
                                  UPPER_CONTINUATION_CUE, self.font,
                                  gs=self.gs, raw=True)
            if view_line + len(visible_ranges) < len(ranges):
                cue_row = len(visible_ranges) - 1
                display.draw_text(cue_x, self._text_y(cue_row),
                                  LOWER_CONTINUATION_CUE, self.font,
                                  gs=self.gs, raw=True)
        self.cursor.draw(display)

    def update(self, kb, event=None):
        # DEL long-press repeat (backspace behavior)
        if kb.is_pressed(4, 3):
            hold = kb.get_hold_time(4, 3)
            if hold > 750:
                if not self._delete_repeat:
                    self._delete_repeat = True
                now = time.ticks_ms()
                if time.ticks_diff(now, self._last_delete) > 100:
                    changed = self.delete_str()
                    self._last_delete = now
                    if changed:
                        return "DELETE"
        else:
            self._delete_repeat = False

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
            return "DELETE" if self.delete_str() else None

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
