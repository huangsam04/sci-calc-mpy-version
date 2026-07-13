# ponytail: simple text widget, draws string at position
"""Text widget for displaying strings on screen."""
from ui.element import UIElement


class Text(UIElement):
    def __init__(self, x=0, y=0, text="", font=None):
        super().__init__(x, y, 0, 10)
        self.text = text
        self.font = font  # XglcdFont instance or None for built-in 8x8
        self.gs = 15       # grayscale (15 = white)
        self._update_size()

    def set_text(self, text):
        self.text = text
        self._update_size()

    def _update_size(self):
        if self.font and self.text:
            self.width = self.font.measure_text(self.text)
        elif self.text:
            self.width = len(self.text) * 8
        else:
            self.width = 0

    def draw(self, display):
        if not self.visible or not self.text:
            return
        if self.font:
            display.draw_text(self.x, self.y, self.text, self.font, gs=self.gs)
        else:
            display.draw_text8x8(self.x, self.y, self.text, gs=self.gs)

    def update(self, kb):
        pass
