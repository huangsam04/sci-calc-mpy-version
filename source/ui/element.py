"""Small common interface for drawable, input-aware UI objects."""

SETTLE_MORE = 1
SETTLE_REDRAW = 2
SETTLE_COLLECT = 4


class UIElement:
    __slots__ = ("x", "y", "width", "height")

    transition_title = "Loading"

    def __init__(self, x=0, y=0, width=0, height=0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def activate(self):
        pass

    def deactivate(self):
        pass

    def draw(self, display):
        pass

    def update(self, kb, event=None):
        pass

    def release_memory(self):
        return False

    def settle_step(self):
        return 0
