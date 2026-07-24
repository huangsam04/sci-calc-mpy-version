from anim import engine
from calc.functions import build_registry
from screens.calculator import CalculatorScreen
from ui.error_popup import (ErrorPopup, PANEL_START_Y, PANEL_Y)
from ui.inputbox import InputBox, UPPER_CONTINUATION_CUE
from ui.menu import Menu
from ui.motion import DIALOG_ENTER_MS


class MenuDisplaySpy:
    def __init__(self):
        self.fills = []
        self.text = []

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        self.fills.append(args)

    def draw_text8x8(self, x, y, text, gs=15):
        self.text.append((x, y, text, gs))


class DirectMenuDisplaySpy(MenuDisplaySpy):
    def __init__(self):
        super().__init__()
        self.direct = []

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct.append((x, y, text, font, gs))


class InputDisplaySpy:
    def __init__(self):
        self.text = []

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        pass

    def draw_vline(self, *args):
        pass

    def draw_text(self, x, y, text, font, invert=False, gs=15, raw=False):
        self.text.append((x, y, text, gs))


class DirectInputDisplaySpy:
    def __init__(self):
        self.direct = []
        self.fallback = []

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        pass

    def draw_vline(self, *args):
        pass

    def draw_text(self, *args, **kwargs):
        self.fallback.append((args, kwargs))

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


def test_menu_marker_keeps_its_current_label_width_while_moving():
    engine.cancel_all_animations()
    menu = Menu(0, 0, 80, visible_rows=2, row_height=12)
    menu.add_item("A", object())
    menu.add_item("Long", object())
    menu.activate()

    assert menu.cursor.width == 12
    menu.move_cursor_down()
    assert menu.cursor.target_w == 36

    first_frame = MenuDisplaySpy()
    menu.draw(first_frame)

    assert first_frame.fills == [(2, 2, 12, 8, 14)]
    assert [row[3] for row in first_frame.text] == [0, 15]

    menu.cursor.y = 14
    menu.cursor.width = 36
    final_frame = MenuDisplaySpy()
    menu.draw(final_frame)

    assert final_frame.fills == [(2, 14, 36, 8, 14)]
    assert [row[3] for row in final_frame.text] == [15, 0]
    engine.cancel_all_animations()


def test_menu_marker_does_not_pause_one_pixel_before_its_target(monkeypatch):
    """A short row move must keep advancing until it reaches the next row."""
    now = [100]
    monkeypatch.setattr(engine.time, "ticks_ms", lambda: now[0])
    monkeypatch.setattr(engine.time, "ticks_diff", lambda a, b: a - b)
    engine.cancel_all_animations()
    menu = Menu(0, 0, 80, visible_rows=2, row_height=12)
    menu.add_item("A", object())
    menu.add_item("B", object())
    menu.activate()
    menu.move_cursor_down()

    positions = []
    for tick in (100, 116, 132, 148, 164, 180, 196):
        now[0] = tick
        engine.animate_all()
        positions.append(menu.cursor.y)

    assert positions == sorted(set(positions))
    now[0] = 200
    engine.animate_all()
    assert menu.cursor.y == 14
    engine.cancel_all_animations()


def test_menu_exposes_only_the_rows_changed_by_its_highlight_motion():
    engine.cancel_all_animations()
    menu = Menu(0, 13, 80, visible_rows=2, row_height=12)
    menu.add_item("A", object())
    menu.add_item("B", object())
    menu.activate()

    assert menu.get_present_rows(64) is None
    menu.mark_presented()
    menu.move_cursor_down()

    assert menu.get_present_rows(64) == ((13, 13),)
    engine.cancel_all_animations()


def test_menu_repeats_a_held_direction_key_after_the_initial_move():
    engine.cancel_all_animations()
    menu = Menu(0, 0, 80, visible_rows=4, row_height=12)
    for label in ("A", "B", "C", "D"):
        menu.add_item(label, object())
    menu.activate()
    keyboard = HeldDownKeyboard()

    menu.update(keyboard, (3, 1, False))
    assert menu.cursor_pos == 1

    keyboard.hold_ms = 400
    menu.update(keyboard, None)
    assert menu.cursor_pos == 2

    keyboard.hold_ms = 500
    menu.update(keyboard, None)
    assert menu.cursor_pos == 3
    engine.cancel_all_animations()


def test_menu_move_animates_only_attributes_that_actually_change():
    engine.cancel_all_animations()
    menu = Menu(0, 0, 80, visible_rows=3, row_height=12)
    for label in ("A", "B", "C"):
        menu.add_item(label, object())
    menu.activate()

    menu.move_cursor_down()
    assert engine.active_animation_count() == 1

    # Retargeting before the previous motion ends replaces that one y
    # animation; it must not accumulate no-op x/width animation objects.
    menu.move_cursor_down()
    assert menu.cursor.target_y == 26
    assert engine.active_animation_count() == 1
    engine.cancel_all_animations()


def test_menu_draws_cached_label_bytes_without_string_framebuffers():
    menu = Menu(0, 0, 80, visible_rows=2, row_height=12,
                font=FontStub())
    menu.add_item("A", object())
    menu.add_item("Long", object())
    menu.activate()
    display = DirectMenuDisplaySpy()

    menu.draw(display)

    assert display.direct == [
        (4, 2, b"A", menu.font, 0),
        (4, 14, b"Long", menu.font, 15),
    ]


def test_multi_line_editor_uses_ascii_caret_for_upper_continuation():
    engine.cancel_all_animations()


def test_input_editor_uses_packed_bytes_for_live_font_text():
    box = InputBox(0, 0, 34, 12, 96, FontStub())
    box.set_str("12", immediate=True)
    display = DirectInputDisplaySpy()

    box.draw(display)

    assert display.direct == [(1, 1, b"12", box.font, 15)]
    assert display.fallback == []
    box = InputBox(0, 0, 34, 18, 96, FontStub(), visible_rows=2)
    box.set_str("123456789")
    box.move_cursor_end()
    display = InputDisplaySpy()

    box.draw(display)

    text = [entry[2] for entry in display.text]
    assert UPPER_CONTINUATION_CUE == "^"
    assert "^" in text
    assert chr(0x2191) not in text
    engine.cancel_all_animations()


def test_error_popup_enters_with_a_short_fade_and_rise(monkeypatch):
    now = [100]
    monkeypatch.setattr(engine.time, "ticks_ms", lambda: now[0])
    monkeypatch.setattr(engine.time, "ticks_diff", lambda a, b: a - b)
    engine.cancel_all_animations()
    popup = ErrorPopup()

    popup.show("1/0", "Division by zero")

    assert popup._shade == 0
    assert popup._panel_y == PANEL_START_Y
    assert engine.is_animating(popup) is True

    # The engine starts its duration on the first rendered animation frame.
    engine.animate_all()
    now[0] += DIALOG_ENTER_MS // 2
    engine.animate_all()
    assert 0 < popup._shade < 15
    assert PANEL_Y < popup._panel_y < PANEL_START_Y

    now[0] += DIALOG_ENTER_MS
    engine.animate_all()
    assert popup._shade == 15
    assert popup._panel_y == PANEL_Y

    popup.dismiss()
    assert engine.is_animating(popup) is False


def test_successful_calculation_starts_a_result_feedback_pulse():
    engine.cancel_all_animations()
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("1+1")

    screen._enter()

    assert screen.history == [("1+1", 2.0)]
    assert screen._result_pulse == 15
    assert engine.is_animating(screen) is True
    screen.deactivate()
    assert screen._result_pulse == 0
    engine.cancel_all_animations()
