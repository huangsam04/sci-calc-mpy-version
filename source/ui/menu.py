"""Menu widget: scrollable list with immediate cursor feedback."""
from ui.element import UIElement
from ui.cursor import Cursor
from input.keyboard import get_key_label


MENU_REPEAT_DELAY_MS = 250
MENU_REPEAT_INTERVAL_MS = 90
UP_KEY = (1, 1)
DOWN_KEY = (3, 1)


class Menu(UIElement):
    def __init__(self, x=0, y=0, width=210, visible_rows=5, row_height=12, font=None):
        super().__init__(x, y, width, visible_rows * row_height)
        self.items = []           # list of (label, target_element)
        self._encoded_labels = []
        self.visible_rows = visible_rows
        self.row_height = row_height
        self.font = font
        self.cursor_pos = 0
        self.view_offset = 0
        self.cursor = Cursor(x + 2, y + 2, mode=0)
        self.cursor.width = 0
        self.cursor.height = row_height - 1
        self.gs = 15
        self._presented_y = None
        self._presented_width = None
        self._presented_view_offset = None
        self._repeat_direction = 0
        self._repeat_hold_ms = 0

    def add_item(self, label, target):
        # Labels are static, so truncate once instead of every frame.
        if self.font:
            max_w = self.width - 10
            if self.font.measure_text(label) > max_w:
                while len(label) > 0 and self.font.measure_text(label + "~") > max_w:
                    label = label[:-1]
                label += "~"
        self.items.append((label, target))
        self._encoded_labels.append(label.encode() if self.font else None)

    def replace_item(self, index, label, target):
        """Replace one dynamic row without invalidating its packed label."""
        self.items[index] = (label, target)
        self._encoded_labels[index] = label.encode() if self.font else None

    def clear_items(self):
        self.items = []
        self._encoded_labels = []
        self._presented_y = None
        self._repeat_direction = 0
        self._repeat_hold_ms = 0

    def get_selected(self):
        if 0 <= self.cursor_pos < len(self.items):
            return self.items[self.cursor_pos][1]
        return None

    def activate(self):
        # Snap cursor to correct position instantly — no animation on activation
        target_y = self.y + 2 + (self.cursor_pos - self.view_offset) * self.row_height
        self.cursor.x = self.x + 2
        self.cursor.y = target_y
        self.cursor.width = self._highlight_width(self.cursor_pos)
        self._presented_y = None
        self._presented_width = None
        self._presented_view_offset = None
        self._repeat_direction = 0
        self._repeat_hold_ms = 0

    def get_present_rows(self, display_height):
        """Return the menu rows affected by the current highlight frame."""
        previous_y = self._presented_y
        if previous_y is None:
            return None
        current_y = int(self.cursor.y)
        current_width = int(self.cursor.width)
        if self.view_offset != self._presented_view_offset:
            return ((self.y, self.height),)
        if (current_y == previous_y
                and current_width == self._presented_width):
            return None
        font_height = self.font.height if self.font else 8
        row_start = max(0, min(previous_y, current_y) - 2)
        row_end = min(display_height,
                      max(previous_y, current_y) + font_height + 3)
        return ((row_start, max(1, row_end - row_start)),)

    def mark_presented(self):
        self._presented_y = int(self.cursor.y)
        self._presented_width = int(self.cursor.width)
        self._presented_view_offset = self.view_offset

    def draw_present_rows(self, display):
        """Clear stale highlight pixels, then rebuild the menu row buffer."""
        display.fill_rectangle(
            self.x + 1, self.y + 1, self.width - 2, self.height - 2, 0)
        self.draw(display)

    def move_cursor_up(self):
        if self.cursor_pos > 0:
            self.cursor_pos -= 1
            self._clamp_view()
            self._update_cursor_target()

    def move_cursor_down(self):
        if self.cursor_pos < len(self.items) - 1:
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
        """Sync animated cursor to current cursor_pos and menu.y."""
        self.cursor.change_target(
            self.x + 2,
            self.y + 2 + (self.cursor_pos - self.view_offset) * self.row_height,
            width=self._highlight_width(self.cursor_pos)
        )

    def _highlight_width(self, item_pos):
        """Measure the compact highlight bar for one visible menu item."""
        if not 0 <= item_pos < len(self.items):
            return 0
        label = self.items[item_pos][0]
        text_width = (self.font.measure_text(label) if self.font
                      else len(label) * 8)
        return min(self.width - 4, text_width + 4)

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
        if direction == 0 or direction != self._repeat_direction:
            self._repeat_direction = 0
            self._repeat_hold_ms = 0
            return False
        key = UP_KEY if direction < 0 else DOWN_KEY
        get_hold = getattr(kb, "get_hold_time", None)
        hold_ms = get_hold(*key) if get_hold is not None else 0
        if (hold_ms < MENU_REPEAT_DELAY_MS
                or hold_ms - self._repeat_hold_ms < MENU_REPEAT_INTERVAL_MS):
            return False
        self._repeat_hold_ms = hold_ms
        previous = self.cursor_pos
        if direction < 0:
            self.move_cursor_up()
        else:
            self.move_cursor_down()
        return self.cursor_pos != previous

    def draw(self, display):
        display.draw_rectangle(self.x, self.y, self.width, self.height, self.gs)
        font_h = self.font.height if self.font else 8
        bar_y = self.cursor.y

        # Position and width move together, preserving a coherent marker as
        # the logical selection changes on a physical key edge.
        if self.cursor.width > 0:
            display.fill_rectangle(self.x + 2, bar_y, self.cursor.width,
                                   font_h, 14)

        # Draw items — only invert text when highlight bar fully covers it
        for i in range(self.view_offset,
                       min(self.view_offset + self.visible_rows, len(self.items))):
            label = self.items[i][0]
            draw_y = self.y + 2 + (i - self.view_offset) * self.row_height

            if abs(bar_y - draw_y) <= 2:
                if self.font:
                    display.draw_text_direct(
                        self.x + 4, draw_y, self._encoded_labels[i],
                        self.font, gs=0)
                else:
                    display.draw_text8x8(self.x + 4, draw_y, label, gs=0)
            else:
                if self.font:
                    display.draw_text_direct(
                        self.x + 4, draw_y, self._encoded_labels[i],
                        self.font, gs=self.gs)
                else:
                    display.draw_text8x8(self.x + 4, draw_y, label, gs=self.gs)

    def update(self, kb, event=None):
        if event is None:
            return "MOVE" if self._repeat_held_direction(kb) else None

        r, c, shift = event
        label = get_key_label(r, c, shift)

        # Navigation: 8=UP, 2=DOWN (no shift needed in menus)
        if label == "8" or label == "up":
            self.move_cursor_up()
            self._repeat_direction = -1
            self._repeat_hold_ms = 0
        elif label == "2" or label == "down":
            self.move_cursor_down()
            self._repeat_direction = 1
            self._repeat_hold_ms = 0
        elif label == "ENT":
            self._repeat_direction = 0
            return "ENTER"
        elif label == "ESC":
            self._repeat_direction = 0
            return "BACK"
        else:
            self._repeat_direction = 0

        return None
