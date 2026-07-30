import pytest

from calc.functions import EvalContext, build_registry
from calc.limits import MAX_VARIABLES
from calc.scenario_variables import (
    VARIABLES_SCENARIO_DELETE,
    VARIABLES_SCENARIO_FILL,
    VARIABLES_SCENARIO_OPERATION_COUNT,
    VARIABLES_SCENARIO_QUOTA,
    VARIABLES_SCENARIO_REFILL,
    VARIABLES_SCENARIO_RESTART,
    open_variables_scenario_transaction,
)
from screens.calculator import CalculatorScreen
from screens.calculator_scenario import CalculatorScenarioTransaction
from utils import storage


def _screen(variables=None):
    return CalculatorScreen(
        None,
        registry=build_registry(),
        variables={} if variables is None else variables,
    )


def test_variables_scenario_uses_one_bounded_scratch_table_and_restores_user_state():
    user_variables = {"saved": "value"}
    screen = _screen(user_variables)
    context = screen.context
    context.mark_dirty()
    original_dirty = context.dirty

    transaction = open_variables_scenario_transaction(screen)
    scratch = context.variables

    assert scratch is not user_variables
    assert scratch == {}
    assert context.dirty is False

    for index in range(MAX_VARIABLES):
        assert transaction.step(
            VARIABLES_SCENARIO_FILL, "v" + str(index), index) is True

    assert len(scratch) == MAX_VARIABLES
    assert context.dirty is False
    assert transaction.step(
        VARIABLES_SCENARIO_QUOTA, "overflow", MAX_VARIABLES) is True
    assert "overflow" not in scratch

    assert transaction.step(VARIABLES_SCENARIO_RESTART) is True
    assert context.variables is scratch
    assert scratch == {}
    assert context.dirty is False

    assert transaction.step(VARIABLES_SCENARIO_REFILL, "v0", 0) is True
    assert transaction.step(VARIABLES_SCENARIO_DELETE, "v0") is True
    assert transaction.step(
        VARIABLES_SCENARIO_REFILL, "replacement", 100) is True
    assert set(scratch) == {"replacement"}

    assert transaction.close() is True
    assert transaction.close() is True
    assert context.variables is user_variables
    assert user_variables == {"saved": "value"}
    assert context.dirty is original_dirty
    assert screen._state[3][3] is None


def test_variables_canonical_sequence_proves_37_operations_and_restores_user_state():
    user_variables = {"saved": "value"}
    screen = _screen(user_variables)
    context = screen.context
    context.mark_dirty()
    original_dirty = context.dirty
    transaction = open_variables_scenario_transaction(screen)
    scratch = context.variables

    assert VARIABLES_SCENARIO_OPERATION_COUNT == MAX_VARIABLES + 5
    assert transaction.canonical_operations_completed == 0
    assert transaction.canonical_complete is False

    for index in range(VARIABLES_SCENARIO_OPERATION_COUNT):
        assert transaction.step_canonical_operation(index) is True
        assert transaction.canonical_operations_completed == index + 1

    assert transaction.canonical_complete is True
    assert set(scratch) == {"replacement"}
    assert transaction.close() is True
    assert context.variables is user_variables
    assert user_variables == {"saved": "value"}
    assert context.dirty is original_dirty
    assert screen._state[3][3] is None


def test_variables_canonical_sequence_rejects_wrong_order_and_terminal_mismatch():
    user_variables = {"saved": "value"}
    screen = _screen(user_variables)
    context = screen.context
    transaction = open_variables_scenario_transaction(screen)

    with pytest.raises(RuntimeError, match="canonical order"):
        transaction.step_canonical_operation(1)
    with pytest.raises(ValueError, match="operation index"):
        transaction.step_canonical_operation(VARIABLES_SCENARIO_OPERATION_COUNT)
    assert transaction.canonical_operations_completed == 0

    for index in range(VARIABLES_SCENARIO_OPERATION_COUNT - 1):
        assert transaction.step_canonical_operation(index) is True

    context.variables["unexpected"] = 1
    with pytest.raises(RuntimeError, match="canonical proof"):
        transaction.step_canonical_operation(
            VARIABLES_SCENARIO_OPERATION_COUNT - 1)

    assert transaction.canonical_operations_completed == (
        VARIABLES_SCENARIO_OPERATION_COUNT - 1)
    assert transaction.canonical_complete is False
    assert transaction.close() is True
    assert context.variables is user_variables
    assert user_variables == {"saved": "value"}


def test_variables_canonical_operation_keeps_oom_primary_and_progress_retryable(
        monkeypatch):
    user_variables = {"saved": "value"}
    screen = _screen(user_variables)
    context = screen.context
    context.mark_dirty()
    original_dirty = context.dirty
    primary = MemoryError("injected canonical variables OOM")

    def exhaust_heap(_context, _name, _value):
        raise primary

    monkeypatch.setattr(EvalContext, "set_var", exhaust_heap)
    transaction = open_variables_scenario_transaction(screen)

    with pytest.raises(MemoryError) as caught_step:
        transaction.step_canonical_operation(0)

    assert caught_step.value is primary
    assert transaction.canonical_operations_completed == 0
    assert transaction.canonical_complete is False
    with pytest.raises(MemoryError) as caught_close:
        transaction.close_with_primary(primary)

    assert caught_close.value is primary
    assert context.variables is user_variables
    assert context.dirty is original_dirty
    assert screen._state[3][3] is None


def test_variables_scenario_never_uses_the_persistence_path(monkeypatch):
    screen = _screen({"saved": 1})
    calls = []

    def unexpected_save(*_args, **_kwargs):
        calls.append("save")
        raise AssertionError("scenario must not persist variables")

    def unexpected_flush(*_args, **_kwargs):
        calls.append("flush")
        raise AssertionError("scenario must not flush storage")

    monkeypatch.setattr(storage, "save_vars", unexpected_save)
    monkeypatch.setattr(storage.DeferredStorage, "flush", unexpected_flush)
    transaction = open_variables_scenario_transaction(screen)

    assert transaction.step(VARIABLES_SCENARIO_FILL, "v0", 0) is True
    assert transaction.step(VARIABLES_SCENARIO_RESTART) is True
    assert transaction.close() is True
    assert calls == []


def test_variables_scenario_propagates_original_memory_error_and_restores_identity(
        monkeypatch):
    user_variables = {"saved": "value"}
    screen = _screen(user_variables)
    context = screen.context
    context.mark_dirty()
    original_dirty = context.dirty
    error = MemoryError("injected scratch OOM")

    def exhaust_heap(_context, _name, _value):
        raise error

    monkeypatch.setattr(EvalContext, "set_var", exhaust_heap)
    transaction = open_variables_scenario_transaction(screen)
    scratch = context.variables

    with pytest.raises(MemoryError) as caught:
        transaction.step(VARIABLES_SCENARIO_FILL, "v0", 0)

    assert caught.value is error
    assert scratch == {}
    assert user_variables == {"saved": "value"}
    assert transaction.close() is True
    assert context.variables is user_variables
    assert context.dirty is original_dirty


def test_variables_scenario_close_retries_calculator_cleanup_after_normal_failure(
        monkeypatch):
    user_variables = {"saved": "value"}
    screen = _screen(user_variables)
    context = screen.context
    context.mark_dirty()
    original_dirty = context.dirty
    transaction = open_variables_scenario_transaction(screen)
    real_close = CalculatorScenarioTransaction.close_with_primary
    close_calls = [0]

    def fail_once(calculator_transaction, primary_error):
        close_calls[0] += 1
        if close_calls[0] == 1:
            raise RuntimeError("injected calculator cleanup failure")
        return real_close(calculator_transaction, primary_error)

    monkeypatch.setattr(
        CalculatorScenarioTransaction, "close_with_primary", fail_once)

    with pytest.raises(RuntimeError, match="injected calculator cleanup"):
        transaction.close()

    assert context.variables is user_variables
    assert context.dirty is original_dirty
    with pytest.raises(RuntimeError, match="closing"):
        transaction.step(VARIABLES_SCENARIO_FILL, "v0", 0)

    assert transaction.close() is True
    assert close_calls == [2]
    assert screen._state[3][3] is None


def test_variables_scenario_close_with_primary_preserves_step_oom_and_releases(
        monkeypatch):
    user_variables = {"saved": "value"}
    screen = _screen(user_variables)
    context = screen.context
    context.mark_dirty()
    original_dirty = context.dirty
    primary = MemoryError("injected variables step OOM")
    cleanup = MemoryError("injected calculator cleanup OOM")

    def exhaust_heap(_context, _name, _value):
        raise primary

    monkeypatch.setattr(EvalContext, "set_var", exhaust_heap)
    transaction = open_variables_scenario_transaction(screen)
    scratch = context.variables

    with pytest.raises(MemoryError) as caught_step:
        transaction.step(VARIABLES_SCENARIO_FILL, "v0", 0)

    assert caught_step.value is primary
    original_restore_popup = CalculatorScenarioTransaction._restore_popup
    restore_attempts = [0]

    def fail_first_restore(calculator_transaction, calculator_screen):
        restore_attempts[0] += 1
        if restore_attempts[0] == 1:
            raise cleanup
        return original_restore_popup(calculator_transaction, calculator_screen)

    monkeypatch.setattr(
        CalculatorScenarioTransaction, "_restore_popup", fail_first_restore)

    with pytest.raises(MemoryError) as caught_cleanup:
        transaction.close_with_primary(primary)

    assert caught_cleanup.value is primary
    assert context.variables is user_variables
    assert context.dirty is original_dirty
    assert transaction._closed is False
    assert transaction._variables_restored is True
    assert transaction._calculator_transaction is screen._state[3][3]
    assert transaction._scratch is scratch

    with pytest.raises(MemoryError) as caught_retry:
        transaction.close_with_primary(primary)

    assert caught_retry.value is primary
    assert restore_attempts == [2]
    assert context.variables is user_variables
    assert context.dirty is original_dirty
    assert screen._state[3][3] is None
    assert transaction._closed is True
    assert transaction._calculator is None
    assert transaction._calculator_transaction is None
    assert transaction._context is None
    assert transaction._user_variables is None
    assert transaction._scratch is None


def test_variables_scenario_close_with_primary_promotes_cleanup_oom(
        monkeypatch):
    user_variables = {"saved": "value"}
    screen = _screen(user_variables)
    context = screen.context
    context.mark_dirty()
    original_dirty = context.dirty
    primary = RuntimeError("injected ordinary variables failure")
    cleanup = MemoryError("injected calculator cleanup OOM")

    def fail_normally(_context, _name, _value):
        raise primary

    monkeypatch.setattr(EvalContext, "set_var", fail_normally)
    transaction = open_variables_scenario_transaction(screen)

    with pytest.raises(RuntimeError) as caught_step:
        transaction.step(VARIABLES_SCENARIO_FILL, "v0", 0)

    assert caught_step.value is primary
    original_restore_popup = CalculatorScenarioTransaction._restore_popup
    restore_attempts = [0]

    def fail_first_restore(calculator_transaction, calculator_screen):
        restore_attempts[0] += 1
        if restore_attempts[0] == 1:
            raise cleanup
        return original_restore_popup(calculator_transaction, calculator_screen)

    monkeypatch.setattr(
        CalculatorScenarioTransaction, "_restore_popup", fail_first_restore)

    with pytest.raises(MemoryError) as caught_cleanup:
        transaction.close_with_primary(primary)

    assert caught_cleanup.value is cleanup
    assert context.variables is user_variables
    assert context.dirty is original_dirty
    assert transaction._closed is False
    assert transaction._variables_restored is True
    assert transaction._calculator_transaction is screen._state[3][3]

    assert transaction.close() is True
    assert restore_attempts == [2]
    assert screen._state[3][3] is None
    assert transaction._closed is True
    assert transaction._calculator is None
    assert transaction._calculator_transaction is None
    assert transaction._context is None
    assert transaction._user_variables is None
    assert transaction._scratch is None


def test_variables_scenario_oom_open_leaves_the_user_context_untouched(
        monkeypatch):
    user_variables = {"saved": "value"}
    screen = _screen(user_variables)
    context = screen.context
    context.mark_dirty()
    original_dirty = context.dirty
    error = MemoryError("injected calculator transaction OOM")

    def exhaust_open(_screen):
        raise error

    monkeypatch.setattr(CalculatorScreen, "open_scenario_transaction", exhaust_open)

    with pytest.raises(MemoryError) as caught:
        open_variables_scenario_transaction(screen)

    assert caught.value is error
    assert context.variables is user_variables
    assert context.dirty is original_dirty
