import sys
import types

from display.ssd1322 import Display
from screens.about import AboutScreen
from screens import settings as settings_module
from screens.settings import (BRIGHTNESS_MAX, BRIGHTNESS_MIN, SettingsScreen)
from calc.number import MAX_DISPLAY_DIGITS, MIN_DISPLAY_DIGITS
from ui.menu import Menu
from version import VERSION


class DisplayStub:
    def __init__(self):
        self.brightness_values = []

    def set_brightness(self, value):
        self.brightness_values.append(value)


class KeyboardStub:
    pass


def test_target_lazily_imports_settings_scenario_transaction(monkeypatch):
    class LazyTransaction:
        def __init__(self, screen):
            self.screen = screen

    lazy_module = types.ModuleType("screens.settings_scenario")
    lazy_module.SettingsScenarioTransaction = LazyTransaction
    monkeypatch.setitem(sys.modules, "screens.settings_scenario", lazy_module)
    monkeypatch.setattr(
        settings_module, "SettingsScenarioTransaction", None)
    screen = SettingsScreen(
        None, DisplayStub(), {"brightness": 80},
        AboutScreen(None, "1.2.3"))

    transaction = screen.open_scenario_transaction()

    assert type(transaction) is LazyTransaction
    assert transaction.screen is screen


def test_display_brightness_uses_ssd1322_master_current_command():
    display = object.__new__(Display)
    commands = []
    display._write_cmd1 = lambda command, value: commands.append(
        (command, value))

    display.set_brightness(50)
    display.set_brightness(100)

    assert commands == [
        (Display.MASTER_CURRENT_CONTROL, 8),
        (Display.MASTER_CURRENT_CONTROL, 15),
    ]


def test_settings_rows_include_display_precision_control():
    about = AboutScreen(None, "1.2.3")
    screen = SettingsScreen(
        None, DisplayStub(), {"version": "1.2.3", "brightness": 80}, about)

    assert [label for label, _ in screen._state[5]._state[5]] == [
        "Version  " + VERSION,
        "About",
        "Brightness  80%",
        "Display digits  4",
    ]


def test_settings_reuses_a_preallocated_menu():
    menu = Menu(0, 13, 210, 4, 10)

    screen = SettingsScreen(
        None, DisplayStub(), {"brightness": 80},
        AboutScreen(None, "1.2.3"), build_rows=False, menu=menu)

    assert screen._state[5] is menu


def test_about_opens_from_second_row_and_brightness_queues_persistence():
    display = DisplayStub()
    queued = []
    settings = {"version": "1.2.3", "brightness": 80}
    about = AboutScreen(None, "1.2.3")
    screen = SettingsScreen(
        None, display, settings, about,
        request_save=lambda value, callback, owner:
            queued.append((dict(value), callback)))

    screen._state[5].cursor_pos = 1
    assert screen.update(KeyboardStub(), (3, 3, False)) is about

    screen._state[5].cursor_pos = 2
    screen.update(KeyboardStub(), (2, 2, False))

    assert settings["brightness"] == 90
    assert display.brightness_values == [90]
    assert queued[0][0]["brightness"] == 90


def test_display_digits_updates_the_live_formatter_and_is_saved():
    display = DisplayStub()
    settings = {"display_digits": 4}
    applied = []
    queued = []
    screen = SettingsScreen(
        None, display, settings, AboutScreen(None, "1.2.3"),
        request_save=lambda value, callback, owner:
            queued.append((dict(value), callback)),
        on_display_digits_change=applied.append)

    screen._state[5].cursor_pos = 3
    screen.update(KeyboardStub(), (2, 2, False))

    assert settings["display_digits"] == 5
    assert applied == [5]
    assert queued[0][0]["display_digits"] == 5


def test_settings_boundaries_do_not_request_redraw_or_persistence():
    display = DisplayStub()
    queued = []
    screen = SettingsScreen(
        None, display,
        {"brightness": BRIGHTNESS_MIN, "display_digits": MIN_DISPLAY_DIGITS},
        AboutScreen(None, "1.2.3"),
        request_save=lambda value, callback, owner: queued.append(value))

    screen._state[5].cursor_pos = 2
    assert screen.update(KeyboardStub(), (2, 0, False)) is None
    screen._state[5].cursor_pos = 3
    assert screen.update(KeyboardStub(), (2, 0, False)) is None

    maximum = SettingsScreen(
        None, display,
        {"brightness": BRIGHTNESS_MAX, "display_digits": MAX_DISPLAY_DIGITS},
        AboutScreen(None, "1.2.3"),
        request_save=lambda value, callback, owner: queued.append(value))
    maximum._state[5].cursor_pos = 2
    assert maximum.update(KeyboardStub(), (2, 2, False)) is None
    maximum._state[5].cursor_pos = 3
    assert maximum.update(KeyboardStub(), (2, 2, False)) is None

    assert display.brightness_values == []
    assert queued == []


def test_settings_persistence_callback_requests_one_frame_per_visible_change():
    callbacks = []
    screen = SettingsScreen(
        None, DisplayStub(), {"brightness": 80}, AboutScreen(None, "1.2.3"),
        request_save=lambda _value, callback, _owner: callbacks.append(callback))
    screen._state[5].cursor_pos = 2

    assert screen.update(KeyboardStub(), (2, 2, False)) == "REDRAW"
    callback = callbacks[0]

    callback(False)
    assert screen.consume_persist_visual_change() is True
    assert screen.consume_persist_visual_change() is False
    callback(False)
    assert screen.consume_persist_visual_change() is False

    callback(True)
    assert screen.consume_persist_visual_change() is True
    assert screen.consume_persist_visual_change() is False
    callback(True)
    assert screen.consume_persist_visual_change() is False
