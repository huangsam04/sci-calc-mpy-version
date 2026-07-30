"""Menu widget: scrollable list with allocation-free cursor feedback."""

from ui.element import UIElement
from ui.cursor import Cursor
from input.keyboard import get_key_label
from ui.motion import DAMAGE_FULL, DAMAGE_NONE, DAMAGE_PARTIAL


MENU_REPEAT_DELAY_MS = 250
MENU_REPEAT_INTERVAL_MS = 90
UP_KEY = (1, 1)
DOWN_KEY = (3, 1)


class Menu(UIElement):
    __slots__ = ("cursor", "_state")

    def __init__(self, x=0, y=0, width=210, visible_rows=5, row_height=12,
                 cursor=None, items=None):
        if cursor is None:
            cursor = Cursor(x + 2, y + 2, mode=0)
        # MicroPython ignores CPython's slot storage and grows an instance map.
        # Keep only two instance keys.  Menu rows share the fixed state table;
        # presented y/width/view offset are packed into one scalar.
        self.cursor = cursor
        self._state = [
            x, y, width, visible_rows * row_height, 0,
            [] if items is None else items, 0, 0, 0]
        # Boot may inject an immutable empty placeholder when a bounded owner
        # replaces it with an exact list before the menu can be activated.
        self.cursor.x = x + 2
        self.cursor.y = y + 2
        self.cursor.mode = 0
        self.cursor.is_visible = True
        self.cursor.gs = 15
        self.cursor.width = 0
        self.cursor.height = row_height - 1
        # State slot 6 is signed (last_hold_ms + 1); its sign is direction.

    @property
    def cursor_pos(self):
        return self._state[7]

    @cursor_pos.setter
    def cursor_pos(self, value):
        self._state[7] = value

    @property
    def view_offset(self):
        return self._state[8]

    @view_offset.setter
    def view_offset(self, value):
        self._state[8] = value

    @property
    def visible_rows(self):
        return self._state[3] // (self.cursor.height + 1)

    def add_item(self, label, target):
        self._state[5].append((label, target))
        self.invalidate_presented()

    def replace_item(self, index, label, target):
        """Replace one dynamic row and invalidate only real pixel changes."""
        items = self._state[5]
        old_label, old_target = items[index]
        if old_label == label and old_target is target:
            return False
        items[index] = (label, target)
        self.invalidate_presented()
        return True

    def invalidate_presented(self):
        """Make the next present redraw labels as well as the cursor band."""
        self._state[4] = 0

    def clear_items(self):
        self._state[5] = []
        self.invalidate_presented()
        self._state[6] = 0

    def get_selected(self):
        items = self._state[5]
        if 0 <= self.cursor_pos < len(items):
            return items[self.cursor_pos][1]
        return None

    def activate(self):
        # Snap cursor to correct position instantly — no animation on activation
        row_height = self.cursor.height + 1
        target_y = (self._state[1] + 2
                    + (self.cursor_pos - self.view_offset) * row_height)
        self.cursor.x = self._state[0] + 2
        self.cursor.y = target_y
        self.cursor.width = self._highlight_width(self.cursor_pos)
        self.invalidate_presented()
        self._state[6] = 0

    def collect_present_damage(self, display_height, damage):
        """Report highlight damage without allocating row-range tuples."""
        state = self._state
        geometry = state[4]
        if not geometry:
            return DAMAGE_FULL
        previous_y = ((geometry >> 8) & 255) - 1
        previous_width = (geometry & 255) - 1
        current_y = int(self.cursor.y)
        current_width = int(self.cursor.width)
        if self.view_offset != (geometry >> 16) - 1:
            damage.add(state[1], state[3])
            return DAMAGE_PARTIAL
        if (current_y == previous_y
                and current_width == previous_width):
            return DAMAGE_NONE
        # Round the affected pixels out to logical menu rows.  The root menu's
        # corresponding framebuffer views are prebuilt by the display driver,
        # so an immediate highlight move remains a bounded partial transfer.
        dirty_start = max(
            state[1] + 1, min(previous_y, current_y) - 2)
        dirty_end = min(
            display_height, state[1] + state[3] - 1,
            max(previous_y, current_y) + 11)
        row_height = self.cursor.height + 1
        last_row = max(0, self.visible_rows - 1)
        first_row = max(
            0, min(last_row, (dirty_start - state[1]) // row_height))
        relative_end = max(0, dirty_end - (state[1] + 2))
        final_row = (
            (relative_end + row_height - 1) // row_height - 1)
        final_row = max(first_row, min(last_row, final_row))
        if final_row == first_row and self.visible_rows > 1:
            if final_row < last_row:
                final_row += 1
            else:
                first_row -= 1
        fixed_start = state[1] + first_row * row_height
        fixed_end = min(
            display_height,
            state[1] + 2 + (final_row + 1) * row_height)
        damage.add(fixed_start, fixed_end - fixed_start)
        return DAMAGE_PARTIAL

    def mark_presented(self):
        self._state[4] = (
            ((self.view_offset + 1) << 16)
            | ((int(self.cursor.y) + 1) << 8)
            | (int(self.cursor.width) + 1))

    def draw_present_rows(self, display):
        """Rebuild only rows touched by the old and current highlights."""
        state = self._state
        geometry = state[4]
        if (not geometry
                or self.view_offset != (geometry >> 16) - 1):
            row_start = state[1] + 1
            row_end = state[1] + state[3] - 1
        else:
            previous_y = ((geometry >> 8) & 255) - 1
            current_y = int(self.cursor.y)
            row_start = max(
                state[1] + 1, min(previous_y, current_y) - 2)
            row_end = min(
                state[1] + state[3] - 1,
                max(previous_y, current_y) + 11)
        if row_end <= row_start:
            return
        display.fill_rectangle(
            state[0] + 1, row_start, state[2] - 2,
            row_end - row_start, 0)
        Menu._draw_rows(self, display, row_start, row_end)

    def move_cursor_up(self):
        if self.cursor_pos > 0:
            self.cursor_pos -= 1
            self._clamp_view()
            self._update_cursor_target()

    def move_cursor_down(self):
        if self.cursor_pos < len(self._state[5]) - 1:
            self.cursor_pos += 1
            self._clamp_view()
            self._update_cursor_target()

    def _clamp_view(self):
        if self.cursor_pos < self.view_offset:
            self.view_offset = self.cursor_pos
        if self.cursor_pos >= self.view_offset + self.visible_rows:
            self.view_offset = self.cursor_pos - self.visible_rows + 1
        self.view_offset = max(0, self.view_offset)

    def _update_cursor_target(self):
        """Snap ordinary menus directly to their logical selection."""
        target_y = (
            self._state[1] + 2
            + (self.cursor_pos - self.view_offset)
            * (self.cursor.height + 1))
        self.cursor.x = self._state[0] + 2
        self.cursor.y = target_y
        self.cursor.width = self._highlight_width(self.cursor_pos)

    def _highlight_width(self, item_pos):
        """Measure the compact highlight bar for one visible menu item."""
        items = self._state[5]
        if not 0 <= item_pos < len(items):
            return 0
        label = items[item_pos][0]
        text_width = len(label) * 8
        return min(self._state[2] - 4, text_width + 4)

    @staticmethod
    def _held_direction(kb):
        up = kb.is_pressed(*UP_KEY)
        down = kb.is_pressed(*DOWN_KEY)
        if up == down:
            return 0
        return -1 if up else 1

    def _repeat_held_direction(self, kb):
        """Repeat a held menu direction without synthesising key edges."""
        direction = self._held_direction(kb)
        repeat_state = self._state[6]
        repeat_direction = (-1 if repeat_state < 0 else
                            1 if repeat_state > 0 else 0)
        if direction == 0 or direction != repeat_direction:
            self._state[6] = 0
            return False
        key = UP_KEY if direction < 0 else DOWN_KEY
        get_hold = getattr(kb, "get_hold_time", None)
        hold_ms = get_hold(*key) if get_hold is not None else 0
        previous_hold_ms = abs(repeat_state) - 1
        if (hold_ms < MENU_REPEAT_DELAY_MS
                or hold_ms - previous_hold_ms < MENU_REPEAT_INTERVAL_MS):
            return False
        self._state[6] = direction * (hold_ms + 1)
        previous = self.cursor_pos
        if direction < 0:
            self.move_cursor_up()
        else:
            self.move_cursor_down()
        return self.cursor_pos != previous

    def draw(self, display):
        state = self._state
        display.draw_rectangle(state[0], state[1], state[2], state[3], 15)
        Menu._draw_rows(
            self, display, state[1] + 1, state[1] + state[3] - 1)

    def _draw_rows(self, display, row_start, row_end):
        """Draw visible menu pixels intersecting one complete-width row band."""
        font_h = 8
        bar_y = self.cursor.y

        # Position and width move together, preserving a coherent marker as
        # the logical selection changes on a physical key edge.
        if (self.cursor.width > 0
                and bar_y < row_end and bar_y + font_h > row_start):
            display.fill_rectangle(self._state[0] + 2, bar_y, self.cursor.width,
                                   font_h, 14)

        # Draw items — only invert text when highlight bar fully covers it
        items = self._state[5]
        i = self.view_offset
        end = min(self.view_offset + self.visible_rows, len(items))
        while i < end:
            label = items[i][0]
            draw_y = (
                self._state[1] + 2 + (i - self.view_offset)
                * (self.cursor.height + 1))
            if draw_y >= row_end or draw_y + font_h <= row_start:
                i += 1
                continue

            if abs(bar_y - draw_y) <= 2:
                display.draw_text8x8(
                    self._state[0] + 4, draw_y, label, gs=0)
            else:
                display.draw_text8x8(
                    self._state[0] + 4, draw_y, label, gs=15)
            i += 1

    def update(self, kb, event=None):
        if event is None:
            return "MOVE" if self._repeat_held_direction(kb) else None

        r, c, shift = event
        label = get_key_label(r, c, shift)
        previous_cursor = self.cursor_pos
        previous_offset = self.view_offset

        # Navigation: 8=UP, 2=DOWN (no shift needed in menus)
        if label == "8" or label == "up":
            self.move_cursor_up()
            self._state[6] = -1
        elif label == "2" or label == "down":
            self.move_cursor_down()
            self._state[6] = 1
        elif label == "ENT":
            self._state[6] = 0
            return "ENTER"
        elif label == "ESC":
            self._state[6] = 0
            return "BACK"
        else:
            self._state[6] = 0

        if (self.cursor_pos != previous_cursor
                or self.view_offset != previous_offset):
            return "MOVE"
        return None
