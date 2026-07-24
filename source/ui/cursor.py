"""Cursor widget for input boxes and menu selection."""
from ui.element import UIElement


class Cursor(UIElement):
    __slots__ = ("mode", "is_visible", "gs")

    def __init__(self, x=0, y=0, mode=1):
        super().__init__(x, y, 5, 12)
        self.mode = mode          # 0=box, 1=line
        self.is_visible = True
        self.gs = 15

    def set_visible(self, v):
        self.is_visible = v

    def change_target(self, new_x, new_y, width=None, height=None):
        """Apply cursor geometry immediately on the input path."""
        self.x = new_x
        self.y = new_y
        if width is not None:
            self.width = width
        if height is not None:
            self.height = height

    def draw(self, display):
        if not self.is_visible:
            return
        if self.mode == 1:
            # Line cursor: vertical line
            display.draw_vline(self.x, self.y, self.height, self.gs)
        else:
            # Box cursor: filled rectangle outline
            display.draw_rectangle(self.x, self.y, self.width, self.height, self.gs)
