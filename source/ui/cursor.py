"""Cursor widget for input boxes and menu selection."""
from ui.element import UIElement
from anim.engine import insert_animation


class Cursor(UIElement):
    def __init__(self, x=0, y=0, mode=1):
        super().__init__(x, y, 5, 12)
        self.mode = mode          # 0=box, 1=line
        self.is_visible = True
        self.gs = 15

    def set_visible(self, v):
        self.is_visible = v

    def change_target(self, new_x, new_y, duration=140, delay=0):
        """Animate cursor to a new position."""
        self.target_x = new_x
        self.target_y = new_y
        insert_animation(self, 'x', self.x, new_x, duration, "OUT_QUAD", delay)
        insert_animation(self, 'y', self.y, new_y, duration, "OUT_QUAD", delay)

    def draw(self, display):
        if not self.is_visible:
            return
        if self.mode == 1:
            # Line cursor: vertical line
            display.draw_vline(self.x, self.y, self.height, self.gs)
        else:
            # Box cursor: filled rectangle outline
            display.draw_rectangle(self.x, self.y, self.width, self.height, self.gs)
