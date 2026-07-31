"""Main menu screen - top-level navigation."""
from ui.element import UIElement
from ui.menu import Menu
from ui.theme import draw_header_fast


class MainMenu(UIElement):
    transition_title = "SCI-CALC"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("menu",)

    def __init__(self):
        # The root has exactly five destinations.  Reserve both the outer
        # list and mutable two-reference rows while the boot heap still has a
        # contiguous run; late binding then performs no list growth or tuple
        # allocation after every resident page already exists.
        rows = [
            ["", None], ["", None], ["", None], ["", None], ["", None]]
        self.menu = Menu(0, 13, 210, 4, 12, items=rows)

    def add_screen(self, label, screen):
        rows = self.menu._state[5]
        index = 0
        while index < len(rows):
            if not rows[index][0]:
                rows[index][0] = label
                rows[index][1] = screen
                self.menu.invalidate_presented()
                return
            index += 1
        raise RuntimeError("Main menu destination limit reached")

    def collect_present_damage(self, damage):
        return self.menu.collect_present_damage(self.height, damage)

    def mark_presented(self):
        self.menu.mark_presented()

    def draw_present_rows(self, display):
        self.menu.draw_present_rows(display)

    @property
    def motion_active(self):
        return self.menu.motion_active

    def advance_motion(self, now):
        return self.menu.advance_motion(now)

    def activate(self):
        # Selection is persistent navigation state. Returning from a page must
        # restore the item the user entered instead of jumping to the top.
        self.menu.activate()

    def draw(self, display):
        draw_header_fast(display, "SCI-CALC", b"", None)
        self.menu.draw(display)

    def update(self, kb, event=None):
        previous = self.menu.cursor_pos
        action = self.menu.update(kb, event)
        if action == "MOVE":
            if not self.menu._state[5][self.menu.cursor_pos][0]:
                self.menu.cursor_pos = previous
                self.menu._update_cursor_target()
                return None
            return "REDRAW"
        if action == "ENTER":
            target = self.menu.get_selected()
            if target:
                return target
        # ESC is intentionally a no-op at the navigation root.
        return None
