from ui.inputbox import _FUNCTION_KEY_INSERTS, InputBox


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


class DirectDisplaySpy(DisplaySpy):
    def __init__(self):
        super().__init__()
        self.direct_rows = []

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct_rows.append((x, y, text, font, gs))


class FontStub:
    width = 5
    height = 7

    def measure_text(self, text, spacing=1):
        return len(text) * (self.width + spacing)


class KeyboardStub:
    def is_pressed(self, _row, _col):
        return False

    def get_hold_time(self, _row, _col):
        return 0


def test_two_line_viewport_wraps_and_keeps_cursor_in_view():
    # 34px width leaves room for three 8px fallback-font characters per row.
    box = InputBox(0, 0, 34, 18, 96, visible_rows=2)
    box.set_str("123456789")
    box.move_cursor_end()

    assert box.get_str() == "123456789"
    assert box.active_rows == 2
    assert box.view_offset == 3

    display = DisplaySpy()
    box.draw(display)

    assert [text for _, _, text in display.text_rows] == ["456", "789"]


def test_two_row_editor_stays_compact_until_text_wraps():
    # The usable width fits three fallback-font characters per row.
    box = InputBox(0, 0, 34, 12, 96, visible_rows=2)
    box.set_str("123")

    assert box.active_rows == 1

    box.set_str("1234")

    assert box.active_rows == 2


def test_visible_window_cache_has_a_fixed_two_row_upper_bound():
    box = InputBox(0, 0, 34, 18, 96, visible_rows=999)

    assert ((box._state[0] >> 18) & 1) + 1 == 2
    assert len(box._state[5]) == 2
    assert len(box._state[6]) == 2


def test_visible_window_cache_is_bounded_and_reused_for_steady_draws():
    box = InputBox(0, 0, 34, 18, 96, FontStub(), visible_rows=2)
    box.set_str("123456789")
    box.move_cursor_end()

    first = DirectDisplaySpy()
    box.draw(first)
    second = DirectDisplaySpy()
    box.draw(second)

    first_rows = first.direct_rows[:2]
    second_rows = second.direct_rows[:2]
    assert [row[2] for row in first_rows] == [b"5678", b"9"]
    assert all(
        len(row[2]) <= ((box._state[1] >> 18) & 511)
        for row in first_rows)
    assert second_rows[0][2] is first_rows[0][2]
    assert second_rows[1][2] is first_rows[1][2]


def test_short_append_batch_rebuilds_the_visible_window_only_when_drawn(
        monkeypatch):
    box = InputBox(0, 0, 210, 12, 96)
    rebuilds = []
    original = InputBox._cache_visible_rows

    def record_rebuild(target):
        rebuilds.append(True)
        return original(target)

    monkeypatch.setattr(InputBox, "_cache_visible_rows", record_rebuild)

    for digit in "12345":
        assert box.insert_str(digit) is True

    assert rebuilds == []
    display = DisplaySpy()
    box.draw(display)
    assert rebuilds == [True]
    assert [text for _, _, text in display.text_rows] == ["12345"]


def test_expression_capacity_is_separate_from_visible_space():
    box = InputBox(0, 0, 34, 18, 96, visible_rows=2)
    expression = "1+" * 47 + "1"

    assert len(expression) == 95
    assert box.insert_str(expression) is True
    assert box.get_str() == expression
    assert box.insert_str("+") is True
    assert box.insert_str("1") is False
    assert len(box.get_str()) == 96


def test_cross_panel_try_insert_is_all_or_nothing_when_input_is_full():
    box = InputBox(0, 0, 34, 12, 4)
    box.set_str("123", immediate=True)
    box.move_cursor_end()

    assert box.try_insert("45") is False
    assert box.get_str() == "123"
    assert box.try_insert("4") is True
    assert box.get_str() == "1234"


def test_typing_and_cursor_motion_uses_nonlinear_ease_out(monkeypatch):
    clock = [100]
    monkeypatch.setattr("ui.inputbox.time.ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        "ui.inputbox.time.ticks_diff", lambda newer, older: newer - older)
    box = InputBox(0, 0, 34, 12, 96)

    assert box.insert_str("1") is True
    first = box.cursor.x
    target = box.x + 1 + 8
    assert box.motion_active is True
    assert box.x + 1 < first < target

    clock[0] = 124
    assert box.advance_motion(clock[0]) is True
    early = box.cursor.x
    clock[0] = 148
    assert box.advance_motion(clock[0]) is True
    late = box.cursor.x
    assert first < early < late < target
    assert early - first > late - early

    clock[0] = 196
    assert box.advance_motion(clock[0]) is True
    assert box.cursor.x == target
    assert box.motion_active is False
    assert len(box._state[7:]) == 3
    assert all(isinstance(value, int) for value in box._state[7:])


def test_restored_text_can_position_the_cursor_without_animation():
    box = InputBox(0, 0, 34, 12, 96)

    box.set_str("123", immediate=True)

    assert box.cursor.x > box.x


def test_function_key_insert_uses_mapping_and_generic_change_action():
    box = InputBox(max_char=16)

    assert _FUNCTION_KEY_INSERTS["sin"] == "sin("
    assert _FUNCTION_KEY_INSERTS["asin"] == "asin("
    # Callers react to the public edit action, not a key-specific label.
    # The inserted text below proves the shared mapping selected the key.
    assert box.update(KeyboardStub(), (0, 4, False)) == "CHANGE"
    assert box.update(KeyboardStub(), (0, 4, True)) == "CHANGE"
    assert box.get_str() == "sin(asin("
