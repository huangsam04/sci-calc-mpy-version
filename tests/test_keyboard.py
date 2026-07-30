from input.keyboard import (
    COL_PINS,
    COLS,
    DEBOUNCE_MS,
    EVENT_QUEUE_CAPACITY,
    Keyboard,
    ROW_PINS,
    ROWS,
    _CALC_MAP,
    _CALC_SHIFT_MAP,
    get_key_label,
)


def _capture_press(keyboard, row, col, now):
    index = row * COLS + col
    rising = keyboard._update_key(index, 1, now)
    keyboard._capture_rising((1 << index) if rising else 0)
    return rising


def _release(keyboard, row, col, now):
    return keyboard._update_key(row * COLS + col, 0, now)


def test_five_rapid_represses_are_not_lost_after_a_valid_release():
    keyboard = Keyboard()
    events = []
    now = 100

    for _ in range(5):
        assert _capture_press(keyboard, 3, 0, now)
        events.append(keyboard.pop_key_event())
        _release(keyboard, 3, 0, now + 1)
        now += DEBOUNCE_MS + 1

    assert events == [(3, 0, False)] * 5


def test_repress_waits_for_one_complete_debounce_interval():
    keyboard = Keyboard()
    assert _capture_press(keyboard, 3, 1, 100)
    assert keyboard.pop_key_event() == (3, 1, False)
    _release(keyboard, 3, 1, 101)

    assert not _capture_press(
        keyboard, 3, 1, 101 + DEBOUNCE_MS - 1)
    assert keyboard.pop_key_event() is None
    assert _capture_press(keyboard, 3, 1, 101 + DEBOUNCE_MS)
    assert keyboard.pop_key_event() == (3, 1, False)


def test_shift_state_is_captured_with_every_edge_in_the_scan():
    keyboard = Keyboard()
    shift = 4 * COLS
    one = 3 * COLS
    three = 3 * COLS + 2
    rising = 0
    for index in (shift, one, three):
        if keyboard._update_key(index, 1, 100):
            rising |= 1 << index
    keyboard._capture_rising(rising)

    assert keyboard.pop_key_event() == (3, 0, True)
    assert keyboard.pop_key_event() == (3, 2, True)
    assert keyboard.pop_key_event() == (4, 0, True)


def test_event_queue_is_bounded_without_allocating_key_objects():
    keyboard = Keyboard()
    for index in range(EVENT_QUEUE_CAPACITY + 2):
        assert keyboard._queue_event(
            index // COLS, index % COLS, False) is (
                index < EVENT_QUEUE_CAPACITY)

    assert keyboard.has_pending_events()
    assert not hasattr(keyboard, "keys")
    assert len(keyboard._release_times) == 30
    assert len(keyboard._press_starts) == 30


def test_long_press_is_consumed_once_until_release(monkeypatch):
    keyboard = Keyboard()
    assert _capture_press(keyboard, 0, 0, 100)
    monkeypatch.setattr("input.keyboard.time.ticks_ms", lambda: 1100)

    assert keyboard.consume_long_press(0, 0, 1000)
    assert not keyboard.consume_long_press(0, 0, 1000)
    _release(keyboard, 0, 0, 1101)
    assert not keyboard.consume_long_press(0, 0, 1000)


def test_key_labels_keep_numeric_and_shift_navigation_layouts():
    assert get_key_label(3, 0, False) == "1"
    assert get_key_label(3, 1, False) == "2"
    assert get_key_label(3, 1, True) == "down"


def test_key_labels_use_fixed_position_tables_and_reject_invalid_positions():
    expected_normal = (
        "ESC", "/", "*", "-", "sin", "sec",
        "7", "8", "9", "+", "cos", "csc",
        "4", "5", "6", "^", "tan", "cot",
        "1", "2", "3", "ENT", "exp", "rpn",
        "shift", "0", ".", "DEL", "ang", "tab",
    )
    expected_shift = (
        "ESC", "(", ")", "-", "asin", "sec",
        "7", "up", "9", "+", "acos", "csc",
        "left", "5", "right", "sqrt", "atan", "cot",
        "1", "down", "3", "ENT", "ln", "rpn",
        "shift", "0", ",", "DEL", "ang", "stab",
    )

    assert type(ROW_PINS) is tuple
    assert type(COL_PINS) is tuple
    assert _CALC_MAP == expected_normal
    assert _CALC_SHIFT_MAP == expected_shift
    assert len(_CALC_MAP) == ROWS * COLS == 30
    assert len(_CALC_SHIFT_MAP) == ROWS * COLS == 30
    assert tuple(
        get_key_label(row, col)
        for row in range(ROWS) for col in range(COLS)
    ) == expected_normal
    assert tuple(
        get_key_label(row, col, True)
        for row in range(ROWS) for col in range(COLS)
    ) == expected_shift
    for row, col in ((-1, 0), (ROWS, 0), (0, -1), (0, COLS), (None, 0)):
        assert get_key_label(row, col) == ""
