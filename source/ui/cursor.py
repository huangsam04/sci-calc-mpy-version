"""Cursor widget for input boxes and menu selection."""


class Cursor:
    __slots__ = ("x", "y", "_size", "_state")

    def __init__(self, x=0, y=0, mode=1):
        self.x = x
        self.y = y
        self._size = (5 << 8) | 12
        # mode bit 0, visibility bit 1, grayscale bits 2..5. Keeping these
        # bounded scalars in one slot makes the cursor fit fragmented heaps.
        self._state = ((15 << 2) | 2 | (1 if mode else 0))

    @property
    def width(self):
        return self._size >> 8

    @width.setter
    def width(self, value):
        self._size = (value << 8) | (self._size & 255)

    @property
    def height(self):
        return self._size & 255

    @height.setter
    def height(self, value):
        self._size = (self._size & ~255) | value

    @property
    def mode(self):
        return self._state & 1

    @mode.setter
    def mode(self, value):
        self._state = (self._state & ~1) | (1 if value else 0)

    @property
    def is_visible(self):
        return bool(self._state & 2)

    @is_visible.setter
    def is_visible(self, value):
        if value:
            self._state |= 2
        else:
            self._state &= ~2

    @property
    def gs(self):
        return self._state >> 2

    @gs.setter
    def gs(self, value):
        self._state = (self._state & 3) | (value << 2)

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
