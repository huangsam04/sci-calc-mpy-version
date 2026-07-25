import subprocess
import sys
from pathlib import Path

import pytest

from runtime_acceptance import RUN_END, RUN_START, RuntimeHandle, run
from runtime_scenarios import (
    ACTION_CALCULATOR_HISTORY,
    ACTION_ERROR_LIFECYCLE,
    ACTION_PLOT_PIPELINE,
    ACTION_PLUGIN_RELOAD,
    ACTION_PAGE_ROUND_TRIPS,
    ACTION_STOPWATCH_LAPS,
    ACTION_VARIABLE_QUOTA_RESTART,
    APPLICATION_CAPABILITIES,
    APPLICATION_PAGE_IDS,
    FAILED,
    MAX_CALCULATOR_HISTORY,
    MAX_CALCULATOR_INPUT,
    PASS,
    STOPWATCH_LAP_COUNT,
    UNAVAILABLE,
    ResidentApplicationScenarioAdapter,
    application_matrix,
    application_scenarios,
)
from runtime_scenarios_host import InMemoryApplicationScenarioAdapter


class _Memory:
    def __init__(self):
        self._buffers = {}


class _Nav:
    def __init__(self, root):
        self.current = root
        self.memory = _Memory()
        self.renderer = type("Renderer", (), {"display": None})()

    def reset(self, root):
        self.current = root

    def present_current(self):
        pass


def _runtime(adapter):
    root = object()
    return RuntimeHandle(
        _Nav(root),
        root,
        (),
        mode="in_memory",
        scenario_adapter=adapter,
    )


def test_calculator_scenario_builds_and_traverses_twenty_max_length_entries():
    adapter = InMemoryApplicationScenarioAdapter()
    scenario = application_scenarios(rounds=1)[0]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_CALCULATOR_HISTORY)

    assert report.scenario_name == "calculator_history"
    assert report.scenarios_completed == 1
    assert report.accepted
    assert verdict.status == PASS
    assert verdict.rounds_completed == 1
    assert verdict.operations == 60
    assert verdict.restored


def test_error_scenario_displays_and_dismisses_twenty_distinct_errors():
    adapter = InMemoryApplicationScenarioAdapter()
    scenario = application_scenarios(rounds=1)[1]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_ERROR_LIFECYCLE)

    assert report.scenario_name == "error_lifecycle"
    assert report.scenarios_completed == 1
    assert report.accepted
    assert verdict.status == PASS
    assert verdict.rounds_completed == 1
    assert verdict.operations == 40
    assert verdict.restored


def test_variable_scenario_fills_quota_persists_restarts_deletes_and_refills():
    adapter = InMemoryApplicationScenarioAdapter()
    scenario = application_scenarios(rounds=1)[2]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_VARIABLE_QUOTA_RESTART)

    assert report.scenario_name == "variable_quota_restart"
    assert report.scenarios_completed == 1
    assert report.accepted
    assert verdict.status == PASS
    assert verdict.rounds_completed == 1
    assert verdict.operations == 37
    assert verdict.restored


def test_plot_scenario_runs_full_pipeline_and_includes_domain_errors():
    adapter = InMemoryApplicationScenarioAdapter()
    scenario = application_scenarios(rounds=1)[3]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)

    assert report.scenario_name == "plot_pipeline"
    assert report.scenarios_completed == 1
    assert report.accepted
    assert verdict.status == PASS
    assert verdict.rounds_completed == 1
    assert verdict.operations == 5
    assert verdict.restored


def test_plugin_scenario_toggles_rescans_reloads_and_rolls_back_dependency_failure():
    adapter = InMemoryApplicationScenarioAdapter()
    scenario = application_scenarios(rounds=1)[4]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_PLUGIN_RELOAD)

    assert report.scenario_name == "plugin_reload"
    assert report.scenarios_completed == 1
    assert report.accepted
    assert verdict.status == PASS
    assert verdict.rounds_completed == 1
    assert verdict.operations == 8
    assert verdict.restored


def test_stopwatch_scenario_runs_twenty_laps_scrolls_both_ways_and_returns():
    adapter = InMemoryApplicationScenarioAdapter()
    scenario = application_scenarios(rounds=1)[5]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_STOPWATCH_LAPS)

    assert report.scenario_name == "stopwatch_laps"
    assert report.scenarios_completed == 1
    assert report.accepted
    assert verdict.status == PASS
    assert verdict.rounds_completed == 1
    assert verdict.operations == 60
    assert verdict.restored


def test_page_scenario_enters_and_exits_every_main_and_auxiliary_page():
    adapter = InMemoryApplicationScenarioAdapter()
    scenario = application_scenarios(rounds=1)[6]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_PAGE_ROUND_TRIPS)

    assert report.scenario_name == "page_round_trips"
    assert report.scenarios_completed == 1
    assert report.accepted
    assert verdict.status == PASS
    assert verdict.rounds_completed == 1
    assert verdict.operations == 18
    assert verdict.restored


def test_diagnostics_are_seven_independent_five_round_reports():
    adapter = InMemoryApplicationScenarioAdapter()
    runtime = _runtime(adapter)
    scenarios = application_scenarios()

    reports = [run(runtime, scenario) for scenario in scenarios]

    assert APPLICATION_CAPABILITIES == (
        "calculator_history",
        "error_lifecycle",
        "variable_quota_restart",
        "plot_pipeline",
        "plugin_reload",
        "stopwatch_laps",
        "page_round_trips",
    )
    assert APPLICATION_PAGE_IDS == (
        "Calculator",
        "Plot",
        "Add-ons",
        "Stopwatch",
        "Settings",
        "About",
        "Letters",
        "Catalog",
        "Variables",
    )
    assert MAX_CALCULATOR_HISTORY == 20
    assert MAX_CALCULATOR_INPUT == 96
    assert STOPWATCH_LAP_COUNT == 20
    assert [report.scenario_name for report in reports] == list(
        APPLICATION_CAPABILITIES)
    assert len(reports) == 7
    assert all(report.rounds_expected == 5 for report in reports)
    assert all(report.rounds_completed == 5 for report in reports)
    assert all(report.scenarios_completed == 5 for report in reports)
    assert all(report.accepted for report in reports)
    for action in range(1, 8):
        verdict = adapter.verdict(action)
        assert verdict.status == PASS
        assert verdict.rounds_completed == 5
        assert verdict.restored


class _MatrixController:
    def __init__(self):
        self.calls = []

    def supports(self, capability):
        return capability in APPLICATION_CAPABILITIES

    def snapshot(self, capability):
        return len(self.calls)

    def perform(self, runtime, capability, round_index):
        self.calls.append((round_index, capability))
        return 1

    def restore(self, capability, snapshot):
        return True


def test_application_matrix_runs_all_capabilities_in_order_under_one_report():
    controller = _MatrixController()
    adapter = ResidentApplicationScenarioAdapter(controller)
    events = []

    report = run(
        _runtime(adapter),
        application_matrix(rounds=1),
        lambda event, current: events.append(event),
    )

    assert report.scenario_name == "application_matrix"
    assert report.rounds_completed == 1
    assert report.scenarios_completed == 7
    assert controller.calls == [
        (0, capability) for capability in APPLICATION_CAPABILITIES
    ]
    assert events.count(RUN_START) == 1
    assert events.count(RUN_END) == 1
    assert report.accepted


def test_application_matrix_repeats_the_complete_order_for_five_rounds():
    controller = _MatrixController()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(_runtime(adapter), application_matrix())

    assert controller.calls == [
        (round_index, capability)
        for round_index in range(5)
        for capability in APPLICATION_CAPABILITIES
    ]
    assert report.rounds_expected == 5
    assert report.rounds_completed == 5
    assert report.scenarios_completed == 35
    assert report.accepted
    for action in range(1, 8):
        assert adapter.verdict(action).rounds_completed == 5


def test_host_adapter_executes_the_complete_matrix_under_one_baseline():
    adapter = InMemoryApplicationScenarioAdapter()

    report = run(_runtime(adapter), application_matrix())

    assert report.scenario_name == "application_matrix"
    assert report.rounds_expected == 5
    assert report.rounds_completed == 5
    assert report.scenarios_completed == 35
    assert report.accepted
    for action in range(1, 8):
        verdict = adapter.verdict(action)
        assert verdict.status == PASS
        assert verdict.rounds_completed == 5
        assert verdict.restored


def test_resident_adapter_reports_each_missing_capability_as_unavailable():
    scenarios = application_scenarios(rounds=1)

    for action, scenario in enumerate(scenarios, 1):
        adapter = ResidentApplicationScenarioAdapter()
        report = run(_runtime(adapter), scenario)
        verdict = adapter.verdict(action)

        assert not report.accepted
        assert report.errors == 1
        assert report.scenarios_completed == 0
        assert verdict.status == UNAVAILABLE
        assert verdict.rounds_completed == 0
        assert verdict.operations == 0
        assert verdict.restored
        assert verdict.reason == APPLICATION_CAPABILITIES[action - 1]


class _ScenarioController:
    def __init__(
            self, capability="variable_quota_restart", fail=False,
            restore_fail=False):
        self.state = "original"
        self.capability = capability
        self.fail = fail
        self.restore_fail = restore_fail

    def supports(self, capability):
        return capability == self.capability

    def snapshot(self, capability):
        return self.state

    def perform(self, runtime, capability, round_index):
        self.state = (capability, round_index)
        if self.fail == "memory":
            raise MemoryError
        if self.fail:
            raise ValueError("injected application failure")
        return 5

    def restore(self, capability, snapshot):
        self.state = snapshot
        if self.restore_fail == "memory":
            raise MemoryError
        if self.restore_fail:
            raise RuntimeError("injected restore failure")
        return True


class _IdentityFailureController(_ScenarioController):
    def __init__(self, error):
        _ScenarioController.__init__(
            self,
            capability="plot_pipeline",
            restore_fail=True,
        )
        self.error = error

    def perform(self, runtime, capability, round_index):
        self.state = (capability, round_index)
        raise self.error


def test_resident_adapter_uses_snapshot_perform_restore_for_variable_state():
    controller = _ScenarioController()
    adapter = ResidentApplicationScenarioAdapter(controller)
    scenario = application_scenarios(rounds=1)[2]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_VARIABLE_QUOTA_RESTART)

    assert report.accepted
    assert controller.state == "original"
    assert verdict.status == PASS
    assert verdict.rounds_completed == 1
    assert verdict.operations == 5
    assert verdict.restored


def test_resident_adapter_restores_state_before_reporting_controller_failure():
    controller = _ScenarioController(capability="plot_pipeline", fail=True)
    adapter = ResidentApplicationScenarioAdapter(controller)
    scenario = application_scenarios(rounds=1)[3]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)

    assert not report.accepted
    assert report.errors == 1
    assert controller.state == "original"
    assert verdict.status == FAILED
    assert verdict.rounds_completed == 0
    assert verdict.restored


def test_resident_adapter_restores_state_and_preserves_memory_error_identity():
    controller = _ScenarioController(
        capability="plot_pipeline",
        fail="memory",
    )
    adapter = ResidentApplicationScenarioAdapter(controller)
    scenario = application_scenarios(rounds=1)[3]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)

    assert not report.accepted
    assert report.memory_errors == 1
    assert report.errors == 0
    assert controller.state == "original"
    assert verdict.status == FAILED
    assert verdict.reason == "MemoryError"
    assert verdict.rounds_completed == 0
    assert verdict.restored


def test_resident_adapter_keeps_perform_oom_primary_when_restore_also_fails():
    controller = _ScenarioController(
        capability="plot_pipeline",
        fail="memory",
        restore_fail=True,
    )
    adapter = ResidentApplicationScenarioAdapter(controller)
    scenario = application_scenarios(rounds=1)[3]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)

    assert not report.accepted
    assert report.memory_errors == 1
    assert report.errors == 0
    assert controller.state == "original"
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert verdict.rounds_completed == 0
    assert not verdict.restored


def test_resident_adapter_reraises_the_same_oom_after_restore_failure():
    primary_error = MemoryError("injected primary OOM")
    controller = _IdentityFailureController(primary_error)
    adapter = ResidentApplicationScenarioAdapter(controller)

    with pytest.raises(MemoryError) as caught:
        adapter.perform(
            _runtime(adapter),
            ACTION_PLOT_PIPELINE,
            0,
        )

    assert caught.value is primary_error
    assert controller.state == "original"
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert not verdict.restored


def test_resident_adapter_reports_restore_oom_as_memory_failure():
    controller = _ScenarioController(
        capability="plot_pipeline",
        restore_fail="memory",
    )
    adapter = ResidentApplicationScenarioAdapter(controller)
    scenario = application_scenarios(rounds=1)[3]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)

    assert not report.accepted
    assert report.memory_errors == 1
    assert report.errors == 0
    assert controller.state == "original"
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert verdict.rounds_completed == 0
    assert not verdict.restored


def test_resident_adapter_prioritizes_restore_oom_over_execution_error():
    controller = _ScenarioController(
        capability="plot_pipeline",
        fail=True,
        restore_fail="memory",
    )
    adapter = ResidentApplicationScenarioAdapter(controller)
    scenario = application_scenarios(rounds=1)[3]

    report = run(_runtime(adapter), scenario)
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)

    assert not report.accepted
    assert report.memory_errors == 1
    assert report.errors == 0
    assert controller.state == "original"
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert verdict.rounds_completed == 0
    assert not verdict.restored


def test_production_scenario_module_does_not_import_the_host_simulator():
    source_dir = Path(__file__).parents[1] / "source"
    script = (
        "import sys;"
        "sys.path.insert(0, " + repr(str(source_dir)) + ");"
        "import runtime_scenarios;"
        "assert 'runtime_scenarios_host' not in sys.modules"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
