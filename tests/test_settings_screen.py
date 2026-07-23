from display.ssd1322 import Display
from screens.about import AboutScreen
from screens.settings import SettingsScreen
from version import VERSION


class DisplayStub:
    def __init__(self):
        self.brightness_values = []

    def set_brightness(self, value):
        self.brightness_values.append(value)


class KeyboardStub:
    pass


def test_display_brightness_uses_ssd1322_master_current_command():
    display = object.__new__(Display)
    commands = []
    display.write_cmd = lambda *values: commands.append(values)

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

    assert [label for label, _ in screen.menu.items] == [
        "Version  " + VERSION,
        "About",
        "Brightness  80%",
        "Display digits  4",
    ]


def test_about_opens_from_second_row_and_brightness_queues_persistence():
    display = DisplayStub()
    queued = []
    settings = {"version": "1.2.3", "brightness": 80}
    about = AboutScreen(None, "1.2.3")
    screen = SettingsScreen(
        None, display, settings, about,
        request_save=lambda value, callback: queued.append((dict(value), callback)))

    screen.menu.cursor_pos = 1
    assert screen.update(KeyboardStub(), (3, 3, False)) is about

    screen.menu.cursor_pos = 2
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
        request_save=lambda value, callback: queued.append((dict(value), callback)),
        on_display_digits_change=applied.append)

    screen.menu.cursor_pos = 3
    screen.update(KeyboardStub(), (2, 2, False))

    assert settings["display_digits"] == 5
    assert applied == [5]
    assert queued[0][0]["display_digits"] == 5
