from input.keyboard import Keyboard, Key, RISING_EDGE


def _keyboard_without_pins():
    keyboard = Keyboard.__new__(Keyboard)
    keyboard.keys = [[Key(row, col) for col in range(6)] for row in range(5)]
    keyboard._event_data = bytearray(8)
    keyboard._event_head = 0
    keyboard._event_count = 0
    return keyboard


def test_press_edge_is_consumed_once_and_captures_shift():
    keyboard = _keyboard_without_pins()
    keyboard.keys[4][0].is_pressed = True
    key = keyboard.keys[1][2]
    key.is_pressed = True
    key.state = RISING_EDGE

    assert keyboard.pop_key_event() == (1, 2, True)
    assert keyboard.pop_key_event() is None


def test_long_press_is_latched_until_release():
    key = Key(0, 0)
    key.update(True, 100)

    assert key.consume_long_press(1200, 1000) is True
    assert key.consume_long_press(1300, 1000) is False
    key.update(False, 1400)
    key.update(True, 1500)
    assert key.consume_long_press(2600, 1000) is True


def test_first_press_is_accepted_at_any_ticks_wrap_position(monkeypatch):
    def wrapped_diff(left, right):
        return ((left - right + 512) % 1024) - 512

    monkeypatch.setattr("input.keyboard.time.ticks_diff", wrapped_diff)
    key = Key(0, 0)

    key.update(True, 700)

    assert key.is_pressed is True
    assert key.state == RISING_EDGE


def test_edges_that_arrive_in_one_slow_frame_are_not_lost():
    """A display upload may span two presses; both edges must survive."""
    keyboard = _keyboard_without_pins()
    first = keyboard.keys[3][0]
    second = keyboard.keys[3][2]

    assert first.update(True, 100) is True
    assert second.update(True, 100) is True
    keyboard._capture_edges()

    assert keyboard.pop_key_event() == (3, 0, False)

    # The next matrix scan turns both physical states into PRESSED before the
    # main loop asks for another event. The second edge still has to be queued.
    first.update(True, 115)
    second.update(True, 115)
    assert keyboard.pop_key_event() == (3, 2, False)


def test_matching_escape_can_bypass_without_discarding_direction_edges():
    keyboard = _keyboard_without_pins()
    assert keyboard._queue_event(1, 1, False) is True
    assert keyboard._queue_event(0, 0, False) is True
    assert keyboard._queue_event(3, 1, False) is True

    assert keyboard.pop_key_event_at(0, 0) == (0, 0, False)
    assert keyboard.pop_key_event() == (1, 1, False)
    assert keyboard.pop_key_event() == (3, 1, False)
    assert keyboard.pop_key_event() is None


def test_matching_event_removal_preserves_wrapped_ring_order():
    keyboard = _keyboard_without_pins()
    for col in range(6):
        assert keyboard._queue_event(0, col, False) is True
    for col in range(5):
        assert keyboard.pop_key_event() == (0, col, False)
    for row, col in ((1, 1), (2, 1), (3, 1), (4, 1)):
        assert keyboard._queue_event(row, col, False) is True

    assert keyboard.pop_key_event_at(3, 1) == (3, 1, False)
    assert keyboard.pop_key_event() == (0, 5, False)
    assert keyboard.pop_key_event() == (1, 1, False)
    assert keyboard.pop_key_event() == (2, 1, False)
    assert keyboard.pop_key_event() == (4, 1, False)
