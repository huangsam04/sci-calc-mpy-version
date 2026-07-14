# ponytail: scrollable list with box cursor, matching original firmware design
"""Menu widget: scrollable list with animation transitions."""
from ui.element import UIElement
from ui.cursor import Cursor
from anim.engine import insert_animation, insert_tmp_target
from input.keyboard import get_key_label


class Menu(UIElement):
    def __init__(self, x=0, y=0, width=210, visible_rows=5, row_height=12, font=None):
        super().__init__(x, y, width, visible_rows * row_height)
        self.items = []           # list of (label, target_element)
        self.visible_rows = visible_rows
        self.row_height = row_height
        self.font = font
        self.cursor_pos = 0
        self.view_offset = 0
        self.cursor = Cursor(x + 2, y + 2, mode=0)
        self.cursor.width = width - 4
        self.cursor.height = row_height - 1
        self._rest_x = x
        self._rest_y = y
        self._active_x = x
        self._active_y = y
        self.gs = 15

    def add_item(self, label, target):
        # ponytail: pre-truncate once, not every draw frame
        if self.font:
            max_w = self.width - 10
            if self.font.measure_text(label) > max_w:
                while len(label) > 0 and self.font.measure_text(label + "~") > max_w:
                    label = label[:-1]
                label += "~"
        self.items.append((label, target))

    def clear_items(self):
        self.items = []

    def get_selected(self):
        if 0 <= self.cursor_pos < len(self.items):
            return self.items[self.cursor_pos][1]
        return None

    def activate(self):
        # Snap cursor to correct position instantly — no animation on activation
        target_y = self.y + 2 + (self.cursor_pos - self.view_offset) * self.row_height
        self.cursor.x = self.x + 2
        self.cursor.y = target_y
        self.cursor.target_x = self.x + 2
        self.cursor.target_y = target_y

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
            200
        )

    def draw(self, display):
        display.draw_rectangle(self.x, self.y, self.width, self.height, self.gs)
        font_h = self.font.height if self.font else 8
        bar_y = self.cursor.y

        # Draw highlight bar at animated position
        if 0 <= self.cursor_pos < len(self.items):
            display.fill_rectangle(self.x + 2, bar_y, self.width - 4, font_h, 14)

        # Draw items — only invert text when highlight bar fully covers it
        # Tight threshold prevents text inversion before the animated bar arrives
        for i in range(self.view_offset,
                       min(self.view_offset + self.visible_rows, len(self.items))):
            label = self.items[i][0]
            draw_y = self.y + 2 + (i - self.view_offset) * self.row_height
            dist = abs(bar_y - draw_y)

            if dist <= 2:
                # Bar is on this row: invert text to contrast with highlight
                if self.font:
                    display.draw_text(self.x + 4, draw_y, label, self.font, invert=True, gs=14)
                else:
                    display.draw_text8x8(self.x + 4, draw_y, label, gs=0)
            else:
                if self.font:
                    display.draw_text(self.x + 4, draw_y, label, self.font, gs=self.gs)
                else:
                    display.draw_text8x8(self.x + 4, draw_y, label, gs=self.gs)

    def update(self, kb):
        event = kb.pop_key_event()
        if event is None:
            return None

        r, c, shift = event
        label = get_key_label(r, c, shift)

        # Navigation: 8=UP, 2=DOWN (no shift needed in menus)
        if label == "8" or label == "up":
            self.move_cursor_up()
        elif label == "2" or label == "down":
            self.move_cursor_down()
        elif label == "ENT":
            return "ENTER"
        elif label == "ESC":
            return "BACK"

        return None
