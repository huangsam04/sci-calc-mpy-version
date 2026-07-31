import sys
import types

import pytest

from screens.about import AboutScreen


def test_about_draw_uses_the_fixed_8x8_path_without_a_font_branch():
    class Display:
        def __init__(self):
            self.calls = []

        def draw_text8x8(self, x, y, text, gs):
            self.calls.append((x, y, text, gs))

    display = Display()
    screen = AboutScreen(object(), "1.2.3")

    screen.draw(display)

    assert display.calls == [
        (5, 2, "SCI-CALC", 15),
        (5, 10, "MP Edition v", 15),
        (101, 10, "1.2.3", 15),
        (101, 18, "by huangsam04", 15),
        (5, 26, "ESP32 WROOM-32E", 15),
        (5, 34, "SSD1322 256x64 OLED", 15),
        (5, 42, "Kailh Choc v1", 15),
        (5, 50, "Designed by SHAO", 15),
    ]


def test_about_draw_keeps_every_text_run_inside_the_content_area():
    class Display:
        def __init__(self):
            self.calls = []

        def draw_text8x8(self, x, y, text, gs):
            self.calls.append((x, y, text, gs))

    display = Display()
    screen = AboutScreen(object(), "1.6.0")

    screen.draw(display)

    assert all(
        x >= 0 and x + len(text) * 8 <= screen.width
        for x, _y, text, _gs in display.calls
    )


def test_target_lazily_imports_about_scenario_transaction(monkeypatch):
    class LazyTransaction:
        def __init__(self, screen):
            self.screen = screen

    lazy_module = types.ModuleType("screens.about_scenario")
    lazy_module.AboutScenarioTransaction = LazyTransaction
    monkeypatch.setitem(sys.modules, "screens.about_scenario", lazy_module)
    screen = AboutScreen(None, "1.2.3")

    transaction = screen.open_scenario_transaction()

    assert type(transaction) is LazyTransaction
    assert transaction.screen is screen


@pytest.mark.parametrize(
    "event",
    ((3, 3, False), (4, 3, False), (0, 0, False)),
    ids=("ent", "del", "esc"),
)
def test_about_active_lease_ignores_all_input_events(event):
    screen = AboutScreen(None, "1.2.3")
    transaction = screen.open_scenario_transaction()
    assert transaction.step() is True
    version = screen.version

    assert screen.update(None, event) is None

    assert screen.version == version
    assert screen._scenario_transaction is transaction
    assert transaction.close() is True
    assert screen.update(None, (0, 0, False)) == "BACK"
