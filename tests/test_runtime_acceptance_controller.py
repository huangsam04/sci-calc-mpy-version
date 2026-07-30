"""Public integration contracts for the resident bounded controller.

These tests deliberately enter through the sealed adapter builder instead of
the controller's private classes.  The fake screens expose only the public
scenario leases used by one capability at a time.
"""

from runtime_acceptance import FAIL_INCOMPLETE, FAIL_MEMORY, STEP_MORE, run
from runtime_application_controller import (
    build_resident_application_scenario_adapter)
from runtime_handle import ApplicationBinding
from runtime_materialize import RuntimeHandle
from runtime_scenarios import application_scenarios


class _Nav:
    def __init__(self, root):
        self.current = root
        self.present_calls = 0

    def reset(self, root):
        self.current = root

    def present_current(self):
        self.present_calls += 1


class _CalculatorTransaction:
    def __init__(self, error=None):
        self.error = error
        self.step_calls = 0
        self.close_calls = 0

    def step(self, _action, _argument=None):
        self.step_calls += 1
        if self.error is not None:
            raise self.error
        return True

    def close(self):
        self.close_calls += 1
        return True


class _Calculator:
    def __init__(self, error=None):
        self.error = error
        self.transactions = []

    def open_scenario_transaction(self):
        transaction = _CalculatorTransaction(self.error)
        self.transactions.append(transaction)
        return transaction


class _StalledPlotTransaction:
    def __init__(self):
        self.probes = []
        self.step_calls = 0
        self.terminal = False
        self.result = None
        self.close_calls = 0

    def start_probe(self, probe):
        self.probes.append(probe)
        return True

    def step(self):
        self.step_calls += 1
        return True

    def close(self):
        self.close_calls += 1
        return True


class _Plot:
    def __init__(self):
        self.transactions = []

    def open_scenario_transaction(self):
        transaction = _StalledPlotTransaction()
        self.transactions.append(transaction)
        return transaction


def _runtime(calculator_error=None):
    root = object()
    nav = _Nav(root)
    calculator = _Calculator(calculator_error)
    plot = _Plot()
    screens = (
        root,
        calculator,
        plot,
        object(),
        object(),
        object(),
        object(),
        object(),
        object(),
        object(),
    )
    binding = ApplicationBinding(
        screens, object(), object(), object(), nav=nav)
    adapter = build_resident_application_scenario_adapter(binding)
    runtime = RuntimeHandle(
        nav,
        root,
        (),
        mode="resident",
        scenario_adapter=adapter,
        application_binding=binding,
    )
    return runtime, calculator, plot


def test_public_adapter_opens_one_calculator_lease_for_the_first_step_only():
    runtime, calculator, plot = _runtime()
    adapter = runtime.scenario_adapter

    session = adapter.open_bounded_session(runtime, ("calculator_history",))

    assert session.step_limits == (64,)
    assert session.no_progress_limits == (1,)
    assert session.step(0, 0) == STEP_MORE
    assert len(calculator.transactions) == 1
    assert calculator.transactions[0].step_calls == 0
    assert plot.transactions == []
    assert session.completed_count == 0

    assert session.close() is True
    assert calculator.transactions[0].close_calls == 1


def test_public_runner_fails_closed_and_restores_a_stalled_plot_lease():
    runtime, _calculator, plot = _runtime()

    report = run(runtime, application_scenarios()[3])

    assert not report.accepted
    assert report.errors == 1
    assert report.failure_mask & FAIL_INCOMPLETE
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    assert report.bounded_session_restored is True
    assert len(plot.transactions) == 1
    assert plot.transactions[0].step_calls == 510
    assert plot.transactions[0].close_calls == 1


def test_public_runner_restores_the_active_lease_after_cross_step_oom():
    primary = MemoryError("calculator scenario OOM")
    runtime, calculator, _plot = _runtime(primary)

    report = run(runtime, application_scenarios()[0])

    assert not report.accepted
    assert report.primary_error is primary
    assert report.memory_errors == 1
    assert report.failure_mask & FAIL_MEMORY
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    assert report.bounded_session_restored is True
    assert len(calculator.transactions) == 1
    assert calculator.transactions[0].step_calls == 1
    assert calculator.transactions[0].close_calls == 1
