from ui.inputbox import InputBox
from anim import engine


class DisplaySpy:
    def __init__(self):
        self.text_rows = []

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        pass

    def draw_text8x8(self, x, y, text, gs=15):
        self.text_rows.append((x, y, text))

    def draw_vline(self, *args):
        pass


def test_two_line_viewport_wraps_and_keeps_cursor_in_view():
    # 34px width leaves room for three 8px fallback-font characters per row.
    box = InputBox(0, 0, 34, 18, 96, visible_rows=2)
    box.set_str("123456789")
    box.move_cursor_end()

    assert box.get_str() == "123456789"
    assert box.active_rows == 2
    assert box.view_offset == 3
    assert box._visible_ranges() == [(3, 6), (6, 9)]

    display = DisplaySpy()
    box.draw(display)

    assert [text for _, _, text in display.text_rows] == ["456", "789"]


def test_two_row_editor_stays_compact_until_text_wraps():
    # The usable width fits three fallback-font characters per row.
    box = InputBox(0, 0, 34, 12, 96, visible_rows=2)
    box.set_str("123")

    assert box.active_rows == 1
    assert box._visible_ranges() == [(0, 3)]

    box.set_str("1234")

    assert box.active_rows == 2
    assert box._visible_ranges() == [(0, 3), (3, 4)]


def test_expression_capacity_is_separate_from_visible_space():
    box = InputBox(0, 0, 34, 18, 96, visible_rows=2)
    expression = "1+" * 47 + "1"

    assert len(expression) == 95
    assert box.insert_str(expression) is True
    assert box.get_str() == expression
    assert box.insert_str("+") is True
    assert box.insert_str("1") is False
    assert len(box.get_str()) == 96


def test_restored_text_can_position_the_cursor_without_animation():
    engine.cancel_all_animations()
    box = InputBox(0, 0, 34, 12, 96)

    box.set_str("123", immediate=True)

    assert engine.is_animating(box.cursor) is False
