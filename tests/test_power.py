from utils.power import AWAKE, LOCKED, SLEEPING, WOKE, DisplayPower


class DisplayStub:
    def __init__(self):
        self.sleeps = 0
        self.wakes = 0

    def sleep(self):
        self.sleeps += 1

    def wake(self):
        self.wakes += 1


def test_idle_display_sleeps_and_wake_key_is_locked_until_release():
    display = DisplayStub()
    power = DisplayPower(display, timeout_ms=1_000, now=100)

    assert power.update(1_099, False) == AWAKE
    assert power.update(1_100, False) == SLEEPING
    assert display.sleeps == 1

    assert power.update(1_200, True) == WOKE
    assert display.wakes == 1
    assert power.update(1_220, True) == LOCKED
    assert power.update(1_240, False) == AWAKE


def test_reset_wakes_display_and_clears_wake_lock():
    display = DisplayStub()
    power = DisplayPower(display, timeout_ms=10, now=0)
    power.update(10, False)

    power.reset(20)

    assert display.wakes == 1
    assert power.sleeping is False
    assert power.update(21, False) == AWAKE
