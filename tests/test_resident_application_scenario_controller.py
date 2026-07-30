import pytest

from calc import plugin_fixture, plugin_reload, scenario_variables
import runtime_fixture_pack
from runtime_acceptance import STEP_DONE
from runtime_application_controller import (
    _ResidentApplicationScenarioController)
from runtime_handle import ApplicationBinding
from runtime_materialize import RuntimeHandle
from runtime_scenarios import (
    APPLICATION_CAPABILITIES, APPLICATION_OPERATION_COUNTS,
    ERROR_KIND_COUNT, MAX_CALCULATOR_HISTORY, MAX_CALCULATOR_INPUT,
    STOPWATCH_LAP_COUNT)
from screens.calculator_scenario import (
    CALCULATOR_SCENARIO_ERROR_DISMISS, CALCULATOR_SCENARIO_ERROR_KIND,
    CALCULATOR_SCENARIO_HISTORY, CALCULATOR_SCENARIO_HISTORY_CURSOR,
    CALCULATOR_SCENARIO_HISTORY_CURSOR_FORWARD,
    CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE)
from screens.plot_scenario import (
    PLOT_SCENARIO_PROBE_ORDINARY_ERROR, PLOT_SCENARIO_PROBE_VALID,
    PLOT_SCENARIO_RESULT_COMPLETE, PLOT_SCENARIO_RESULT_ORDINARY_ERROR)


class _PoisonedNav:
    def __init__(self):
        self.page_transaction = _PageTransaction()

    @property
    def _managed(self):
        raise AssertionError("controller must not inspect nav._managed")

    def open_page_scenario_transaction(self, _screens):
        return self.page_transaction


class _PoisonedRuntime(RuntimeHandle):
    __slots__ = ()

    def find_target(self, _name):
        raise AssertionError("controller must not discover runtime targets")


class _CalculatorTransaction:
    def __init__(self):
        self.history_steps = 0
        self.history_reverse_steps = 0
        self.history_forward_steps = 0
        self.history_cursor = None
        self.error_kind = None
        self.error_diagnostic_proof = None
        self.error_kind_mask = 0
        self.closed = False

    def step(self, action, argument=None):
        if action == CALCULATOR_SCENARIO_HISTORY:
            assert isinstance(argument, str)
            assert len(argument) == MAX_CALCULATOR_INPUT
            self.history_steps += 1
            return True
        if action == CALCULATOR_SCENARIO_HISTORY_CURSOR:
            if argument == CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE:
                self.history_cursor = (
                    MAX_CALCULATOR_HISTORY - self.history_reverse_steps - 1)
                self.history_reverse_steps += 1
                return True
            assert argument == CALCULATOR_SCENARIO_HISTORY_CURSOR_FORWARD
            self.history_cursor = self.history_forward_steps
            self.history_forward_steps += 1
            return True
        if action == CALCULATOR_SCENARIO_ERROR_KIND:
            self.error_kind = argument
            self.error_diagnostic_proof = argument + 1
            self.error_kind_mask |= 1 << argument
            return True
        assert action == CALCULATOR_SCENARIO_ERROR_DISMISS
        self.error_kind = None
        self.error_diagnostic_proof = None
        return True

    def close(self):
        self.closed = True
        return True


class _Calculator:
    def __init__(self):
        self.transactions = []

    def open_scenario_transaction(self):
        transaction = _CalculatorTransaction()
        self.transactions.append(transaction)
        return transaction


class _VariablesTransaction:
    def __init__(self):
        self.canonical_operations_completed = 0
        self.canonical_complete = False
        self.closed = False

    def step_canonical_operation(self, index):
        assert index == self.canonical_operations_completed
        self.canonical_operations_completed += 1
        if self.canonical_operations_completed == (
                scenario_variables.VARIABLES_SCENARIO_OPERATION_COUNT):
            self.canonical_complete = True
        return True

    def close(self):
        self.closed = True
        return True


class _PlotTransaction:
    def __init__(self):
        self.probe = None
        self.terminal = False
        self.result = None
        self.closed = False

    def start_probe(self, probe):
        self.probe = probe
        self.terminal = False
        self.result = None
        return True

    def step(self):
        self.terminal = True
        if self.probe == PLOT_SCENARIO_PROBE_VALID:
            self.result = PLOT_SCENARIO_RESULT_COMPLETE
        else:
            assert self.probe == PLOT_SCENARIO_PROBE_ORDINARY_ERROR
            self.result = PLOT_SCENARIO_RESULT_ORDINARY_ERROR
        return True

    def close(self):
        self.closed = True
        return True


class _Plot:
    def __init__(self):
        self.transactions = []

    def open_scenario_transaction(self):
        transaction = _PlotTransaction()
        self.transactions.append(transaction)
        return transaction


class _StopwatchLease:
    def __init__(self):
        self.laps = 0
        self.older = 0
        self.newer = 0
        self.lap_window_active = True
        self.lap_window_verified = False
        self.closed = False

    def start(self):
        return True

    def lap(self):
        self.laps += 1
        return True

    def move_lap_cursor(self, direction):
        if direction == 1:
            self.older += 1
        else:
            assert direction == -1
            self.newer += 1
        return True

    def verify_and_leave_lap_window(self):
        assert self.laps == STOPWATCH_LAP_COUNT
        assert self.older == STOPWATCH_LAP_COUNT - 1
        assert self.newer == STOPWATCH_LAP_COUNT - 1
        self.lap_window_active = False
        self.lap_window_verified = True
        return True

    def close(self):
        self.closed = True
        return True


class _Stopwatch:
    def __init__(self):
        self.leases = []

    def open_scenario_lease(self):
        lease = _StopwatchLease()
        self.leases.append(lease)
        return lease


class _PageTransaction:
    def __init__(self):
        self.actions = []
        self.closed = False

    def step(self, action):
        self.actions.append(action)
        return True

    def close(self):
        self.closed = True
        return True


class _FixtureSnapshot:
    directory = "/sd/.slots/A/functions"
    files = (
        "_acceptance_core.py",
        "_acceptance_dependent.py",
        "_acceptance_missing.py",
    )
    valid_selection = ("plugin:_acceptance_dependent",)
    missing_selection = ("plugin:_acceptance_missing",)

    def __init__(self):
        self.reverifies = 0

    def open_reverify(self):
        self.reverifies += 1
        return _FixtureCandidate(self)


class _FixtureCandidate:
    def __init__(self, snapshot=None):
        self.available = True
        self.snapshot = _FixtureSnapshot() if snapshot is None else snapshot
        self.closed = False

    def step(self):
        return True

    def close(self):
        self.closed = True
        return True


class _PluginReport:
    def __init__(self, valid):
        if valid:
            self.loaded = [
                ("_acceptance_core", 1, ""),
                ("_acceptance_dependent", 1, ""),
            ]
            self.errors = []
        else:
            self.loaded = []
            self.errors = [
                ("_acceptance_missing", "Dependency failed: _acceptance_absent")]


class _PluginTransaction:
    def __init__(self, valid):
        self.complete = True
        self.succeeded = valid
        self.report = _PluginReport(valid)
        self.cancelled = False
        self.closed = False

    def step(self):
        return True

    def cancel(self):
        self.cancelled = True
        self.closed = True
        return True

    def close(self):
        self.closed = True
        return True


class _FixturePack:
    directory = "/sd/.slots/A/functions"
    files = _FixtureSnapshot.files
    valid_selection = _FixtureSnapshot.valid_selection
    missing_selection = _FixtureSnapshot.missing_selection

    def __init__(self, snapshot):
        self._snapshot = snapshot

    def open_reverify(self):
        return self._snapshot.open_reverify()

    def accepts_reverified_candidate(self, candidate):
        return candidate.available and candidate.snapshot is self._snapshot

    def valid_reload_result(self, transaction):
        return transaction.complete and transaction.succeeded

    def missing_reload_result(self, transaction):
        return transaction.complete and not transaction.succeeded


def _binding():
    root = object()
    nav = _PoisonedNav()
    calculator = _Calculator()
    plot = _Plot()
    function_panel = object()
    stopwatch = _Stopwatch()
    screens = (
        root, calculator, plot, function_panel, stopwatch, object(), object(),
        object(), object(), object())
    binding = ApplicationBinding(
        screens, object(), object(), object(), nav=nav)
    runtime = _PoisonedRuntime(
        nav, root, (), mode="resident", application_binding=binding)
    return binding, runtime, calculator, plot, stopwatch, nav


def _run_one_round(session):
    for capability_index, _capability in enumerate(APPLICATION_CAPABILITIES):
        limit = session.step_limits[capability_index]
        for _ in range(limit):
            if session.step(0, capability_index) == STEP_DONE:
                break
        else:
            raise AssertionError("resident controller exceeded its fixed limit")


def test_real_resident_controller_runs_one_bounded_matrix_with_public_primitives(
        monkeypatch):
    binding, runtime, calculator, plot, stopwatch, nav = _binding()
    controller = _ResidentApplicationScenarioController(binding)
    variables = []
    plugins = []

    def open_variables(_calculator):
        transaction = _VariablesTransaction()
        variables.append(transaction)
        return transaction

    def open_plugin_reload(_registry, _panel, settings=None, func_dir=None,
                           files=None, selection=None):
        assert settings is binding.settings
        assert func_dir == "/sd/.slots/A/functions"
        assert files == _FixtureSnapshot.files
        assert selection in (
            _FixtureSnapshot.valid_selection, _FixtureSnapshot.missing_selection)
        transaction = _PluginTransaction(
            selection == _FixtureSnapshot.valid_selection)
        plugins.append(transaction)
        return transaction

    monkeypatch.setattr(
        scenario_variables, "open_variables_scenario_transaction", open_variables)
    monkeypatch.setattr(
        plugin_fixture, "PluginScenarioFixtureCandidate", _FixtureCandidate)
    monkeypatch.setattr(
        runtime_fixture_pack,
        "bind_verified_candidate",
        lambda candidate: _FixturePack(candidate.snapshot))
    monkeypatch.setattr(
        plugin_reload, "open_plugin_reload_transaction", open_plugin_reload)

    session = controller.open_bounded_session(runtime, APPLICATION_CAPABILITIES)
    assert session.step_limits == (64, 64, 64, 512, 512, 64, 64)
    assert session.no_progress_limits == (1,) * len(APPLICATION_CAPABILITIES)
    with pytest.raises(AttributeError, match="limits"):
        session.step_limits = ()
    with pytest.raises(RuntimeError, match="already open"):
        controller.open_bounded_session(runtime, APPLICATION_CAPABILITIES)

    _run_one_round(session)

    assert session.completed_count == len(APPLICATION_CAPABILITIES)
    assert session.completed_operations == sum(APPLICATION_OPERATION_COUNTS)
    assert calculator.transactions[0].closed is True
    assert variables[0].canonical_complete is True
    assert variables[0].closed is True
    assert plot.transactions[0].closed is True
    assert plugins[0].cancelled is True
    assert plugins[1].closed is True
    assert stopwatch.leases[0].closed is True
    assert nav.page_transaction.actions == list(range(1, 10))
    assert nav.page_transaction.closed is False

    assert session.close() is True
    assert nav.page_transaction.closed is True
    assert controller.open_bounded_session(runtime, ("calculator_history",)).close() is True


def test_real_resident_controller_rejects_a_foreign_binding_before_opening_a_lease():
    binding, runtime, calculator, _plot, _stopwatch, _nav = _binding()
    controller = _ResidentApplicationScenarioController(binding)
    foreign_binding, foreign_runtime, _calc, _plot, _stopwatch, _nav = _binding()

    assert foreign_binding is not binding
    with pytest.raises(RuntimeError, match="foreign"):
        controller.open_bounded_session(
            foreign_runtime, ("calculator_history",))
    assert calculator.transactions == []
    assert controller.open_bounded_session(
        runtime, ("calculator_history",)).close() is True
