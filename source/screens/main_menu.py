"""Main menu screen - top-level navigation."""
from ui.element import UIElement
from ui.menu import Menu
from ui.theme import SHELL_MAIN_MENU, draw_header, draw_page_shell


class MainMenu(UIElement):
    swap_key = "main_menu"
    transition_title = "SCI-CALC"

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
        # Selection is persistent navigation state. Returning from a page must
        # restore the item the user entered instead of jumping to the top.
        self.menu.activate()

    def snapshot_state(self):
        return {"cursor": self.menu.cursor_pos,
                "view": self.menu.view_offset}

    def reset_state(self):
        self.menu.cursor_pos = 0
        self.menu.view_offset = 0

    def activate_default(self):
        self.menu.cursor_pos = 0
        self.menu.view_offset = 0
        self.menu.activate()

    def restore_state(self, state):
        self.menu.cursor_pos = max(0, min(
            int(state.get("cursor", 0)), len(self._items) - 1))
        self.menu.view_offset = max(0, int(state.get("view", 0)))
        self.menu._clamp_view()
        self.menu.activate()

    def draw_transition_default(self, display):
        draw_page_shell(display, SHELL_MAIN_MENU, self.font)

    def draw(self, display):
        draw_header(display, "SCI-CALC", self.font)
        self.menu.draw(display)

    def update(self, kb, event=None):
        action = self.menu.update(kb, event)
        if action == "ENTER":
            target = self.menu.get_selected()
            if target:
                return target
        # ESC is intentionally a no-op at the navigation root.
        return None
