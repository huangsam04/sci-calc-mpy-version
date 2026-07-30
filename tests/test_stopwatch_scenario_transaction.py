import sys
import types

import pytest

import screens.stopwatch as stopwatch_module
import screens.stopwatch_scenario as stopwatch_scenario_module
from screens.stopwatch import LAP_COUNT, LAP_MAX, StopwatchScreen


def test_target_lazily_imports_stopwatch_scenario_lease(monkeypatch):
    class LazyScenarioLease:
        def __init__(self, screen):
            self.screen = screen

    lazy_module = types.ModuleType("screens.stopwatch_scenario")
    lazy_module.StopwatchScenarioLease = LazyScenarioLease
    monkeypatch.setitem(sys.modules, "screens.stopwatch_scenario", lazy_module)
    monkeypatch.setattr(stopwatch_module, "_StopwatchScenarioLease", None)
    scenario_screen = StopwatchScreen(None)

    scenario = scenario_screen.open_scenario_lease()

    assert type(scenario) is LazyScenarioLease
    assert scenario.screen is scenario_screen


def _screen_with_resident_state():
    screen = StopwatchScreen(None)
    laps = [(number, number * 100) for number in range(LAP_MAX, 0, -1)]
    screen._clock[1] = True
    screen._clock[2][0] = False
    screen._clock[2][1] = 400
    screen._clock[2][2] = 123
    screen._clock[2][3] = laps
    screen._clock[3][0] = 6
    screen._clock[3][1] = 4
    screen._clock[3][2] = LAP_MAX + 1
    screen._clock[3][3] = 17
    return screen, laps


def _resident_state(screen):
    return (
        screen._clock[1],
        screen._clock[2][0],
        screen._clock[2][1],
        screen._clock[2][2],
        screen._clock[2][3],
        screen._clock[3][0],
        screen._clock[3][1],
        screen._clock[3][2],
        screen._clock[3][3],
    )


def _presented_state(screen):
    return (
        screen._render[1][0],
        screen._render[1][1],
        screen._render[1][2],
        screen._render[1][3],
        screen._render[2][0],
        screen._render[2][1],
    )


def _set_presented_state(screen):
    screen._render[1][0] = True
    screen._render[1][1] = 123
    screen._render[1][2] = 17
    screen._render[1][3] = 6
    screen._render[2][0] = 4
    screen._render[2][1] = 2


def test_scenario_lease_rejects_oversized_lap_snapshot_before_claim():
    screen, resident_laps = _screen_with_resident_state()
    oversized_laps = resident_laps + [(LAP_MAX + 1, 0)]
    screen._clock[2][3] = oversized_laps
    resident_state = _resident_state(screen)
    _set_presented_state(screen)
    presented_state = _presented_state(screen)

    with pytest.raises(RuntimeError, match="fixed limit"):
        screen.open_scenario_lease()

    assert screen._runtime[1][1] is None
    assert screen._runtime[1][2] is None
    assert _resident_state(screen) == resident_state
    assert _presented_state(screen) == presented_state
    assert screen._clock[2][3] is oversized_laps


def test_scenario_lease_keeps_a_fixed_lap_reference_and_restores_state(
        monkeypatch):
    screen, resident_laps = _screen_with_resident_state()
    resident_state = _resident_state(screen)
    clock = [1000]

    monkeypatch.setattr(stopwatch_module.time, "ticks_ms", lambda: clock[0])

    lease = screen.open_scenario_lease()

    assert lease._saved_laps is resident_laps
    assert screen._clock[2][3] is not resident_laps
    assert screen._clock[2][3] == []
    assert screen._clock[1] is False
    assert screen._clock[2][0] is False
    with pytest.raises(RuntimeError, match="already active"):
        screen.open_scenario_lease()

    assert lease.start() is True
    for _ in range(LAP_MAX + 1):
        clock[0] += 10
        assert lease.lap() is True

    assert len(screen._clock[2][3]) == LAP_MAX
    assert screen._clock[2][3][0][0] == LAP_MAX + 1
    assert screen._clock[2][3][-1][0] == 2
    scratch_laps = screen._clock[2][3]
    assert lease.pause() is True
    assert lease.reset() is True
    assert screen._clock[2][3] is scratch_laps
    assert screen._clock[2][3] == []

    assert lease.close() is True
    assert _resident_state(screen) == resident_state
    assert screen._clock[2][3] is resident_laps
    assert lease.close() is True


def test_scenario_lease_propagates_lap_memory_error_before_scalar_commit(
        monkeypatch):
    screen, resident_laps = _screen_with_resident_state()
    resident_state = _resident_state(screen)
    clock = [1000]

    monkeypatch.setattr(stopwatch_module.time, "ticks_ms", lambda: clock[0])
    lease = screen.open_scenario_lease()
    assert lease.start() is True
    scratch_laps = screen._clock[2][3]
    scratch_state = (
        screen._clock[1],
        screen._clock[2][0],
        screen._clock[2][1],
        screen._clock[2][2],
        screen._clock[3][2],
        screen._clock[3][3],
    )
    error = MemoryError("injected lap OOM")

    def fail_ticks_diff(_left, _right):
        raise error

    monkeypatch.setattr(stopwatch_module.time, "ticks_diff", fail_ticks_diff)

    with pytest.raises(MemoryError) as caught:
        lease.lap()

    assert caught.value is error
    assert screen._clock[2][3] is scratch_laps
    assert screen._clock[2][3] == []
    assert (
        screen._clock[1],
        screen._clock[2][0],
        screen._clock[2][1],
        screen._clock[2][2],
        screen._clock[3][2],
        screen._clock[3][3],
    ) == scratch_state

    assert lease.close() is True
    assert _resident_state(screen) == resident_state
    assert screen._clock[2][3] is resident_laps


def test_scenario_lease_proves_the_full_sixty_action_lap_window(
        monkeypatch):
    screen, resident_laps = _screen_with_resident_state()
    resident_state = _resident_state(screen)
    clock = [1000]

    monkeypatch.setattr(stopwatch_module.time, "ticks_ms", lambda: clock[0])
    lease = screen.open_scenario_lease()
    actions = 0

    assert lease.start() is True
    actions += 1
    for _ in range(LAP_MAX):
        clock[0] += 10
        assert lease.lap() is True
        actions += 1

    for cursor in range(1, LAP_MAX):
        assert lease.move_lap_cursor(1) is True
        actions += 1
        assert screen._clock[3][0] == cursor

    assert screen._clock[3][1] == LAP_MAX - LAP_COUNT
    assert lease._terminal_lap_view_offset == LAP_MAX - LAP_COUNT

    for cursor in range(LAP_MAX - 2, -1, -1):
        assert lease.move_lap_cursor(-1) is True
        actions += 1
        assert screen._clock[3][0] == cursor

    assert screen._clock[3][1] == 0

    assert lease.verify_and_leave_lap_window() is True
    actions += 1
    assert actions == 60
    assert lease.lap_window_active is False
    assert lease.lap_window_verified is True
    assert screen._clock[1] is True
    assert screen._clock[3][0] == 0
    assert screen._clock[3][1] == 0

    with pytest.raises(RuntimeError, match="closed"):
        lease.move_lap_cursor(1)

    assert lease.close() is True
    assert lease.close() is True
    assert _resident_state(screen) == resident_state
    assert screen._clock[2][3] is resident_laps


def test_scenario_lease_cursor_bounds_and_failed_window_proof_leave_state_intact(
        monkeypatch):
    screen, resident_laps = _screen_with_resident_state()
    resident_state = _resident_state(screen)
    clock = [1000]

    monkeypatch.setattr(stopwatch_module.time, "ticks_ms", lambda: clock[0])
    lease = screen.open_scenario_lease()
    assert lease.start() is True
    for _ in range(LAP_MAX):
        clock[0] += 10
        assert lease.lap() is True

    before = (
        screen._clock[1], screen._clock[3][0], screen._clock[3][1],
        screen._clock[3][2], screen._clock[3][3],
    )
    assert lease.move_lap_cursor(-1) is False
    with pytest.raises(ValueError, match="direction"):
        lease.move_lap_cursor(0)
    with pytest.raises(RuntimeError, match="terminal window proof"):
        lease.verify_and_leave_lap_window()
    assert (
        screen._clock[1], screen._clock[3][0], screen._clock[3][1],
        screen._clock[3][2], screen._clock[3][3],
    ) == before
    assert lease.lap_window_active is True
    assert lease.lap_window_verified is False

    for _ in range(LAP_MAX - 1):
        assert lease.move_lap_cursor(1) is True
    assert lease.move_lap_cursor(1) is False
    assert screen._clock[3][0] == LAP_MAX - 1
    assert screen._clock[3][1] == LAP_MAX - LAP_COUNT
    assert lease._terminal_lap_view_offset == LAP_MAX - LAP_COUNT
    with pytest.raises(RuntimeError, match="cursor did not return"):
        lease.verify_and_leave_lap_window()
    for _ in range(LAP_MAX - 1):
        assert lease.move_lap_cursor(-1) is True
    assert lease.move_lap_cursor(-1) is False
    assert screen._clock[3][0] == 0
    assert screen._clock[3][1] == 0
    assert lease.verify_and_leave_lap_window() is True

    assert lease.close() is True
    assert _resident_state(screen) == resident_state
    assert screen._clock[2][3] is resident_laps


def test_scenario_lease_cursor_action_propagates_its_original_memory_error(
        monkeypatch):
    screen, resident_laps = _screen_with_resident_state()
    resident_state = _resident_state(screen)
    lease = screen.open_scenario_lease()
    primary = MemoryError("injected cursor action OOM")
    scratch_state = (
        screen._clock[1], screen._clock[2][0], screen._clock[2][1], screen._clock[2][2],
        screen._clock[2][3], screen._clock[3][0], screen._clock[3][1],
        screen._clock[3][2], screen._clock[3][3],
    )

    def exhaust_action(_lease):
        raise primary

    monkeypatch.setattr(
        stopwatch_scenario_module.StopwatchScenarioLease,
        "_screen_for_action", exhaust_action)

    with pytest.raises(MemoryError) as caught:
        lease.move_lap_cursor(1)

    assert caught.value is primary
    assert (
        screen._clock[1], screen._clock[2][0], screen._clock[2][1], screen._clock[2][2],
        screen._clock[2][3], screen._clock[3][0], screen._clock[3][1],
        screen._clock[3][2], screen._clock[3][3],
    ) == scratch_state
    assert lease.close() is True
    assert _resident_state(screen) == resident_state
    assert screen._clock[2][3] is resident_laps


def test_stopwatch_normal_cursor_navigation_is_unchanged_by_scenario_lease(
        monkeypatch):
    screen = StopwatchScreen(None)
    screen._clock[2][3] = [(number, number * 10) for number in range(LAP_MAX, 0, -1)]
    screen._clock[3][0] = 3
    screen._clock[3][1] = 0

    monkeypatch.setattr(stopwatch_module, "get_key_label", lambda *_args: "down")
    assert screen.update(None, (0, 0, False)) == "REDRAW"
    assert screen._clock[3][0] == 4

    monkeypatch.setattr(stopwatch_module, "get_key_label", lambda *_args: "up")
    assert screen.update(None, (0, 0, False)) == "REDRAW"
    assert screen._clock[3][0] == 3
