"""Single-frame renderer with optional row-only OLED updates."""
import time

from ui.theme import CONTENT_W, SCREEN_H


class Renderer:
    __slots__ = (
        "display", "sidebar", "last_present_us", "_visible_screen",
        "_sidebar_dirty")

    def __init__(self, display, sidebar, memory=None):
        self.display = display
        self.sidebar = sidebar
        self.last_present_us = 0
        self._visible_screen = None
        self._sidebar_dirty = True

    def _present(self, rows):
        if self._sidebar_dirty:
            self.sidebar.draw(self.display)
            self._sidebar_dirty = False
        started = time.ticks_us()
        if rows is None:
            self.display.present()
        else:
            self.display.present_rows(rows)
        self.last_present_us = time.ticks_diff(time.ticks_us(), started)

    def _present_rows(self, screen):
        if self._visible_screen is not screen:
            return None
        getter = getattr(screen, "get_present_rows", None)
        return getter() if getter is not None else None

    def present(self, screen):
        rows = self._present_rows(screen)
        if self._sidebar_dirty:
            rows = None
        partial = (getattr(screen, "draw_present_rows", None)
                   if rows is not None else None)
        if partial is None:
            rows = None
            if self._sidebar_dirty:
                self.display.clear_buffers(0)
            else:
                self.display.fill_rectangle(
                    0, 0, CONTENT_W, SCREEN_H, 0)
            screen.draw(self.display)
        else:
            partial(self.display)
        self._present(rows)
        self._visible_screen = screen
        marker = getattr(screen, "mark_presented", None)
        if marker is not None:
            marker()

    def invalidate(self):
        self._visible_screen = None
        self._sidebar_dirty = True

    def invalidate_sidebar(self):
        self._sidebar_dirty = True
