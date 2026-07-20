"""UIElement base class for all UI components."""


class UIElement:
    def __init__(self, x=0, y=0, width=0, height=0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.target_x = x
        self.target_y = y
        self.target_w = width
        self.target_h = height

    def init(self, display):
        """Called once after construction."""
        pass

    def activate(self):
        """Called when this element becomes the active screen."""
        pass

    def deactivate(self):
        """Called when this element is being navigated away from."""
        pass

    def draw(self, display):
        """Render to display. Override in subclasses."""
        pass

    def update(self, kb, event=None):
        """Handle input and logic. Override in subclasses."""
        pass

    def animation_children(self):
        """Return owned UI elements whose animations share this lifecycle."""
        return ()

    def move_to(self, x, y, w=None, h=None):
        """Instantly set position without animation."""
        self.x = x
        self.y = y
        self.target_x = x
        self.target_y = y
        if w is not None:
            self.width = w
            self.target_w = w
        if h is not None:
            self.height = h
            self.target_h = h
