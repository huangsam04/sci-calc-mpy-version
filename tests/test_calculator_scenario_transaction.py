import sys
import types

import pytest

from calc.functions import build_registry
from screens import calculator as calculator_module
from screens import calculator_scenario as calculator_scenario_module
from screens.calculator import CalculatorScreen
from screens.calculator_scenario import (
    CALCULATOR_SCENARIO_ERROR_DISMISS,
    CALCULATOR_SCENARIO_ERROR_KIND,
    CALCULATOR_SCENARIO_ERROR_KIND_COUNT,
    CALCULATOR_SCENARIO_ERROR_SHOW,
    CALCULATOR_SCENARIO_HISTORY,
    CALCULATOR_SCENARIO_HISTORY_CURSOR,
    CALCULATOR_SCENARIO_HISTORY_CURSOR_FORWARD,
    CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE,
    CalculatorScenarioTransaction,
)


def _screen():
    return CalculatorScreen(None, registry=build_registry(), variables={})


def _assert_closed_transaction_releases_snapshots(transaction):
    assert transaction._closed is True
    assert transaction._screen is None
    assert transaction._history_owner is None
    assert transaction._history is None
    assert transaction._input_str is None
    assert transaction._storage_error is None
    assert transaction._popup_expr is None
    assert transaction._popup_title is None
    assert transaction._popup_detail is None
    assert transaction.error_diagnostic_proof is None


def test_target_lazily_imports_heavy_scenario_transaction(monkeypatch):
    class LazyTransaction:
        def __init__(self, screen):
            self.screen = screen

    lazy_module = types.ModuleType("screens.calculator_scenario")
    lazy_module.CalculatorScenarioTransaction = LazyTransaction
    monkeypatch.setitem(
        sys.modules, "screens.calculator_scenario", lazy_module)
    screen = _screen()

    transaction = screen.open_scenario_transaction()

    assert type(transaction) is LazyTransaction
    assert transaction.screen is screen
    assert screen._state[3][3] is transaction


def test_calculator_scenario_transaction_restores_history_input_and_popup():
    screen = _screen()
    original_history = [("saved", 7.0)]
    screen._state[0] = original_history
    screen.input_box.set_str("1+", immediate=True)
    screen.input_box.move_cursor_end()
    screen._state[1].show_static("Saved error", "Saved detail")
    screen._state[1].expr = "saved expression"
    screen._state[1]._state[2] = 3
    screen.mode = 2

    popup_state = (
        screen._state[1].expr,
        screen._state[1]._state[2],
        screen._state[1].title,
        screen._state[1].detail,
        screen._state[1]._state[3],
        screen._state[1].active,
    )
    input_state = (
        screen.input_box.str,
        screen.input_box.cursor_pos,
        screen.input_box.view_offset,
        screen.input_box.cursor.x,
        screen.input_box.cursor.y,
        screen.input_box.cursor.width,
        screen.input_box.cursor.height,
        screen.input_box.cursor.mode,
        screen.input_box.cursor.is_visible,
        screen.input_box.cursor.gs,
    )

    transaction = screen.open_scenario_transaction()

    assert transaction.step(CALCULATOR_SCENARIO_HISTORY, "1+1") is True
    assert transaction.step(CALCULATOR_SCENARIO_ERROR_SHOW, "1/") is True
    assert transaction.step(CALCULATOR_SCENARIO_ERROR_DISMISS) is True
    assert transaction.close() is True
    assert transaction.close() is True

    assert screen._state[0] is original_history
    assert screen._state[0] == [("saved", 7.0)]
    assert (
        screen.input_box.str,
        screen.input_box.cursor_pos,
        screen.input_box.view_offset,
        screen.input_box.cursor.x,
        screen.input_box.cursor.y,
        screen.input_box.cursor.width,
        screen.input_box.cursor.height,
        screen.input_box.cursor.mode,
        screen.input_box.cursor.is_visible,
        screen.input_box.cursor.gs,
    ) == input_state
    assert screen.mode == 2
    assert (
        screen._state[1].expr,
        screen._state[1]._state[2],
        screen._state[1].title,
        screen._state[1].detail,
        screen._state[1]._state[3],
        screen._state[1].active,
    ) == popup_state


def test_calculator_scenario_transaction_is_exclusive_and_history_is_bounded():
    screen = _screen()
    transaction = screen.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="already open"):
        screen.open_scenario_transaction()

    for _ in range(20):
        assert transaction.step(CALCULATOR_SCENARIO_HISTORY, "1+1") is True

    assert transaction.history_steps == 20
    assert len(screen._state[0]) == 20
    history_before_limit = screen._state[0][:]
    input_before_limit = screen.input_box.get_str()

    with pytest.raises(RuntimeError, match="history limit"):
        transaction.step(CALCULATOR_SCENARIO_HISTORY, "1+1")

    assert screen._state[0] == history_before_limit
    assert screen.input_box.get_str() == input_before_limit
    assert transaction.close() is True


def test_calculator_scenario_transaction_traverses_all_history_positions():
    screen = _screen()
    transaction = screen.open_scenario_transaction()

    for index in range(20):
        prefix = str(index) + "+"
        width = 46 if index == 19 else 38
        expression = prefix + "9" * (width - len(prefix))
        assert transaction.step(CALCULATOR_SCENARIO_HISTORY, expression) is True

    assert transaction.history_steps == 20
    assert len(screen._state[0]) == 20

    with pytest.raises(ValueError, match="cursor direction"):
        transaction.step(CALCULATOR_SCENARIO_HISTORY_CURSOR, 0)

    for expected in range(19, -1, -1):
        assert transaction.step(
            CALCULATOR_SCENARIO_HISTORY_CURSOR,
            CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE) is True
        assert transaction.history_cursor == expected
        assert screen._state[3][1] == expected

    assert transaction.history_reverse_steps == 20
    assert transaction.history_forward_steps == 0

    for expected in range(20):
        assert transaction.step(
            CALCULATOR_SCENARIO_HISTORY_CURSOR,
            CALCULATOR_SCENARIO_HISTORY_CURSOR_FORWARD) is True
        assert transaction.history_cursor == expected
        assert screen._state[3][1] == expected

    assert transaction.history_forward_steps == 20
    with pytest.raises(RuntimeError, match="forward traversal limit"):
        transaction.step(
            CALCULATOR_SCENARIO_HISTORY_CURSOR,
            CALCULATOR_SCENARIO_HISTORY_CURSOR_FORWARD)

    assert transaction.close() is True


def test_calculator_scenario_transaction_exercises_each_canonical_error_proof():
    screen = _screen()
    transaction = screen.open_scenario_transaction()
    popup_diagnostics = set()
    diagnostic_proofs = set()
    expected_diagnostics = set()

    for kind in range(CALCULATOR_SCENARIO_ERROR_KIND_COUNT):
        title_index, detail_index, position = (
            calculator_scenario_module._CALCULATOR_SCENARIO_ERROR_DIAGNOSTICS[kind])
        expected_diagnostics.add((
            calculator_scenario_module._CALCULATOR_SCENARIO_DIAGNOSTIC_TITLES[title_index],
            calculator_scenario_module._CALCULATOR_SCENARIO_DIAGNOSTIC_DETAILS[detail_index],
            position,
        ))
        assert transaction.step(CALCULATOR_SCENARIO_ERROR_KIND, kind) is True
        assert transaction.error_kind == kind
        assert screen.mode == 2
        assert screen._state[1].active is True
        assert screen._state[1].expr == (
            calculator_scenario_module._CALCULATOR_SCENARIO_ERROR_SOURCES[kind])
        assert transaction.error_diagnostic_proof == (
            (title_index << 12) | (detail_index << 7) | position)
        popup_diagnostics.add((
            screen._state[1].title,
            screen._state[1].detail,
            screen._state[1]._state[2],
        ))
        diagnostic_proofs.add(transaction.error_diagnostic_proof)
        assert transaction.step(CALCULATOR_SCENARIO_ERROR_DISMISS) is True
        assert transaction.error_kind is None
        assert transaction.error_diagnostic_proof is None

    assert transaction.error_steps == CALCULATOR_SCENARIO_ERROR_KIND_COUNT
    assert len(expected_diagnostics) == CALCULATOR_SCENARIO_ERROR_KIND_COUNT
    assert popup_diagnostics == expected_diagnostics
    assert len(diagnostic_proofs) == CALCULATOR_SCENARIO_ERROR_KIND_COUNT
    assert transaction.error_kind_mask == (
        (1 << CALCULATOR_SCENARIO_ERROR_KIND_COUNT) - 1)

    with pytest.raises(RuntimeError, match="already shown"):
        transaction.step(CALCULATOR_SCENARIO_ERROR_KIND, 0)

    assert transaction.close() is True


def test_calculator_scenario_error_kind_rejects_source_only_popup_proof(
        monkeypatch):
    screen = _screen()
    transaction = screen.open_scenario_transaction()

    def wrong_diagnostic(_expression, _context):
        raise ValueError("wrong diagnostic")

    monkeypatch.setattr(calculator_module, "evaluate", wrong_diagnostic)

    with pytest.raises(RuntimeError, match="diagnostic proof"):
        transaction.step(CALCULATOR_SCENARIO_ERROR_KIND, 0)

    assert screen.mode == 2
    assert screen._state[1].active is True
    assert screen._state[1].expr == "."
    assert transaction.error_steps == 0
    assert transaction.error_kind_mask == 0
    assert transaction.error_diagnostic_proof is None
    assert transaction.close() is True


def test_calculator_scenario_transaction_propagates_original_memory_error(
        monkeypatch):
    screen = _screen()
    screen._state[0] = [("saved", 7.0)]
    screen.input_box.set_str("saved input", immediate=True)
    screen.input_box.move_cursor_end()
    original_history = screen._state[0]
    original_error = MemoryError("injected")

    def exhaust_heap(_expression, _context):
        raise original_error

    monkeypatch.setattr(calculator_module, "evaluate", exhaust_heap)
    transaction = screen.open_scenario_transaction()

    with pytest.raises(MemoryError) as raised:
        transaction.step(CALCULATOR_SCENARIO_HISTORY, "1+1")

    assert raised.value is original_error
    assert screen._state[0] == [("saved", 7.0)]
    assert transaction.close() is True
    assert screen._state[0] is original_history
    assert screen._state[0] == [("saved", 7.0)]
    assert screen.input_box.get_str() == "saved input"
    assert screen.mode == 0
    assert screen._state[1].active is False


def test_calculator_scenario_close_with_primary_preserves_step_oom_and_retries(
        monkeypatch):
    screen = _screen()
    original_history = [("saved", 7.0)]
    screen._state[0] = original_history
    screen.input_box.set_str("saved input", immediate=True)
    screen.input_box.move_cursor_end()
    primary = MemoryError("injected step OOM")
    cleanup = MemoryError("injected restore OOM")

    def exhaust_heap(_expression, _context):
        raise primary

    monkeypatch.setattr(calculator_module, "evaluate", exhaust_heap)
    transaction = screen.open_scenario_transaction()

    with pytest.raises(MemoryError) as caught_step:
        transaction.step(CALCULATOR_SCENARIO_HISTORY, "1+1")

    assert caught_step.value is primary
    original_restore_popup = CalculatorScenarioTransaction._restore_popup
    restore_attempts = [0]

    def fail_first_restore(current, current_screen):
        restore_attempts[0] += 1
        if restore_attempts[0] == 1:
            raise cleanup
        return original_restore_popup(current, current_screen)

    monkeypatch.setattr(
        CalculatorScenarioTransaction, "_restore_popup", fail_first_restore)

    with pytest.raises(MemoryError) as caught_cleanup:
        transaction.close_with_primary(primary)

    assert caught_cleanup.value is primary
    assert transaction._closed is False
    assert screen._state[3][3] is transaction
    assert transaction._history_owner is original_history
    assert transaction._history is not None

    with pytest.raises(MemoryError) as caught_retry:
        transaction.close_with_primary(primary)

    assert caught_retry.value is primary
    assert restore_attempts == [2]
    assert screen._state[3][3] is None
    assert screen._state[0] is original_history
    assert screen._state[0] == [("saved", 7.0)]
    _assert_closed_transaction_releases_snapshots(transaction)


def test_calculator_scenario_close_with_primary_upgrades_ordinary_primary_to_oom(
        monkeypatch):
    screen = _screen()
    cleanup = MemoryError("injected restore OOM")

    def fail_normally(_expression, _context):
        raise ValueError("injected ordinary failure")

    monkeypatch.setattr(calculator_module, "evaluate", fail_normally)
    transaction = screen.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="history action failed") as caught_step:
        transaction.step(CALCULATOR_SCENARIO_HISTORY, "1+1")

    primary = caught_step.value
    original_restore_popup = CalculatorScenarioTransaction._restore_popup
    restore_attempts = [0]

    def fail_first_restore(current, current_screen):
        restore_attempts[0] += 1
        if restore_attempts[0] == 1:
            raise cleanup
        return original_restore_popup(current, current_screen)

    monkeypatch.setattr(
        CalculatorScenarioTransaction, "_restore_popup", fail_first_restore)

    with pytest.raises(MemoryError) as caught_cleanup:
        transaction.close_with_primary(primary)

    assert caught_cleanup.value is cleanup
    assert transaction._closed is False
    assert screen._state[3][3] is transaction
    assert transaction.close() is True
    assert restore_attempts == [2]
    assert screen._state[3][3] is None
    _assert_closed_transaction_releases_snapshots(transaction)


def test_calculator_scenario_transaction_rolls_back_after_ordinary_failure(
        monkeypatch):
    screen = _screen()
    original_history = [("saved", 7.0)]
    screen._state[0] = original_history
    screen.input_box.set_str("saved input", immediate=True)
    screen.input_box.move_cursor_end()

    def fail_normally(_expression, _context):
        raise ValueError("injected ordinary failure")

    monkeypatch.setattr(calculator_module, "evaluate", fail_normally)
    transaction = screen.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="history action failed"):
        transaction.step(CALCULATOR_SCENARIO_HISTORY, "1+1")

    assert screen._state[0] is original_history
    assert screen._state[0] == [("saved", 7.0)]
    assert screen.mode == 2
    assert screen._state[1].active is True
    assert transaction.close() is True
    assert screen._state[0] is original_history
    assert screen._state[0] == [("saved", 7.0)]
    assert screen.input_box.get_str() == "saved input"


@pytest.mark.parametrize(
    "field", ("input_str", "expr", "title", "detail", "storage_error"))
def test_calculator_scenario_transaction_rejects_oversized_text_snapshot(field):
    screen = _screen()
    screen.input_box.set_str("saved input", immediate=True)
    screen.input_box.move_cursor_end()
    popup = screen._state[1]
    popup.show_static("Saved title", "Saved detail")
    popup.expr = "saved expression"
    popup._state[2] = 3
    screen.mode = 2
    screen._state[3][0][3][0] = "saved storage error"
    too_large = "x" * (calculator_module.MAX_EXPRESSION_CHARS + 1)

    if field == "input_str":
        screen.input_box.str = too_large
    elif field == "storage_error":
        screen._state[3][0][3][0] = too_large
    else:
        setattr(popup, field, too_large)

    popup_state = (
        popup.expr,
        popup._state[2],
        popup.title,
        popup.detail,
        popup._state[3],
        popup.active,
    )
    input_state = (
        screen.input_box.str,
        screen.input_box.cursor_pos,
        screen.input_box.view_offset,
    )
    storage_error = screen._state[3][0][3][0]

    with pytest.raises(RuntimeError, match="text snapshot"):
        screen.open_scenario_transaction()

    assert screen._state[3][3] is None
    assert screen.mode == 2
    assert (
        popup.expr,
        popup._state[2],
        popup.title,
        popup.detail,
        popup._state[3],
        popup.active,
    ) == popup_state
    assert (
        screen.input_box.str,
        screen.input_box.cursor_pos,
        screen.input_box.view_offset,
    ) == input_state
    assert screen._state[3][0][3][0] is storage_error


def test_calculator_scenario_transaction_requires_error_lifecycle_order():
    screen = _screen()
    transaction = screen.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="not visible"):
        transaction.step(CALCULATOR_SCENARIO_ERROR_DISMISS)

    assert transaction.step(CALCULATOR_SCENARIO_ERROR_SHOW, "1/") is True
    assert screen.mode == 2
    assert screen._state[1].active is True

    with pytest.raises(RuntimeError, match="already visible"):
        transaction.step(CALCULATOR_SCENARIO_ERROR_SHOW, "1/")

    assert transaction.step(CALCULATOR_SCENARIO_ERROR_DISMISS) is True
    assert screen.mode == 0
    assert screen._state[1].active is False
    assert transaction.close() is True
