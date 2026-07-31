from calc.functions import build_registry
from screens import stopwatch as stopwatch_module
from screens.calculator import CalculatorScreen
from screens.main_menu import MainMenu
from screens.stopwatch import StopwatchScreen, STOPWATCH_FRAME_MS
from ui.error_popup import ErrorPopup
from ui.inputbox import InputBox, UPPER_CONTINUATION_CUE
from ui.menu import Menu
from ui.motion import (
    DAMAGE_FULL, DAMAGE_PARTIAL, DamageMap, FrameScheduler)
from ui import menu as menu_module
from ui import motion as motion_module


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


class StopwatchDisplaySpy:
    def __init__(self):
        self.fills = []
        self.lines = []
        self.direct = []

    def fill_rectangle(self, *args):
        self.fills.append(args)

    def draw_hline(self, *args):
        self.lines.append(args)

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


def test_menu_marker_eases_to_the_selected_row_without_waiting_for_feedback(
        monkeypatch):
    clock = [100]
    monkeypatch.setattr(menu_module.time, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        menu_module.time, "ticks_diff", lambda newer, older: newer - older)
    menu = Menu(0, 0, 80, visible_rows=2, row_height=12)
    menu.add_item("A", object())
    menu.add_item("Long", object())
    menu.activate()

    menu.move_cursor_down()

    assert menu.cursor.y == 4
    assert menu.cursor.width == 36
    assert menu.motion_active is True

    clock[0] = 148
    assert menu.advance_motion(clock[0]) is True
    assert 4 < menu.cursor.y < 14

    clock[0] = 196
    assert menu.advance_motion(clock[0]) is True
    assert menu.motion_active is False

    display = MenuDisplaySpy()
    menu.draw(display)

    assert menu.cursor.y == 14
    assert menu.cursor.width == 36
    assert display.fills == [(2, 14, 36, 8, 14)]
    assert [row[3] for row in display.text] == [15, 0]


def test_inactive_menus_can_share_cursor_and_restore_their_own_selection():
    first = Menu(0, 13, 80, visible_rows=2, row_height=12)
    second = Menu(
        0, 13, 80, visible_rows=2, row_height=12,
        cursor=first.cursor)
    for menu in (first, second):
        menu.add_item("A", None)
        menu.add_item("Long", None)

    first.cursor_pos = 1
    first.activate()
    assert first.cursor.y == 27
    assert first.cursor.width == 36

    second.cursor_pos = 0
    second.activate()
    assert second.cursor is first.cursor
    assert second.cursor.y == 15
    assert second.cursor.width == 12


def test_menu_row_update_covers_old_and_new_highlights():
    menu = Menu(0, 13, 80, visible_rows=2, row_height=12)
    menu.add_item("A", object())
    menu.add_item("B", object())
    menu.activate()
    menu.mark_presented()

    menu.move_cursor_down()

    damage = DamageMap()
    assert menu.collect_present_damage(64, damage) == DAMAGE_PARTIAL
    assert damage.ranges == [[13, 26], [0, 0]]


def test_menu_partial_draw_rebuilds_only_the_affected_rows():
    menu = Menu(0, 13, 210, visible_rows=4, row_height=12)
    for label in ("A", "B", "C", "D"):
        menu.add_item(label, object())
    menu.activate()
    menu.mark_presented()
    menu.move_cursor_down()
    display = MenuDisplaySpy()

    menu.draw_present_rows(display)

    assert display.fills[0] == (1, 14, 208, 14, 0)
    assert display.fills[1] == (2, 17, 12, 8, 14)
    assert display.text == [
        (4, 15, "A", 0),
        (4, 27, "B", 15),
    ]


def test_main_menu_advances_highlight_and_reports_one_fixed_two_row_band(
        monkeypatch):
    clock = [100]
    monkeypatch.setattr(menu_module.time, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        menu_module.time, "ticks_diff", lambda newer, older: newer - older)
    screen = MainMenu()
    screen.add_screen("A", object())
    screen.add_screen("B", object())
    screen.activate()
    screen.mark_presented()

    assert screen.update(None, (3, 1, False)) == "REDRAW"
    assert type(screen.menu) is Menu
    assert screen.menu.cursor.y == 17
    damage = DamageMap()
    assert screen.collect_present_damage(damage) == DAMAGE_PARTIAL
    assert damage.ranges == [[13, 26], [0, 0]]
    clock[0] = 196
    assert screen.advance_motion(clock[0]) is True
    assert screen.menu.cursor.y == 27
    assert screen.update(None, (3, 1, False)) is None
    assert screen.menu.cursor_pos == 1


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


def test_menu_motion_retargets_from_its_visible_position(monkeypatch):
    clock = [100]
    monkeypatch.setattr(menu_module.time, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        menu_module.time, "ticks_diff", lambda newer, older: newer - older)
    menu = Menu(0, 0, 80, visible_rows=2, row_height=12)
    menu.add_item("A", object())
    menu.add_item("B", object())
    menu.activate()

    menu.move_cursor_down()
    clock[0] = 148
    menu.advance_motion(clock[0])
    reversing_y = menu.cursor.y
    menu.move_cursor_up()

    assert menu.cursor.y == reversing_y - 2
    clock[0] = 244
    assert menu.advance_motion(clock[0]) is True
    assert menu.cursor.y == 2
    assert menu.motion_active is False


def test_menu_motion_snaps_when_selection_scrolls_the_viewport(monkeypatch):
    clock = [100]
    monkeypatch.setattr(menu_module.time, "ticks_ms", lambda: clock[0])
    menu = Menu(0, 0, 80, visible_rows=2, row_height=12)
    for label in ("A", "B", "C"):
        menu.add_item(label, object())
    menu.cursor_pos = 1
    menu.activate()

    menu.move_cursor_down()

    assert menu.view_offset == 1
    assert menu.cursor.y == 14
    assert menu.motion_active is False


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

    assert popup.active is True
    assert not hasattr(popup, "_shade")
    assert not hasattr(popup, "_panel_y")


def test_successful_calculation_has_no_animation_state():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("1+1")

    screen._enter()

    assert screen._state[0] == [("1+1", 2.0)]
    assert not hasattr(screen, "_result_pulse")


def test_damage_map_merges_adjacent_bands_without_replacing_its_backing():
    damage = DamageMap()
    backing = damage.ranges

    assert damage.add(10, 3) is True
    assert damage.add(13, 4) is True

    assert damage.ranges is backing
    assert damage.count == 1
    assert backing == [[10, 7], [0, 0]]


def test_damage_map_capacity_fallback_clears_stale_ranges_for_next_partial():
    damage = DamageMap()

    damage.add(2, 3)
    damage.add(12, 3)
    assert damage.add(24, 3) is False

    assert damage.full is True
    assert damage.count == 0
    assert damage.ranges == [[0, 0], [0, 0]]

    damage.clear()
    assert damage.add(40, 4) is True
    assert damage.ranges == [[40, 4], [0, 0]]


def test_damage_map_clear_and_add_only_require_indexed_fixed_backing():
    class IndexedOnlyRanges:
        def __init__(self):
            self.values = [[3, 4], [8, 2]]

        def __len__(self):
            return len(self.values)

        def __getitem__(self, index):
            return self.values[index]

        def __iter__(self):
            raise AssertionError("DamageMap hot path must not build an iterator")

    damage = DamageMap()
    backing = IndexedOnlyRanges()
    damage._ranges = backing
    damage._count = 2

    damage.clear()
    assert backing.values == [[0, 0], [0, 0]]
    assert damage.add(10, 3) is True
    assert backing.values == [[10, 3], [0, 0]]


def test_frame_scheduler_owns_immediate_idle_quiet_and_background_deadlines(
        monkeypatch):
    monkeypatch.setattr(motion_module.time, "ticks_diff", lambda newer, older: newer - older)
    monkeypatch.setattr(motion_module.time, "ticks_add", lambda value, delta: value + delta)
    scheduler = FrameScheduler(
        100, idle_frame_ms=66, background_idle_ms=750,
        sidebar_refresh_ms=5000)

    scheduler.request_render()
    assert scheduler.should_present(165) is False
    assert scheduler.should_present(166) is True
    scheduler.mark_presented(166)

    scheduler.note_input(200)
    assert scheduler.should_present(200, input_changed=True) is True
    scheduler.clear_render_request()
    assert scheduler.dirty is False
    assert scheduler.should_present(265, continuous=True) is True
    assert scheduler.background_due(949) is False
    assert scheduler.background_due(950) is True

    assert scheduler.sidebar_poll_due(5099, quiet=True) is False
    assert scheduler.sidebar_poll_due(5100, quiet=False) is False
    assert scheduler.sidebar_poll_due(5100, quiet=True) is True
    assert scheduler.sidebar_poll_due(5101, quiet=True) is False

    scheduler.force_render(800)
    assert scheduler.should_present(800) is True
    scheduler.reset(900, force_render=True)
    assert scheduler.should_present(900) is True


def test_menu_motion_reuses_exactly_four_scalar_state_slots():
    menu = Menu()

    assert len(menu._state[9:]) == 4
    assert all(isinstance(value, int) for value in menu._state[9:])
    assert not any(name.startswith("_motion_") for name in Menu.__slots__)
    assert not hasattr(motion_module, "animations_enabled")
    assert not hasattr(motion_module, "note_animation_heap")


def test_scheduler_records_ignored_input_without_requesting_a_phantom_frame(
        monkeypatch):
    monkeypatch.setattr(motion_module.time, "ticks_diff", lambda newer, older: newer - older)
    scheduler = FrameScheduler(0)

    scheduler.note_input(10)

    assert scheduler.dirty is False
    assert scheduler.should_present(100) is False
    assert scheduler.should_present(
        49, continuous=True, continuous_frame_ms=50) is False
    assert scheduler.should_present(
        50, continuous=True, continuous_frame_ms=50) is True


def test_menu_reports_only_a_real_highlight_move_as_redraw():
    menu = Menu(0, 0, 80, visible_rows=2, row_height=12)
    menu.add_item("A", object())
    menu.add_item("B", object())
    menu.activate()

    assert menu.update(None, (3, 1, False)) == "MOVE"
    assert menu.update(None, (3, 1, False)) is None


def test_stopwatch_steady_frames_only_damage_the_timer_band_and_reuse_bytes(
        monkeypatch):
    clock = [0]
    monkeypatch.setattr(stopwatch_module.time, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        stopwatch_module.time, "ticks_diff", lambda newer, older: newer - older)
    stopwatch = StopwatchScreen(FontStub())
    display = StopwatchDisplaySpy()

    assert stopwatch._start() is True
    stopwatch.mark_presented()
    clock[0] = 50
    damage = DamageMap()

    assert stopwatch.collect_present_damage(damage) == DAMAGE_PARTIAL
    assert damage.count == 1
    assert damage.ranges == [[0, 13], [0, 0]]
    stopwatch.draw_present_rows(display)

    assert display.fills == [(0, 0, 210, 13, 0)]
    assert display.direct[-1][2] is stopwatch._render[0][0]
    assert bytes(stopwatch._render[0][0]) == b"00:00:05"
    assert STOPWATCH_FRAME_MS == 50

    assert stopwatch._pause() is True
    damage.clear()
    assert stopwatch.collect_present_damage(damage) == DAMAGE_FULL


def test_stopwatch_fontless_steady_frames_use_fixed_segments_without_fmt(
        monkeypatch):
    clock = [0]
    monkeypatch.setattr(stopwatch_module.time, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        stopwatch_module.time, "ticks_diff", lambda newer, older: newer - older)

    def unexpected_format(*_args):
        raise AssertionError("steady fontless timer frames must not call _fmt")

    monkeypatch.setattr(
        StopwatchScreen, "_fmt", staticmethod(unexpected_format))
    stopwatch = StopwatchScreen(None)
    display = StopwatchDisplaySpy()
    fixed_buffer = stopwatch._render[0][0]
    damage = DamageMap()

    assert isinstance(fixed_buffer, bytearray)
    assert stopwatch._start() is True
    stopwatch.mark_presented()

    for now in (50, 100):
        clock[0] = now
        damage.clear()
        assert stopwatch.collect_present_damage(damage) == DAMAGE_PARTIAL
        assert damage.ranges == [[0, 13], [0, 0]]
        stopwatch.draw_present_rows(display)
        stopwatch.mark_presented()

    assert stopwatch._render[0][0] is fixed_buffer
    assert bytes(fixed_buffer) == b"00:00:10"
    assert display.direct == []


def test_stopwatch_reuses_one_footer_cache_for_steady_timer_frames(
        monkeypatch):
    stopwatch = StopwatchScreen(FontStub())
    display = StopwatchDisplaySpy()
    hint_bytes = stopwatch._footer[0][1]
    right_bytes = stopwatch._footer[0][3]

    def unexpected_fit(*_args):
        raise AssertionError("steady footer must not refit text")

    monkeypatch.setattr(stopwatch_module, "fit_text", unexpected_fit)
    stopwatch._draw_footer(display)
    stopwatch._draw_footer(display)

    assert stopwatch._footer[0][1] is hint_bytes
    assert stopwatch._footer[0][3] is right_bytes
    assert display.direct[-2:] == [
        (3, 56, hint_bytes, stopwatch._clock[0], 9),
        (stopwatch._footer[1][0], 56, right_bytes, stopwatch._clock[0], 15),
    ]


def test_stopwatch_reuses_only_the_visible_lap_label_strings(monkeypatch):
    stopwatch = StopwatchScreen(FontStub())
    stopwatch._clock[2][3] = [(index + 1, (index + 1) * 1234)
                       for index in range(6)]
    display = StopwatchDisplaySpy()

    stopwatch.draw(display)
    labels = stopwatch._runtime[0][0]
    label_ids = [id(label) for label in labels]

    def unexpected_format(*_args):
        raise AssertionError("steady lap rows must reuse cached labels")

    monkeypatch.setattr(
        StopwatchScreen, "_fmt", staticmethod(unexpected_format))
    stopwatch.draw(display)

    assert [id(label) for label in labels] == label_ids
    assert len(labels) == 4
    assert all(label.startswith("Lap") for label in labels)


def test_stopwatch_extended_hours_are_bounded_fixed_width_and_positioned():
    stopwatch = StopwatchScreen(FontStub())
    display = StopwatchDisplaySpy()

    stopwatch._draw_time(display, 100 * 3600000 + 2 * 60000 + 3 * 1000 + 40)

    assert display.direct[-1] == (
        stopwatch_module._EXTENDED_TIME_X, 2, stopwatch._render[0][2],
        stopwatch._clock[0], 15)
    assert bytes(stopwatch._render[0][2]) == b"100:02:03:04"

    stopwatch._draw_time(display, 1000 * 3600000 + 59 * 60000 + 59 * 1000 + 990)

    assert display.direct[-1][0] == stopwatch_module._EXTENDED_TIME_X
    assert display.direct[-1][2] is stopwatch._render[0][2]
    assert bytes(stopwatch._render[0][2]) == b"999:59:59:99"


def test_stopwatch_detach_and_rebuild_preserves_clock_and_laps():
    stopwatch = StopwatchScreen(FontStub())
    stopwatch._clock[1] = True
    stopwatch._clock[2][1] = 1234
    stopwatch._clock[2][3].append((1, 500))
    clock = stopwatch._clock

    retained = stopwatch.detach_state()
    restored = StopwatchScreen(FontStub(), retained_state=retained)

    assert restored._clock is clock
    assert restored._clock[1] is True
    assert restored._clock[2][3] == [(1, 500)]
