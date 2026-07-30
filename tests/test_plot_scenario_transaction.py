import sys
import types

import pytest

from calc.functions import build_registry
from screens import plot as plot_module
from screens.plot import MAX_PLOT_EXPRESSION_CHARS, PlotScreen
from screens.plot_scenario import (
    PLOT_SCENARIO_PROBE_ORDINARY_ERROR,
    PLOT_SCENARIO_PROBE_VALID,
    PLOT_SCENARIO_RESULT_COMPLETE,
    PLOT_SCENARIO_RESULT_MEMORY_ERROR,
    PLOT_SCENARIO_RESULT_NONE,
    PLOT_SCENARIO_RESULT_ORDINARY_ERROR,
    PLOT_SCENARIO_STATUS_RUNNING,
    PLOT_SCENARIO_STATUS_TERMINAL,
)
from ui.element import SETTLE_MORE, SETTLE_REDRAW
from ui.memory import MemoryManager


def _screen(memory=None):
    screen = PlotScreen(None, registry=build_registry(), memory=memory)
    screen.expr = "x^2"
    screen._state[0][0] = -7.0
    screen._state[0][1] = 9.0
    screen._state[0][2] = -3.0
    screen._state[0][3] = 11.0
    screen.input_box.set_str("x^2")
    screen.input_box.cursor_pos = 2
    screen.input_box.view_offset = 1
    screen.input_box.cursor.x = 23
    screen.input_box.cursor.y = 4
    screen.input_box.cursor.mode = 1
    screen.input_box.cursor.is_visible = True
    screen._state[3][3][0][2] = "x+1"
    return screen


def _curve_job(phase=1):
    job = plot_module._CurveJob(False, 1, 1, 1)
    job.phase = phase
    return job


def _finish_probe(transaction):
    for _ in range(200):
        settled = transaction.step()
        if transaction.terminal:
            return settled
    raise AssertionError("bounded Plot probe did not reach a terminal state")


def test_target_lazily_imports_plot_scenario_transaction(monkeypatch):
    class LazyScenarioTransaction:
        def __init__(self, screen):
            self.screen = screen

    lazy_module = types.ModuleType("screens.plot_scenario")
    lazy_module.PlotScenarioTransaction = LazyScenarioTransaction
    monkeypatch.setitem(sys.modules, "screens.plot_scenario", lazy_module)
    monkeypatch.setattr(plot_module, "PlotScenarioTransaction", None)
    scenario_screen = _screen()

    scenario = scenario_screen.open_scenario_transaction()

    assert type(scenario) is LazyScenarioTransaction
    assert scenario.screen is scenario_screen


def test_scenario_transaction_releases_derived_curve_runtime_without_snapshot():
    memory = MemoryManager()
    screen = _screen(memory)
    workspace = memory.reserve_plot_workspace(screen.height)
    old_program = object()
    old_job = _curve_job()
    old_framebuffer = object()
    screen._state[2][1] = workspace
    screen._state[2][0] = old_framebuffer
    screen._state[3][1] = old_job
    screen._state[3][3][1][2] = old_program
    screen._state[3][3][1][3] = screen.expr

    transaction = screen.open_scenario_transaction()

    assert screen._state[2][1] is None
    assert screen._state[2][0] is None
    assert screen._state[3][1] is None
    assert screen._state[3][3][1][2] is None
    assert memory.get_plot_workspace() is workspace
    assert not hasattr(transaction, "_curve_buf")
    assert not hasattr(transaction, "_curve_fb")
    assert not hasattr(transaction, "_curve_job")
    assert not hasattr(transaction, "_program")

    assert transaction.close() is True
    assert screen._state[2][1] is None
    assert screen._state[2][0] is None
    assert screen._state[3][1] is None
    assert screen._state[3][3][1][2] is None
    assert memory.get_plot_workspace() is workspace
    assert screen._state[2][2] is True


def test_scenario_transaction_restores_user_intent_and_editor_scalars_lazily():
    screen = _screen()
    expected = (
        screen.expr,
        screen._state[0][0],
        screen._state[0][1],
        screen._state[0][2],
        screen._state[0][3],
        screen._state[1][3],
        screen._state[3][3][0][3],
        screen._state[3][3][0][2],
        screen.input_box.str,
        screen.input_box.cursor_pos,
        screen.input_box.view_offset,
        screen.input_box.cursor.x,
        screen.input_box.cursor.y,
        screen.input_box.cursor.mode,
        screen.input_box.cursor.is_visible,
    )

    transaction = screen.open_scenario_transaction()
    screen.expr = "x+1"
    screen._state[0][0] = -1.0
    screen._state[0][1] = 1.0
    screen._state[0][2] = -2.0
    screen._state[0][3] = 2.0
    screen._state[1][3] = 1
    screen._state[3][3][0][3] = 0
    screen._state[3][3][0][2] = "x+1"
    screen.input_box.set_str("x+1")
    screen.input_box.cursor_pos = 3
    screen.input_box.view_offset = 0
    screen.input_box.cursor.x = 41
    screen.input_box.cursor.y = 8
    screen.input_box.cursor.mode = 2
    screen.input_box.cursor.is_visible = False
    screen._state[2][1] = bytearray(1)
    screen._state[2][0] = object()
    screen._state[3][1] = _curve_job()
    screen._state[3][3][1][2] = object()

    assert transaction.close() is True
    assert (
        screen.expr,
        screen._state[0][0],
        screen._state[0][1],
        screen._state[0][2],
        screen._state[0][3],
        screen._state[1][3],
        screen._state[3][3][0][3],
        screen._state[3][3][0][2],
        screen.input_box.str,
        screen.input_box.cursor_pos,
        screen.input_box.view_offset,
        screen.input_box.cursor.x,
        screen.input_box.cursor.y,
        screen.input_box.cursor.mode,
        screen.input_box.cursor.is_visible,
    ) == expected
    assert screen._state[2][1] is None
    assert screen._state[2][0] is None
    assert screen._state[3][1] is None
    assert screen._state[3][3][1][2] is None
    assert screen._state[2][2] is True
    assert screen._state[2][3] is False


def test_scenario_transaction_steps_only_one_existing_curve_phase(monkeypatch):
    screen = _screen()
    started = []
    advanced = []

    def begin(active_screen, auto_scale):
        started.append(auto_scale)
        active_screen._state[3][1] = _curve_job()
        active_screen._state[3][2] = 1
        return True

    def advance(_screen):
        advanced.append(True)
        return 0

    monkeypatch.setattr(PlotScreen, "_begin_curve_job", begin)
    monkeypatch.setattr(PlotScreen, "_advance_curve_job", advance)
    transaction = screen.open_scenario_transaction()
    assert transaction.start_probe(PLOT_SCENARIO_PROBE_VALID) is True

    assert transaction.step() == SETTLE_MORE
    assert started == [True]
    assert advanced == []
    assert transaction.step() == SETTLE_MORE
    assert started == [True]
    assert advanced == [True]
    assert transaction.close() is True


@pytest.mark.parametrize("stage", ("begin", "advance"))
def test_scenario_transaction_propagates_curve_memory_error_and_restores(
        monkeypatch, stage):
    screen = _screen()
    expected_expr = screen.expr
    expected_range = (screen._state[0][0], screen._state[0][1], screen._state[0][2], screen._state[0][3])
    primary = MemoryError("injected plot OOM")
    transaction = screen.open_scenario_transaction()
    assert transaction.start_probe(PLOT_SCENARIO_PROBE_VALID) is True

    if stage == "begin":
        def fail_begin(_screen, _auto_scale):
            raise primary

        monkeypatch.setattr(PlotScreen, "_begin_curve_job", fail_begin)
    else:
        screen._state[2][2] = False
        screen._state[3][1] = _curve_job()

        def fail_advance(_screen):
            raise primary

        monkeypatch.setattr(PlotScreen, "_advance_curve_job", fail_advance)

    with pytest.raises(MemoryError) as caught:
        transaction.step()

    assert caught.value is primary
    assert transaction.status == PLOT_SCENARIO_STATUS_TERMINAL
    assert transaction.result == PLOT_SCENARIO_RESULT_MEMORY_ERROR
    assert transaction.close() is True
    assert screen.expr == expected_expr
    assert (screen._state[0][0], screen._state[0][1], screen._state[0][2], screen._state[0][3]) == expected_range
    assert screen._state[2][2] is True


def test_scenario_transaction_rejects_idle_step_without_settling_user_curve(
        monkeypatch):
    screen = _screen()
    transaction = screen.open_scenario_transaction()
    calls = []

    def settle_user_curve(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("an IDLE scenario must not settle the user curve")

    monkeypatch.setattr(PlotScreen, "_settle_curve_step", settle_user_curve)

    with pytest.raises(RuntimeError, match="not running"):
        transaction.step()

    assert calls == []
    assert transaction.status != PLOT_SCENARIO_STATUS_TERMINAL
    assert transaction.result == PLOT_SCENARIO_RESULT_NONE
    assert transaction.close() is True


def test_scenario_transaction_rejects_concurrency_and_closed_steps():
    screen = _screen()
    transaction = screen.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="already active"):
        screen.open_scenario_transaction()

    assert transaction.close() is True
    assert transaction.close() is True
    with pytest.raises(RuntimeError, match="closed"):
        transaction.step()

    next_transaction = screen.open_scenario_transaction()
    assert next_transaction.close() is True


def test_controlled_valid_probe_uses_fixed_scalars_and_reports_completion():
    screen = _screen()
    expected = (
        screen.expr, screen._state[0][0], screen._state[0][1], screen._state[0][2], screen._state[0][3],
        screen._state[1][3], screen._state[3][3][0][3])
    transaction = screen.open_scenario_transaction()

    assert transaction.start_probe(PLOT_SCENARIO_PROBE_VALID) is True
    assert transaction.status == PLOT_SCENARIO_STATUS_RUNNING
    assert transaction.result == PLOT_SCENARIO_RESULT_NONE
    assert (screen.expr, screen._state[0][0], screen._state[0][1]) == ("x^2", -2.0, 2.0)
    assert screen._state[2][2] is True
    assert screen._state[2][3] is True

    assert transaction.step() == SETTLE_MORE
    assert transaction.terminal is False
    terminal_settle = _finish_probe(transaction)

    assert terminal_settle == SETTLE_REDRAW
    assert transaction.status == PLOT_SCENARIO_STATUS_TERMINAL
    assert transaction.result == PLOT_SCENARIO_RESULT_COMPLETE
    assert screen._state[1][3] == 0
    assert screen._state[3][1] is None
    assert screen._state[2][0] is not None
    assert screen._state[2][1] is not None

    assert transaction.close() is True
    assert (
        screen.expr, screen._state[0][0], screen._state[0][1], screen._state[0][2], screen._state[0][3],
        screen._state[1][3], screen._state[3][3][0][3]) == expected
    assert screen._state[2][0] is None
    assert screen._state[2][1] is None


def test_controlled_probe_does_not_accept_a_redraw_without_a_curve(
        monkeypatch):
    screen = _screen()
    transaction = screen.open_scenario_transaction()
    assert transaction.start_probe(PLOT_SCENARIO_PROBE_VALID) is True

    def fake_redraw(active_screen, propagate_memory=False):
        assert propagate_memory is True
        active_screen._state[2][2] = False
        active_screen._state[2][3] = False
        return SETTLE_REDRAW

    monkeypatch.setattr(PlotScreen, "_settle_curve_step", fake_redraw)

    assert transaction.step() == SETTLE_REDRAW
    assert transaction.status == PLOT_SCENARIO_STATUS_RUNNING
    assert transaction.result == PLOT_SCENARIO_RESULT_NONE
    assert transaction.terminal is False
    assert screen._state[2][1] is None
    assert screen._state[2][0] is None
    assert transaction.close() is True


def test_controlled_ordinary_error_probe_has_a_distinct_terminal_result():
    screen = _screen()
    transaction = screen.open_scenario_transaction()

    assert transaction.start_probe(PLOT_SCENARIO_PROBE_ORDINARY_ERROR) is True
    assert transaction.step() == SETTLE_REDRAW

    assert transaction.terminal is True
    assert transaction.status == PLOT_SCENARIO_STATUS_TERMINAL
    assert transaction.result == PLOT_SCENARIO_RESULT_ORDINARY_ERROR
    assert screen._state[1][3] == 2
    assert screen.error_popup.active is True
    with pytest.raises(RuntimeError, match="terminal"):
        transaction.step()

    # An ordinary probe error is a result, not a poisoned lease: the
    # controller may choose another fixed probe before it eventually closes.
    assert transaction.start_probe(PLOT_SCENARIO_PROBE_VALID) is True
    assert transaction.status == PLOT_SCENARIO_STATUS_RUNNING
    assert transaction.result == PLOT_SCENARIO_RESULT_NONE
    assert transaction.close() is True


def test_controlled_probe_records_and_reraises_the_exact_memory_error(
        monkeypatch):
    screen = _screen()
    transaction = screen.open_scenario_transaction()
    primary = MemoryError("controlled probe OOM")

    def fail_begin(_screen, _auto_scale):
        raise primary

    monkeypatch.setattr(PlotScreen, "_begin_curve_job", fail_begin)
    assert transaction.start_probe(PLOT_SCENARIO_PROBE_VALID) is True

    with pytest.raises(MemoryError) as caught:
        transaction.step()

    assert caught.value is primary
    assert transaction.terminal is True
    assert transaction.result == PLOT_SCENARIO_RESULT_MEMORY_ERROR
    with pytest.raises(RuntimeError, match="must close"):
        transaction.start_probe(PLOT_SCENARIO_PROBE_ORDINARY_ERROR)
    assert transaction.close() is True


def test_controlled_probe_close_failure_keeps_the_checkpoint_retryable(
        monkeypatch):
    screen = _screen()
    expected = (
        screen.expr, screen._state[0][0], screen._state[0][1], screen._state[0][2], screen._state[0][3],
        screen._state[1][3], screen._state[3][3][0][3], screen.input_box.str)
    transaction = screen.open_scenario_transaction()
    assert transaction.start_probe(PLOT_SCENARIO_PROBE_VALID) is True
    clear_presented = PlotScreen._clear_presented_editor_state
    failures = []

    def fail_once(active_screen):
        failures.append(True)
        if len(failures) == 1:
            raise RuntimeError("injected Plot cleanup failure")
        return clear_presented(active_screen)

    monkeypatch.setattr(
        PlotScreen, "_clear_presented_editor_state", fail_once)

    with pytest.raises(RuntimeError, match="cleanup failure"):
        transaction.close()

    assert screen._state[1][2] is transaction
    assert (
        screen.expr, screen._state[0][0], screen._state[0][1], screen._state[0][2], screen._state[0][3],
        screen._state[1][3], screen._state[3][3][0][3], screen.input_box.str) == expected
    assert transaction.close() is True
    assert len(failures) == 2
    assert screen._state[1][2] is None


def test_scenario_close_with_primary_preserves_memory_error_and_retries(
        monkeypatch):
    screen = _screen()
    transaction = screen.open_scenario_transaction()
    primary = MemoryError("injected Plot action OOM")
    cleanup = MemoryError("injected Plot restore OOM")
    clear_presented = PlotScreen._clear_presented_editor_state
    restore_attempts = []

    def fail_first_restore(active_screen):
        restore_attempts.append(True)
        if len(restore_attempts) == 1:
            raise cleanup
        return clear_presented(active_screen)

    monkeypatch.setattr(
        PlotScreen, "_clear_presented_editor_state", fail_first_restore)

    with pytest.raises(MemoryError) as first_close:
        transaction.close_with_primary(primary)

    assert first_close.value is primary
    assert transaction._closed is False
    assert transaction._screen is screen
    assert screen._state[1][2] is transaction

    with pytest.raises(MemoryError) as retry_close:
        transaction.close_with_primary(primary)

    assert retry_close.value is primary
    assert restore_attempts == [True, True]
    assert transaction._closed is True
    assert screen._state[1][2] is None


def test_scenario_close_with_primary_promotes_cleanup_memory_error(
        monkeypatch):
    screen = _screen()
    transaction = screen.open_scenario_transaction()
    primary = RuntimeError("injected Plot action error")
    cleanup = MemoryError("injected Plot restore OOM")
    clear_presented = PlotScreen._clear_presented_editor_state
    restore_attempts = []

    def fail_first_restore(active_screen):
        restore_attempts.append(True)
        if len(restore_attempts) == 1:
            raise cleanup
        return clear_presented(active_screen)

    monkeypatch.setattr(
        PlotScreen, "_clear_presented_editor_state", fail_first_restore)

    with pytest.raises(MemoryError) as first_close:
        transaction.close_with_primary(primary)

    assert first_close.value is cleanup
    assert transaction._closed is False
    assert transaction._screen is screen
    assert screen._state[1][2] is transaction
    assert transaction.close() is True
    assert restore_attempts == [True, True]
    assert screen._state[1][2] is None


@pytest.mark.parametrize(
    "field",
    ("expr", "edit_original", "input_str", "popup_expr", "popup_title",
     "popup_detail"),
)
def test_scenario_transaction_rejects_oversized_text_before_claim_or_release(
        field):
    screen = _screen()
    popup = screen.error_popup
    popup.show_static("Saved title", "Saved detail")
    popup.expr = "saved expression"
    screen._state[1][3] = 2
    curve_buf = bytearray(1)
    curve_fb = object()
    curve_job = _curve_job()
    program = object()
    screen._state[2][1] = curve_buf
    screen._state[2][0] = curve_fb
    screen._state[3][1] = curve_job
    screen._state[3][3][1][2] = program
    screen._state[3][3][1][3] = screen.expr
    too_large = "x" * (MAX_PLOT_EXPRESSION_CHARS + 1)

    if field == "expr":
        screen.expr = too_large
    elif field == "edit_original":
        screen._state[3][3][0][2] = too_large
    elif field == "input_str":
        screen.input_box.str = too_large
    else:
        setattr(popup, field[6:], too_large)

    screen_state = (
        screen.expr, screen._state[3][3][0][2], screen.input_box.str, screen._state[1][3],
        screen._state[2][2], screen._state[2][3],
    )
    popup_state = (
        popup.expr, popup._state[2], popup.title, popup.detail,
        popup._state[3], popup.active,
    )

    with pytest.raises(RuntimeError, match="text snapshot"):
        screen.open_scenario_transaction()

    assert screen._state[1][2] is None
    assert (
        screen.expr, screen._state[3][3][0][2], screen.input_box.str, screen._state[1][3],
        screen._state[2][2], screen._state[2][3],
    ) == screen_state
    assert (
        popup.expr, popup._state[2], popup.title, popup.detail,
        popup._state[3], popup.active,
    ) == popup_state
    assert screen._state[2][1] is curve_buf
    assert screen._state[2][0] is curve_fb
    assert screen._state[3][1] is curve_job
    assert screen._state[3][3][1][2] is program


def test_normal_settle_step_keeps_its_existing_memory_error_ui_recovery(
        monkeypatch):
    screen = _screen()
    primary = MemoryError("ordinary settle OOM")
    screen._state[2][2] = True
    screen._state[2][3] = True

    def fail_begin(_screen, _auto_scale):
        raise primary

    monkeypatch.setattr(PlotScreen, "_begin_curve_job", fail_begin)

    assert screen.settle_step() == SETTLE_REDRAW
    assert screen._state[1][3] == 2
    assert screen.error_popup.title == "Graph paused"
