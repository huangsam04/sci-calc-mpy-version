"""Main menu screen - top-level navigation."""
from ui.element import UIElement
from ui.menu import Menu
from ui.theme import draw_header


class MainMenu(UIElement):
    def __init__(self, font):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.menu = Menu(0, 13, 210, 4, 12, font)
        self._items = []  # keep track of (label, screen) pairs

    def add_screen(self, label, screen):
        self.menu.add_item(label, screen)
        self._items.append((label, screen))

    def animation_children(self):
        return (self.menu,)

    def activate(self):
        self.menu.cursor_pos = 0
        self.menu.view_offset = 0
        self.menu.activate()

    def draw(self, display):
        draw_header(display, "SCI-CALC", self.font)
        self.menu.draw(display)

    def update(self, kb, event=None):
        action = self.menu.update(kb, event)
        if action == "ENTER":
            target = self.menu.get_selected()
            if target:
                return target
        # ponytail: ESC no-op on main menu per spec
        return None
