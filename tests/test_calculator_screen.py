from calc.functions import build_registry
from screens.calculator import CalculatorScreen


class KeyboardStub:
    def is_pressed(self, row, col):
        return False

    def get_hold_time(self, row, col):
        return 0

    def consume_long_press(self, row, col, threshold):
        return False


class HeldDeleteKeyboard(KeyboardStub):
    def is_pressed(self, row, col):
        return (row, col) == (4, 3)

    def get_hold_time(self, row, col):
        return 800


def test_calculator_consumes_supplied_event_and_records_result():
    keyboard = KeyboardStub()
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.activate()

    for event in ((3, 1, False), (1, 3, False), (3, 2, False), (3, 3, False)):
        screen.update(keyboard, event)

    assert screen.history == [("2+3", 5.0)]
    assert screen.input_box.get_str() == ""


def test_assignment_marks_context_for_persistence():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("x=7")

    screen.update(KeyboardStub(), (3, 3, False))

    assert screen.vars == {"x": 7.0}
    assert screen.context.consume_dirty() is True


def test_calculator_snapshot_restores_input_and_history_progressively():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("x+1")
    screen.input_box.cursor_pos = 2
    screen.history = [("2+3", 5.0), ("4*6", 24.0)]
    state = screen.snapshot_state()

    screen.reset_state()
    assert screen.input_box.get_str() == ""
    assert screen.history == []

    screen.restore_state(state)
    assert screen.input_box.get_str() == "x+1"
    assert screen.history == []

    assert screen.settle_step()
    assert screen.history == [("2+3", 5.0)]
    assert screen.settle_step() == 2
    assert screen.history == [("2+3", 5.0), ("4*6", 24.0)]


def test_held_delete_requests_redraw_without_a_new_edge(monkeypatch):
    now = [1_000]
    monkeypatch.setattr("ui.inputbox.time.ticks_ms", lambda: now[0])
    monkeypatch.setattr("ui.inputbox.time.ticks_diff", lambda a, b: a - b)
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("123")
    screen.input_box.move_cursor_end()

    assert screen.update(HeldDeleteKeyboard(), None) == "REDRAW"
    assert screen.input_box.get_str() == "12"


def test_calculator_expands_the_input_panel_only_after_the_first_line_wraps():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    expression = "1+" * 47 + "1"

    assert screen._panel_layout() == (13, 15, 4)
    assert screen.input_box.height == 12
    assert screen.input_box.active_rows == 1

    screen.input_box.set_str(expression)
    screen.input_box.move_cursor_end()

    assert screen.input_box.max_char == 96
    assert screen.input_box.visible_rows == 2
    assert screen.input_box.get_str() == expression
    assert screen.input_box.view_offset > 0
    assert screen._panel_layout() == (23, 25, 3)
    assert screen.input_box.height == 22
    assert screen.input_box.active_rows == 2

    screen.input_box.clear_str()

    assert screen._panel_layout() == (13, 15, 4)
    assert screen.input_box.height == 12
    assert screen.input_box.active_rows == 1


def test_expanding_the_input_panel_keeps_selected_history_visible():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.history = [(str(index), float(index)) for index in range(4)]
    screen._cursor = 3

    _, _, compact_history_rows = screen._panel_layout()
    screen._clamp_view(compact_history_rows)

    assert compact_history_rows == 4
    assert screen._view_offset == 0

    screen.input_box.set_str("1+" * 47 + "1")
    _, _, expanded_history_rows = screen._panel_layout()
    screen._clamp_view(expanded_history_rows)

    assert expanded_history_rows == 3
    assert screen._view_offset == 1


def test_typing_uses_editor_and_footer_rows_but_wrapping_falls_back_to_full_frame():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.activate()
    screen.mark_presented()

    screen.input_box.insert_str("1")
    assert screen.get_present_rows() == ((0, 12), (54, 10))

    screen.input_box.set_str("1" * 25, immediate=True)
    screen.mark_presented()
    screen.input_box.insert_str("1")
    assert screen.get_present_rows() is None
