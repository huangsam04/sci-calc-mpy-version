"""Main menu screen - top-level navigation."""
from ui.element import UIElement
from ui.menu import Menu


class MainMenu(UIElement):
    def __init__(self, font):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.menu = Menu(0, 13, 210, 4, 12, font)
        self._items = []  # keep track of (label, screen) pairs

    def add_screen(self, label, screen):
        self.menu.add_item(label, screen)
        self._items.append((label, screen))

    def activate(self):
        self.menu.cursor_pos = 0
        self.menu.view_offset = 0
        self.menu.activate()

    def draw(self, display):
        # Title
        if self.font:
            display.draw_text(2, 1, "SCI-CALC", self.font, gs=15)
        else:
            display.draw_text8x8(2, 1, "SCI-CALC", gs=15)

        display.draw_hline(0, 11, 210, 15)

        self.menu.draw(display)

    def update(self, kb):
        action = self.menu.update(kb)
        if action == "ENTER":
            target = self.menu.get_selected()
            if target:
                return target
        # ponytail: ESC no-op on main menu per spec
        return None
