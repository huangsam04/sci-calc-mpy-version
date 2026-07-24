"""Cursor widget for input boxes and menu selection."""
from ui.element import UIElement
from anim.engine import cancel_animation, insert_animation
from ui.motion import CONTROL_MOTION_MS


class Cursor(UIElement):
    __slots__ = ("mode", "is_visible", "gs")

    def __init__(self, x=0, y=0, mode=1):
        super().__init__(x, y, 5, 12)
        self.mode = mode          # 0=box, 1=line
        self.is_visible = True
        self.gs = 15

    def set_visible(self, v):
        self.is_visible = v

    def change_target(self, new_x, new_y, duration=CONTROL_MOTION_MS, delay=0,
                      width=None, height=None):
        """Animate cursor position and optional dimensions to a new target."""
        self.target_x = new_x
        self.target_y = new_y
        self._animate_changed("x", new_x, duration, delay)
        self._animate_changed("y", new_y, duration, delay)
        if width is not None:
            self.target_w = width
            self._animate_changed("width", width, duration, delay)
        if height is not None:
            self.target_h = height
            self._animate_changed("height", height, duration, delay)

    def _animate_changed(self, attr, target, duration, delay):
        current = getattr(self, attr)
        if current == target:
            cancel_animation(self, attr)
            return
        insert_animation(
            self, attr, current, target, duration,
            delay, ensure_progress=True)

    def draw(self, display):
        if not self.is_visible:
            return
        if self.mode == 1:
            # Line cursor: vertical line
            display.draw_vline(self.x, self.y, self.height, self.gs)
        else:
            # Box cursor: filled rectangle outline
            display.draw_rectangle(self.x, self.y, self.width, self.height, self.gs)
