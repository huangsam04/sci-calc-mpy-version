import pytest

from calc.limits import MAX_VARIABLES
from screens import function_picker as function_picker_module
from screens import variable_panel as variable_panel_module
from screens.function_picker import FunctionPicker
from screens.function_picker_scenario import MAX_SCENARIO_FUNCTION_NAMES
from screens.variable_panel import VariablePanel


class _Input:
    def try_insert(self, _value):
        return True


class _Registry(dict):
    def __init__(self, names=()):
        super().__init__(
            (name, (None, None, "prefix")) for name in names)
        self.revision = 0


class _PickerCalculator:
    def __init__(self, registry):
        self.context = type("Context", (), {"registry": registry})()
        self.input_box = _Input()


class _VariablesCalculator:
    def __init__(self, variables):
        self.vars = variables
        self.input_box = _Input()

    def _fmt(self, value):
        return str(value)


class _RaiseOnNext:
    def __init__(self, error):
        self.error = error

    def __iter__(self):
        return self

    def __next__(self):
        raise self.error


class _OOMRegistry(_Registry):
    def __init__(self, error):
        super().__init__(("alpha",))
        self.error = error
        self.iterations = 0

    def keys(self):
        self.iterations += 1
        if self.iterations == 2:
            return _RaiseOnNext(self.error)
        return super().keys()


class _OOMVariables(dict):
    """Use a normal first iterator for the stamp, then fail scenario input."""

    def __init__(self, error):
        super().__init__({"alpha": 1})
        self.error = error
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations == 2:
            return _RaiseOnNext(self.error)
        return super().__iter__()


def _assert_two_name_steps(screen):
    transaction = screen.open_scenario_transaction()

    assert transaction.step() is False
    assert screen._state[1] == ["b"]
    assert transaction.step() is False
    assert screen._state[1] == ["b"]

    assert transaction.step() is False
    assert screen._state[1] == ["b", "a"]
    assert transaction.step() is False
    assert screen._state[1] == ["b", "b"]
    assert transaction.step() is False
    assert screen._state[1] == ["a", "b"]
    assert transaction.step() is True

    return transaction


def test_function_picker_transaction_batches_the_same_in_place_sort():
    picker = FunctionPicker(None, _PickerCalculator(_Registry(("b", "a"))))

    transaction = picker.open_scenario_transaction()

    assert transaction.step() is True
    assert transaction.close() is True
    assert picker._state[1] == ["a", "b"]


def test_variable_panel_transaction_consumes_one_name_or_one_comparison():
    panel = VariablePanel(None, _VariablesCalculator({"b": 1, "a": 2}))

    transaction = _assert_two_name_steps(panel)

    assert transaction.close() is True
    assert panel._state[1] == ["a", "b"]


def test_page_transactions_do_not_call_sorted_while_incrementally_rebuilding(
        monkeypatch):
    picker = FunctionPicker(None, _PickerCalculator(_Registry(("b", "a"))))
    panel = VariablePanel(None, _VariablesCalculator({"b": 1, "a": 2}))

    def reject_sorted(*_args, **_kwargs):
        raise AssertionError("scenario transaction must not call sorted")

    monkeypatch.setattr(
        function_picker_module, "sorted", reject_sorted, raising=False)
    monkeypatch.setattr(
        variable_panel_module, "sorted", reject_sorted, raising=False)

    picker_transaction = picker.open_scenario_transaction()
    assert picker_transaction.step() is True
    assert picker_transaction.close() is True

    panel_transaction = _assert_two_name_steps(panel)
    assert panel_transaction.close() is True


def test_page_transactions_reject_fixed_catalog_limits_before_claiming_screen():
    registry = _Registry(
        "fn" + str(index) for index in range(
            MAX_SCENARIO_FUNCTION_NAMES + 1))
    picker = FunctionPicker(None, _PickerCalculator(registry))
    original_names = picker._state[1]

    with pytest.raises(RuntimeError, match="exceeds its limit"):
        picker.open_scenario_transaction()

    assert picker._state[5] is None
    assert picker._state[1] is original_names
    assert len(picker._state[1]) == MAX_SCENARIO_FUNCTION_NAMES + 1

    variables = {
        "v" + str(index): index for index in range(MAX_VARIABLES + 1)
    }
    panel = VariablePanel(None, _VariablesCalculator(variables))

    with pytest.raises(RuntimeError, match="exceed the fixed limit"):
        panel.open_scenario_transaction()

    assert panel._state[5] is None
    assert panel._state[1] == ()


def test_page_transactions_reject_concurrent_open_calls():
    picker = FunctionPicker(None, _PickerCalculator(_Registry(("alpha",))))
    picker_transaction = picker.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="already active"):
        picker.open_scenario_transaction()

    assert picker_transaction.close() is True

    panel = VariablePanel(None, _VariablesCalculator({"x": 1}))
    panel_transaction = panel.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="already active"):
        panel.open_scenario_transaction()

    assert panel_transaction.close() is True


def test_function_picker_source_change_fails_closed_and_close_restores_scalars():
    registry = _Registry(
        "function_" + str(index)
        for index in range(MAX_SCENARIO_FUNCTION_NAMES - 1, -1, -1))
    picker = FunctionPicker(None, _PickerCalculator(registry))
    picker._state[2] = 5
    picker._state[3] = 8
    picker._state[4] = "keep"
    transaction = picker.open_scenario_transaction()

    assert transaction.step() is False
    registry["function_0"] = (None, 1, "prefix")
    registry.revision += 1

    with pytest.raises(RuntimeError, match="registry changed"):
        transaction.step()

    assert transaction.close() is True
    assert transaction.close() is True
    assert (picker._state[2], picker._state[3], picker._state[4]) == (5, 8, "keep")
    assert picker._state[1] == []
    assert not hasattr(picker, "_registry_source")
    with pytest.raises(RuntimeError, match="closed"):
        transaction.step()

    assert picker.open_scenario_transaction().close() is True


def test_variable_panel_value_change_fails_closed_and_close_restores_scalars():
    variables = {"b": 1, "a": 2}
    panel = VariablePanel(None, _VariablesCalculator(variables))
    panel._state[2] = 5
    panel._state[3] = 8
    panel._state[4] = "keep"
    transaction = panel.open_scenario_transaction()

    assert transaction.step() is False
    variables["a"] = 3

    with pytest.raises(RuntimeError, match="variables changed"):
        transaction.step()

    assert transaction.close() is True
    assert transaction.close() is True
    assert (panel._state[2], panel._state[3], panel._state[4]) == (5, 8, "keep")
    assert panel._state[1] == ()
    assert not hasattr(panel, "_variables_source")
    with pytest.raises(RuntimeError, match="closed"):
        transaction.step()

    assert panel.open_scenario_transaction().close() is True


def test_function_picker_transaction_preserves_primary_memory_error_and_retry_close():
    primary = MemoryError("injected catalog OOM")
    picker = FunctionPicker(None, _PickerCalculator(_OOMRegistry(primary)))
    picker._state[2] = 2
    picker._state[3] = 4
    picker._state[4] = "saved"
    transaction = picker.open_scenario_transaction()

    with pytest.raises(MemoryError) as raised:
        transaction.step()

    assert raised.value is primary
    assert transaction.close() is True
    assert transaction.close() is True
    assert (picker._state[2], picker._state[3], picker._state[4]) == (2, 4, "saved")
    assert picker._state[1] == []


def test_variable_panel_transaction_preserves_primary_memory_error_and_retry_close():
    primary = MemoryError("injected variables OOM")
    variables = _OOMVariables(primary)
    panel = VariablePanel(None, _VariablesCalculator(variables))
    panel._state[2] = 2
    panel._state[3] = 4
    panel._state[4] = "saved"
    transaction = panel.open_scenario_transaction()

    with pytest.raises(MemoryError) as raised:
        transaction.step()

    assert raised.value is primary
    assert transaction.close() is True
    assert transaction.close() is True
    assert (panel._state[2], panel._state[3], panel._state[4]) == (2, 4, "saved")
    assert panel._state[1] == ()


def test_function_picker_catalog_refreshes_on_activation():
    registry = _Registry(("alpha",))
    picker = FunctionPicker(None, _PickerCalculator(registry))
    picker.activate()
    assert picker._state[1] == ["alpha"]

    registry.clear()
    registry["beta"] = (None, None, "prefix")
    registry.revision += 1
    picker.activate()

    assert picker._state[1] == ["beta"]


def test_variable_panel_snapshot_refreshes_on_activation():
    variables = {"x": 1}
    panel = VariablePanel(None, _VariablesCalculator(variables))
    panel.activate()
    assert panel._state[1] == ["x"]

    variables["y"] = 2
    panel.activate()

    assert panel._state[1] == ["x", "y"]
