from input.keyboard import Keyboard, Key, RISING_EDGE


def test_press_edge_is_consumed_once_and_captures_shift():
    keyboard = Keyboard.__new__(Keyboard)
    keyboard.keys = [[Key(row, col) for col in range(6)] for row in range(5)]
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
