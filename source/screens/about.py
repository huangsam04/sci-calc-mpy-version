from ui.element import UIElement


class AboutScreen(UIElement):
    transition_title = "About"

    __slots__ = ("version", "_scenario_transaction")

    def __init__(self, font, version):
        UIElement.__init__(self, 0, 0, 210, 64)
        self.version = version
        self._scenario_transaction = None

    def open_scenario_transaction(self):
        from screens.about_scenario import AboutScenarioTransaction
        return AboutScenarioTransaction(self)

    def draw(self, display):
        display.draw_text8x8(5, 2, "SCI-CALC", gs=15)
        display.draw_text8x8(5, 10, "MP Edition v", gs=15)
        display.draw_text8x8(101, 10, self.version, gs=15)
        display.draw_text8x8(101, 18, "by huangsam04", gs=15)
        display.draw_text8x8(5, 26, "ESP32 WROOM-32E", gs=15)
        display.draw_text8x8(5, 34, "SSD1322 256x64 OLED", gs=15)
        display.draw_text8x8(5, 42, "Kailh Choc v1", gs=15)
        display.draw_text8x8(5, 50, "Designed by SHAO", gs=15)

    def update(self, kb, event=None):
        if self._scenario_transaction is not None or event is None:
            return None
        if event[0] == 0 and event[1] == 0:
            return "BACK"
        return None
