"""UIElement base class for all UI components."""


class UIElement:
    swap_key = None
    transition_title = "Loading"

    def __init__(self, x=0, y=0, width=0, height=0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.target_x = x
        self.target_y = y
        self.target_w = width
        self.target_h = height
        self._residency_error = ""

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

    def release_memory(self):
        """Drop rebuildable cache state when this page becomes inactive."""
        return False

    def snapshot_state(self):
        """Return bounded JSON-shaped state; derived caches never belong here."""
        return {}

    def restore_state(self, state):
        """Restore state after the page transition has completed."""
        pass

    def reset_state(self):
        """Return page-local session state to its default values."""
        pass

    def activate_default(self):
        """Activate without loading state or rebuilding heavy derived data."""
        self.activate()

    def settle_step(self):
        """Perform at most one bounded post-transition rebuild step."""
        return 0

    def draw_transition_default(self, display):
        """Allocation-bounded fallback target used during page animation."""
        display.draw_rectangle(1, 1, max(1, self.width - 2),
                               max(1, self.height - 2), 8)
        display.draw_text8x8(6, 5, str(self.transition_title)[:20], gs=15)
        display.draw_hline(5, 16, max(1, self.width - 10), 6)
        display.draw_text8x8(6, max(18, self.height - 11),
                             "Loading...", gs=8)

    def show_residency_error(self, message):
        self._residency_error = str(message or "Page state unavailable")

    def clear_residency_error(self):
        self._residency_error = ""

    def draw_residency_error(self, display):
        if not self._residency_error:
            return
        label = ("PAGE ERROR"
                 if self._residency_error.startswith("Page ")
                 else "SWAP ERROR")
        display.fill_rectangle(4, 13, max(1, self.width - 8), 38, 0)
        display.draw_rectangle(4, 13, max(1, self.width - 8), 38, 15)
        display.draw_text8x8(10, 18, label, gs=15)
        display.draw_text8x8(10, 29, self._residency_error[:23], gs=10)
        display.draw_text8x8(10, 40, "Page reset - any key", gs=8)

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
