"""Main menu screen - top-level navigation."""
from ui.element import UIElement
from ui.menu import Menu
from ui.theme import draw_header


class MainMenu(UIElement):
    transition_title = "SCI-CALC"

    def __init__(self, font):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.menu = Menu(0, 13, 210, 4, 12, font)
        self._items = []  # keep track of (label, screen) pairs

    def add_screen(self, label, screen):
        self.menu.add_item(label, screen)
        self._items.append((label, screen))

    def get_present_rows(self):
        return self.menu.get_present_rows(self.height)

    def mark_presented(self):
        self.menu.mark_presented()

    def draw_present_rows(self, display):
        self.menu.draw_present_rows(display)

    def activate(self):
        # Selection is persistent navigation state. Returning from a page must
        # restore the item the user entered instead of jumping to the top.
        self.menu.activate()

    def draw(self, display):
        draw_header(display, "SCI-CALC", self.font)
        self.menu.draw(display)

    def update(self, kb, event=None):
        action = self.menu.update(kb, event)
        if action == "MOVE":
            return "REDRAW"
        if action == "ENTER":
            target = self.menu.get_selected()
            if target:
                return target
        # ESC is intentionally a no-op at the navigation root.
        return None
