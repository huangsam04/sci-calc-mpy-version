"""About screen: version and hardware info."""
from ui.element import UIElement
from ui.residency import SETTLE_MORE, SETTLE_REDRAW
from ui.theme import SHELL_ABOUT, draw_page_shell
from input.keyboard import get_key_label
from version import VERSION


class AboutScreen(UIElement):
    transition_title = "About"

    def __init__(self, font, version=VERSION):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.version = version
        self._visible_lines = 7

    def activate(self):
        self._visible_lines = 7

    def activate_default(self):
        self._visible_lines = 1

    def settle_step(self):
        if self._visible_lines >= 7:
            return 0
        self._visible_lines += 1
        if self._visible_lines < 7:
            return SETTLE_REDRAW | SETTLE_MORE
        return SETTLE_REDRAW

    def draw(self, display):
        # Eight-pixel spacing fits seven lines on the 64px display.
        # (2 + 6*8 + 9 = 59px)
        lines = [
            "SCI-CALC",
            f"MP Edition v{self.version} by huangsam04",
            "",
            "ESP32 WROOM-32E",
            "SSD1322 256x64 OLED",
            "Kailh Choc v1",
            "Designed by SHAO",
        ]
        for i, line in enumerate(lines[:self._visible_lines]):
            y = 2 + i * 8
            if line:
                if self.font:
                    display.draw_text(5, y, line, self.font, gs=15)
                else:
                    display.draw_text8x8(5, y, line, gs=15)

    def update(self, kb, event=None):
        if event is None:
            return None
        r, c, shift = event
        label = get_key_label(r, c, shift)
        if label == "ESC":
            return "BACK"
        return None

    def draw_transition_default(self, display):
        draw_page_shell(display, SHELL_ABOUT, self.font)
