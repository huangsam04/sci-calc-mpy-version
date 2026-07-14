"""About screen: version and hardware info."""
from ui.element import UIElement
from input.keyboard import get_key_label


class AboutScreen(UIElement):
    def __init__(self, font, version="1.0.0"):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.version = version

    def activate(self):
        pass

    def draw(self, display):
        # ponytail: 8px line spacing fits 7 lines on 64px display
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
        for i, line in enumerate(lines):
            y = 2 + i * 8
            if line:
                if self.font:
                    display.draw_text(5, y, line, self.font, gs=15)
                else:
                    display.draw_text8x8(5, y, line, gs=15)

    def update(self, kb):
        event = kb.pop_key_event()
        if event is None:
            return None
        r, c, shift = event
        label = get_key_label(r, c, shift)
        if label == "ESC":
            return "BACK"
        return None
