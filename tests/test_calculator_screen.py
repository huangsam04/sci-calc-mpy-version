import pytest

from calc.functions import build_registry
from calc.number import Number
from screens import calculator as calculator_module
from screens.calculator import (
    MAX_HISTORY_ENTRIES, MAX_HISTORY_EXPRESSION_CHARS, CalculatorScreen,
    MAX_EXPRESSION_CHARS)
from ui.element import SETTLE_REDRAW
from ui.motion import DAMAGE_FULL, DAMAGE_NONE, DAMAGE_PARTIAL, DamageMap


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


class LiveShiftKeyboard(KeyboardStub):
    def is_pressed(self, row, col):
        return (row, col) == (4, 0)


class CalculatorDisplay:
    def __init__(self):
        self.direct = []
        self.text = []
        self.fills = []
        self.hlines = []

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        self.fills.append(args)

    def draw_hline(self, *args):
        self.hlines.append(args)

    def draw_vline(self, *args):
        pass

    def draw_text8x8(self, x, y, text, gs=15):
        self.text.append((x, y, text, gs))

    def draw_text(self, x, y, text, font, invert=False, gs=15, raw=False):
        self.text.append((x, y, text, gs))

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct.append((x, y, text, font, gs))


class FontStub:
    width = 5
    height = 7

    def measure_text(self, text, spacing=1):
        return len(text) * (self.width + spacing)


def _history_screen(font=None, total=20, offset=0):
    screen = CalculatorScreen(font, font, build_registry(), {})
    screen._state[0] = [("expression " + str(index), index)
                      for index in range(total)]
    screen.mode = 1
    screen._state[3][1] = offset
    screen._state[3][2] = offset
    return screen


def test_calculator_consumes_supplied_event_and_records_result():
    keyboard = KeyboardStub()
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.activate()

    for event in ((3, 1, False), (1, 3, False), (3, 2, False), (3, 3, False)):
        screen.update(keyboard, event)

    assert screen._state[0] == [("2+3", 5.0)]
    assert screen.input_box.get_str() == ""
def test_calculator_uses_queued_shift_snapshot_not_live_matrix_state():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("1")
    screen.input_box.move_cursor_end()

    # Shift was held with ENT but is already released while the slow frame
    # consumes the queued edge.
    screen.update(KeyboardStub(), (3, 3, True))

    assert screen.input_box.get_str() == "1="
    assert screen._state[0] == []

    # Conversely, a later physical Shift press must not reinterpret an old
    # ordinary ENT edge.
    screen.input_box.set_str("1+1")
    screen.update(LiveShiftKeyboard(), (3, 3, False))

    assert screen.input_box.get_str() == ""
    assert screen._state[0][0][0] == "1+1"


def test_calculator_exposes_letters_only_in_non_modal_input_context():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})

    assert screen.letter_input_target() is screen.input_box
    assert screen.blocks_global_shortcuts() is False

    screen.mode = 1
    assert screen.letter_input_target() is None

    screen.mode = 2
    assert screen.letter_input_target() is None
    assert screen.blocks_global_shortcuts() is True


def test_assignment_marks_context_for_persistence():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("x=7")

    screen.update(KeyboardStub(), (3, 3, False))

    assert screen.vars == {"x": 7.0}
    assert screen.context.consume_dirty() is True


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

    screen._refresh_panel_layout()
    assert screen.input_box.height == 12
    assert screen.input_box.active_rows == 1

    screen.input_box.set_str(expression)
    screen.input_box.move_cursor_end()

    assert (screen.input_box._state[0] >> 19) & 511 == 96
    assert ((screen.input_box._state[0] >> 18) & 1) + 1 == 2
    assert screen.input_box.get_str() == expression
    assert screen.input_box.view_offset > 0
    screen._refresh_panel_layout()
    assert screen.input_box.height == 22
    assert screen.input_box.active_rows == 2

    screen.input_box.clear_str()

    screen._refresh_panel_layout()
    assert screen.input_box.height == 12
    assert screen.input_box.active_rows == 1


def test_expanding_the_input_panel_keeps_selected_history_visible():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen._state[0] = [(str(index), float(index)) for index in range(4)]
    screen._state[3][1] = 3

    screen._refresh_panel_layout()
    compact_history_rows = 3 if screen.input_box.height == 12 else 2
    screen._clamp_view(compact_history_rows)

    assert compact_history_rows == 3
    assert screen._state[3][2] == 1

    screen.input_box.set_str("1+" * 47 + "1")
    screen._refresh_panel_layout()
    expanded_history_rows = 3 if screen.input_box.height == 12 else 2
    screen._clamp_view(expanded_history_rows)

    assert expanded_history_rows == 2
    assert screen._state[3][2] == 2


def test_history_uses_two_rows_for_newest_and_two_compact_older_entries():
    screen = CalculatorScreen(
        FontStub(), registry=build_registry(), variables={})
    long_result = "123456789012345678901234567890"
    screen._state[0] = [
        ("new expression", long_result),
        ("older one", "2"),
        ("older two", "3"),
        ("off screen", "4"),
    ]
    screen.mode = 1
    display = CalculatorDisplay()

    screen.draw(display)

    rows = [item for item in display.direct if 15 <= item[1] < 54]
    assert [(x, y) for x, y, *_rest in rows] == [
        (4, 15),
        (14, 24),
        (4, 33),
        (4, 42),
    ]
    assert rows[0][2] == b"new expression"
    assert rows[1][2] == b"= " + long_result.encode()
    assert rows[2][2] == b"older one = 2"
    assert rows[3][2] == b"older two = 3"
    assert len(rows[1][2]) > 13


def test_history_selection_uses_nonlinear_cursor_motion(monkeypatch):
    clock = [100]
    monkeypatch.setattr(calculator_module.time, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr("ui.inputbox.time.ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        "ui.inputbox.time.ticks_diff", lambda newer, older: newer - older)
    screen = _history_screen(FontStub(), total=5)
    screen.draw(CalculatorDisplay())
    start_y = screen.input_box.cursor.y

    assert screen.update(KeyboardStub(), (3, 1, False)) == "REDRAW"
    first_y = screen.input_box.cursor.y
    assert screen.motion_active is True
    assert start_y < first_y < 33

    clock[0] = 148
    assert screen.advance_motion(clock[0]) is True
    assert first_y < screen.input_box.cursor.y < 33
    clock[0] = 196
    assert screen.advance_motion(clock[0]) is True
    assert screen.input_box.cursor.y == 33
    assert screen.motion_active is False


def test_history_selection_refreshes_footer_before_motion_frame():
    screen = _history_screen(total=5)
    screen._ensure_footer_cache()

    assert screen.update(KeyboardStub(), (3, 1, False)) == "REDRAW"

    footer = screen._state[2][1]
    assert footer[5] == 1
    assert footer[8] == 1
    assert footer[2] == "2/5"


def test_calculator_input_caret_blinks_during_quiet_steps(monkeypatch):
    clock = [0]
    monkeypatch.setattr(calculator_module.time, "ticks_ms", lambda: clock[0])
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.activate()
    screen.mark_presented()

    assert screen.input_box.cursor.is_visible is True
    clock[0] = 500
    assert screen.settle_step() == SETTLE_REDRAW
    assert screen.input_box.cursor.is_visible is False
    clock[0] = 1000
    assert screen.settle_step() == SETTLE_REDRAW
    assert screen.input_box.cursor.is_visible is True


def test_input_change_refreshes_footer_before_caret_motion_frame():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.activate()

    assert screen.update(KeyboardStub(), (3, 0, False)) == "REDRAW"
    assert screen.motion_active is True

    footer = screen._state[2][1]
    assert footer[5] == 0
    assert footer[6] == 1
    assert footer[2] == "1/96"


def test_typing_splits_editor_and_footer_rows_but_wrapping_uses_full_frame():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    footer = screen._state[2][1]
    screen.activate()
    screen.mark_presented()
    presented = screen._state[2][0]
    assert screen._state[2][0] is presented
    assert screen._state[2][1] is footer

    screen.input_box.insert_str("1")
    damage = DamageMap()
    assert screen.collect_present_damage(damage) == DAMAGE_PARTIAL
    assert damage.ranges == [[0, 12], [0, 0]]
    assert not hasattr(screen, "_editor_present_state")

    screen.mark_presented()
    assert screen.settle_step() == SETTLE_REDRAW
    damage.clear()
    assert screen.collect_present_damage(damage) == 3
    assert damage.ranges == [[54, 10], [0, 0]]
    screen.draw_present_rows(CalculatorDisplay())
    screen.mark_presented()
    damage.clear()
    assert screen.collect_present_damage(damage) == DAMAGE_NONE

    screen.input_box.set_str("1" * 25, immediate=True)
    screen.mark_presented()
    screen.input_box.insert_str("1")
    damage.clear()
    assert screen.collect_present_damage(damage) == DAMAGE_FULL
    screen._invalidate_footer_cache()
    assert screen._state[2][0] is presented
    assert screen._state[2][1] is footer
    assert not hasattr(screen, "_presented_mode")
    assert not hasattr(screen, "_footer_hint")


def test_activation_prepares_first_present_caches_before_animation_frames():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})

    screen.activate()
    presented = screen._state[2][0]
    footer = screen._state[2][1]
    screen.mark_presented()

    assert presented is not None
    assert footer is not None
    assert screen._state[2][0] is presented
    assert screen._state[2][1] is footer


def test_calculator_propagates_memory_error_without_opening_an_error_popup(
        monkeypatch):
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("1+1")

    def exhaust_heap(expression, context):
        raise MemoryError("injected")

    monkeypatch.setattr(calculator_module, "evaluate", exhaust_heap)

    with pytest.raises(MemoryError, match="injected"):
        screen._enter()

    assert screen._state[0] == []
    assert screen.input_box.get_str() == "1+1"
    assert screen._state[1].active is False


def test_long_history_is_bounded_by_total_lossless_expression_text():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})

    for index in range(MAX_HISTORY_ENTRIES):
        prefix = str(index)
        expression = prefix + "0" * (MAX_EXPRESSION_CHARS - len(prefix))
        screen.input_box.set_str(expression, immediate=True)
        screen._enter()

    history = screen._state[0]
    assert len(history) == (
        MAX_HISTORY_EXPRESSION_CHARS // MAX_EXPRESSION_CHARS)
    assert sum(len(item[0]) for item in history) <= (
        MAX_HISTORY_EXPRESSION_CHARS)


def test_error_at_full_history_budget_does_not_evict_a_valid_entry():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})

    for index in range(MAX_HISTORY_ENTRIES):
        prefix = str(index)
        width = 46 if index == MAX_HISTORY_ENTRIES - 1 else 38
        screen.input_box.set_str(
            prefix + "0" * (width - len(prefix)), immediate=True)
        screen._enter()
    history = screen._state[0]
    before = tuple(history)
    assert len(history) == MAX_HISTORY_ENTRIES
    assert sum(len(item[0]) for item in history) == (
        MAX_HISTORY_EXPRESSION_CHARS)

    screen.input_box.set_str("1/0", immediate=True)
    screen._enter()

    assert tuple(history) == before
    assert screen.mode == 2
    assert screen._state[1].active is True


def test_error_popup_quiet_tick_does_not_redraw_until_a_visible_dismissal():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen._state[1].show_static("Error", "Try again")
    screen.mode = 2

    assert screen.update(KeyboardStub(), None) is None
    assert screen.mode == 2
    assert screen._state[1].active is True
    assert screen.update(KeyboardStub(), (3, 3, False)) == "REDRAW"
    assert screen.mode == 0
    assert screen._state[1].active is False
    assert screen.update(KeyboardStub(), None) is None


def test_history_result_recall_uses_lossless_literal_not_display_precision():
    keyboard = KeyboardStub()
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("1/3")
    screen.update(keyboard, (3, 3, False))
    original = screen._state[0][0][1]

    screen.set_display_digits(1)
    assert screen._fmt(original) != original.to_literal()

    screen.update(keyboard, (4, 5, False))
    screen.update(keyboard, (3, 3, False))

    assert screen.input_box.get_str() == original.to_literal()
    assert screen.mode == 0

    screen.update(keyboard, (3, 3, False))
    assert screen._state[0][0][1] == original


def test_history_recall_keeps_selection_when_literal_would_overflow_input():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    result = Number.parse("123456789012345678901234567890e-30")
    literal = result.to_literal()
    existing = "x" * (MAX_EXPRESSION_CHARS - len(literal) + 1)
    screen.input_box.set_str(existing, immediate=True)
    screen.input_box.move_cursor_end()
    screen._state[0] = [("1/3", result)]
    screen.mode = 1

    assert screen.update(KeyboardStub(), (3, 3, False)) == "REDRAW"

    assert screen.mode == 1
    assert screen.input_box.get_str() == existing
    assert screen._state[3][0][1] == "Input full"
    assert screen.update(KeyboardStub(), (3, 3, False)) is None


def test_history_non_numeric_result_is_display_only():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen._state[0] = [("plugin()", "not calculator input")]
    screen.mode = 1

    assert screen.update(KeyboardStub(), (3, 3, False)) == "REDRAW"

    assert screen.mode == 1
    assert screen.input_box.get_str() == ""
    assert screen._state[3][0][1] == "Result is display-only"
    assert screen.update(KeyboardStub(), (3, 3, False)) is None


@pytest.mark.parametrize(
    ("result", "expected"),
    (
        (Number.parse("-2.5"), "-25e-1"),
        (Number(1, 999999999), "1e999999999"),
        (5.0, "5e0"),
    ),
)
def test_history_numeric_recall_accepts_negative_huge_and_legacy_float_results(
        result, expected):
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen._state[0] = [("saved", result)]
    screen.mode = 1

    screen.update(KeyboardStub(), (3, 3, False))

    assert screen.mode == 0
    assert screen.input_box.get_str() == expected


def test_history_expression_insert_and_edit_are_distinct_actions():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen._state[0] = [("2+3", Number(5))]
    screen.input_box.set_str("1+", immediate=True)
    screen.input_box.move_cursor_end()
    screen.mode = 1

    screen.update(KeyboardStub(), (2, 0, False))

    assert screen.input_box.get_str() == "1+2+3"
    assert screen.mode == 0

    screen.input_box.set_str("1+", immediate=True)
    screen.input_box.move_cursor_end()
    screen.mode = 1
    screen.update(KeyboardStub(), (2, 2, False))

    assert screen.input_box.get_str() == "2+3"
    assert screen.input_box.cursor_pos == len("2+3")
    assert screen.mode == 0


def test_calculator_partial_editor_path_uses_scalar_layout_state():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    display = CalculatorDisplay()
    screen.activate()
    screen.mark_presented()
    screen.input_box.insert_str("1")

    assert not hasattr(CalculatorScreen, "_panel_layout")
    screen.draw_present_rows(display)


def test_calculator_partial_editor_redraw_keeps_static_footer_hint():
    font = FontStub()
    screen = CalculatorScreen(font, registry=build_registry(), variables={})
    display = CalculatorDisplay()
    screen.activate()
    screen.draw(display)
    screen.mark_presented()
    display.direct.clear()
    display.text.clear()
    display.fills.clear()
    display.hlines.clear()

    screen.input_box.insert_str("1")
    damage = DamageMap()
    assert screen.collect_present_damage(damage) == DAMAGE_PARTIAL
    screen.draw_present_rows(display)
    screen.mark_presented()

    footer_draws = [item for item in display.direct if item[1] == 56]
    assert all(
        item[2] != calculator_module._INPUT_FOOTER_HINT.encode()
        for item in footer_draws)
    assert not any(item[2] == b"1/96" for item in footer_draws)
    assert (130, 54, 80, 10, 0) not in display.fills

    assert screen.settle_step() == SETTLE_REDRAW
    damage.clear()
    assert screen.collect_present_damage(damage) == 3
    screen.draw_present_rows(display)
    screen.mark_presented()

    footer_draws = [item for item in display.direct if item[1] == 56]
    assert any(item[2] == b"1/96" for item in footer_draws)
    assert (130, 54, 80, 10, 0) in display.fills
    assert (130, 54, 80, 8) in display.hlines


def test_calculator_detach_and_rebuild_preserves_user_state_by_identity():
    registry = build_registry()
    variables = {}
    screen = CalculatorScreen(
        None, registry=registry, variables=variables, display_digits=7)
    screen.input_box.set_str("1+2")
    history = screen._state[0]
    history.append(("1+2", "3"))
    input_box = screen.input_box
    context = screen.context

    retained = screen.detach_state()
    restored = CalculatorScreen(
        None, registry=registry, variables=variables,
        retained_state=retained)

    assert restored.input_box is input_box
    assert restored.context is context
    assert restored._state[0] is history
    assert restored.input_box.get_str() == "1+2"
    assert restored._state[3][0][2] == 7


def test_calculator_history_cache_formats_only_the_visible_window(monkeypatch):
    screen = _history_screen(total=20, offset=16)
    display = CalculatorDisplay()
    formatted = []

    def count_format(_screen, value):
        formatted.append(value)
        return "result " + str(value)

    monkeypatch.setattr(CalculatorScreen, "_fmt", count_format)

    screen.draw(display)

    assert formatted == [16, 17, 18]
    assert screen._state[2][2][:3] == screen._state[0][16:19]
    assert len(screen._state[2][2]) == 12


def test_calculator_history_cache_reuses_visible_text_until_a_key_changes(
        monkeypatch):
    screen = _history_screen(total=8, offset=0)
    formatted = []

    def count_format(_screen, value):
        formatted.append(value)
        return "result " + str(value)

    monkeypatch.setattr(CalculatorScreen, "_fmt", count_format)

    screen._ensure_history_cache(4)
    cached_expr = screen._state[2][2][4]
    cached_result = screen._state[2][2][8]
    screen._ensure_history_cache(4)

    assert formatted == [0, 1, 2]
    assert screen._state[2][2][4] is cached_expr
    assert screen._state[2][2][8] is cached_result

    screen._state[3][1] = 1
    screen._state[3][2] = 1
    screen._ensure_history_cache(4)
    assert formatted == [0, 1, 2, 1, 2, 3]

    screen._ensure_history_cache(3)
    assert formatted == [0, 1, 2, 1, 2, 3]

    screen._state[0][1] = ["replacement", 99]
    screen._ensure_history_cache(3)
    assert formatted[-3:] == [99, 2, 3]

    screen._state[0][1] = ["replacement", 101]
    screen._ensure_history_cache(3)
    assert formatted[-3:] == [101, 2, 3]

    screen.set_display_digits(1)
    screen._ensure_history_cache(3)
    assert formatted[-3:] == [101, 2, 3]

    screen._state[0].append(("offscreen", 100))
    screen._ensure_history_cache(3)
    assert formatted[-3:] == [101, 2, 3]

    format_count = len(formatted)
    screen._state[0] = list(screen._state[0])
    screen._ensure_history_cache(3)
    assert len(formatted) == format_count


def test_calculator_history_font_cache_reuses_encoded_visible_rows(
        monkeypatch):
    screen = _history_screen(FontStub(), total=4)
    display = CalculatorDisplay()

    screen.draw(display)
    expr_bytes = screen._state[2][2][4:7]
    result_bytes = screen._state[2][2][8:11]

    def unexpected_format(*_args):
        raise AssertionError("steady history draw must reuse cached text")

    monkeypatch.setattr(CalculatorScreen, "_fmt", unexpected_format)
    screen.draw(display)

    assert all(isinstance(value, bytes) for value in expr_bytes)
    assert all(isinstance(value, bytes) for value in result_bytes)
    assert screen._state[2][2][4:7] == expr_bytes
    assert screen._state[2][2][8:11] == result_bytes
    history_direct = [row for row in display.direct if 15 <= row[1] < 54]
    assert len(history_direct) == 8
    assert all(isinstance(row[2], bytes) for row in history_direct)


def test_calculator_history_cache_release_preserves_lossless_history():
    screen = _history_screen(total=6)
    screen._ensure_history_cache(4)
    history = screen._state[0]

    assert screen.release_memory() is True
    assert screen._state[0] is history
    assert screen._state[2][2] is None
    assert screen._state[2][3] == 0


def test_calculator_activation_rebuilds_visible_caches_before_page_draw(
        monkeypatch):
    screen = _history_screen(total=3)
    screen.input_box.set_str("12345", immediate=True)
    screen.draw(CalculatorDisplay())

    assert screen.release_memory() is True
    assert screen._state[2][0] is None
    assert screen._state[2][2] is None

    screen.activate()

    assert screen._state[2][0] is not None
    assert screen._state[2][2] is not None

    def unexpected_format(*_args):
        raise AssertionError("page draw rebuilt Calculator history")

    monkeypatch.setattr(CalculatorScreen, "_fmt", unexpected_format)
    screen.draw(CalculatorDisplay())


def test_calculator_history_cache_propagates_memory_error(monkeypatch):
    screen = _history_screen(total=4)

    def exhaust_heap(_screen, _value):
        raise MemoryError("history cache exhausted")

    monkeypatch.setattr(CalculatorScreen, "_fmt", exhaust_heap)

    with pytest.raises(MemoryError, match="history cache exhausted"):
        screen._ensure_history_cache(4)

    assert screen._state[2][2] == [None] * 12
    assert screen._state[2][3] == 0
