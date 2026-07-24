from calc.functions import build_registry
from screens.calculator import CalculatorScreen
from ui.error_popup import ErrorPopup, PANEL_Y
from ui.inputbox import InputBox, UPPER_CONTINUATION_CUE
from ui.menu import Menu


class MenuDisplaySpy:
    def __init__(self):
        self.fills = []
        self.text = []
        self.direct = []

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        self.fills.append(args)

    def draw_text8x8(self, x, y, text, gs=15):
        self.text.append((x, y, text, gs))

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct.append((x, y, text, font, gs))


class InputDisplaySpy:
    def __init__(self):
        self.text = []
        self.direct = []
        self.fallback = []

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        pass

    def draw_vline(self, *args):
        pass

    def draw_text(self, x, y, text, font, invert=False, gs=15, raw=False):
        self.text.append((x, y, text, gs))
        self.fallback.append(text)

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct.append((x, y, text, font, gs))


class FontStub:
    width = 5
    height = 7

    def measure_text(self, text, spacing=1):
        return len(text) * (self.width + spacing)


class HeldDownKeyboard:
    def __init__(self):
        self.hold_ms = 0

    def is_pressed(self, row, col):
        return (row, col) == (3, 1)

    def get_hold_time(self, row, col):
        return self.hold_ms if (row, col) == (3, 1) else 0


def test_menu_marker_snaps_to_the_selected_row():
    menu = Menu(0, 0, 80, visible_rows=2, row_height=12)
    menu.add_item("A", object())
    menu.add_item("Long", object())
    menu.activate()

    menu.move_cursor_down()
    display = MenuDisplaySpy()
    menu.draw(display)

    assert menu.cursor.y == 14
    assert menu.cursor.width == 36
    assert display.fills == [(2, 14, 36, 8, 14)]
    assert [row[3] for row in display.text] == [15, 0]


def test_menu_row_update_covers_old_and_new_highlights():
    menu = Menu(0, 13, 80, visible_rows=2, row_height=12)
    menu.add_item("A", object())
    menu.add_item("B", object())
    menu.activate()
    menu.mark_presented()

    menu.move_cursor_down()

    assert menu.get_present_rows(64) == ((13, 25),)


def test_menu_repeats_a_held_direction_key():
    menu = Menu(0, 0, 80, visible_rows=4, row_height=12)
    for label in ("A", "B", "C", "D"):
        menu.add_item(label, object())
    menu.activate()
    keyboard = HeldDownKeyboard()

    menu.update(keyboard, (3, 1, False))
    keyboard.hold_ms = 400
    menu.update(keyboard, None)
    keyboard.hold_ms = 500
    menu.update(keyboard, None)

    assert menu.cursor_pos == 3


def test_menu_draws_cached_label_bytes():
    menu = Menu(0, 0, 80, visible_rows=2, row_height=12,
                font=FontStub())
    menu.add_item("A", object())
    menu.add_item("Long", object())
    menu.activate()
    display = MenuDisplaySpy()

    menu.draw(display)

    assert display.direct == [
        (4, 2, b"A", menu.font, 0),
        (4, 14, b"Long", menu.font, 15),
    ]


def test_input_editor_uses_packed_bytes_and_ascii_continuation():
    box = InputBox(0, 0, 34, 12, 96, FontStub())
    box.set_str("12", immediate=True)
    display = InputDisplaySpy()
    box.draw(display)
    assert display.direct == [(1, 1, b"12", box.font, 15)]
    assert display.fallback == []

    box = InputBox(0, 0, 34, 18, 96, FontStub(), visible_rows=2)
    box.set_str("123456789")
    box.move_cursor_end()
    display = InputDisplaySpy()
    box.draw(display)

    assert UPPER_CONTINUATION_CUE == "^"
    assert b"^" in [entry[2] for entry in display.direct]


def test_error_popup_appears_immediately_without_animation():
    popup = ErrorPopup()

    popup.show("1/0", "Division by zero")

    assert popup._shade == 15
    assert popup._panel_y == PANEL_Y


def test_successful_calculation_has_no_animation_state():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("1+1")

    screen._enter()

    assert screen.history == [("1+1", 2.0)]
    assert not hasattr(screen, "_result_pulse")
