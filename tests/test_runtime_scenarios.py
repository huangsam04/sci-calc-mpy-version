import subprocess
import sys
from pathlib import Path

import pytest

from runtime_acceptance import (
    FAIL_ERROR, FAIL_MEMORY, RUN_BOUNDED, RUN_END, RUN_START, STEP_DONE,
    RuntimeHandle, run)
from runtime_scenarios import (
    ACTION_CALCULATOR_HISTORY,
    ACTION_ERROR_LIFECYCLE,
    ACTION_PLOT_PIPELINE,
    ACTION_PLUGIN_RELOAD,
    ACTION_PAGE_ROUND_TRIPS,
    ACTION_STOPWATCH_LAPS,
    ACTION_VARIABLE_QUOTA_RESTART,
    APPLICATION_CAPABILITIES,
    APPLICATION_DEVICE_OPERATIONS_READY,
    APPLICATION_MATRIX_DEVICE_READY,
    APPLICATION_OPERATION_COUNTS,
    APPLICATION_PAGE_IDS,
    FAILED,
    MAX_CALCULATOR_HISTORY,
    MAX_CALCULATOR_INPUT,
    PASS,
    STOPWATCH_LAP_COUNT,
    UNAVAILABLE,
    _ApplicationScenarioSession,
    ResidentApplicationScenarioAdapter,
    ScenarioUnavailable,
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


def _runtime(adapter, mode="in_memory"):
    root = object()
    return RuntimeHandle(
        _Nav(root),
        root,
        (),
        mode=mode,
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


def test_canonical_scenarios_use_ordered_bounded_session_descriptors():
    diagnostics = application_scenarios(rounds=1)
    diagnostic_sessions = []

    for capability, scenario in zip(APPLICATION_CAPABILITIES, diagnostics):
        scenario_name, rounds, steps = scenario
        assert scenario_name == capability
        assert rounds == 1
        assert len(steps) == 1
        step_name, kind, session = steps[0]
        assert step_name == capability
        assert kind == RUN_BOUNDED
        assert session.capabilities == (capability,)
        diagnostic_sessions.append(session)

    matrix_name, matrix_rounds, matrix_steps = application_matrix(rounds=1)
    matrix_session = matrix_steps[0][2]

    assert len({id(session) for session in diagnostic_sessions}) == 7
    assert matrix_name == "application_matrix"
    assert matrix_rounds == 1
    assert tuple(step[0] for step in matrix_steps) == APPLICATION_CAPABILITIES
    assert all(step[1] == RUN_BOUNDED for step in matrix_steps)
    assert all(step[2] is matrix_session for step in matrix_steps)
    assert matrix_session.capabilities == APPLICATION_CAPABILITIES
    assert APPLICATION_MATRIX_DEVICE_READY is True
    assert APPLICATION_DEVICE_OPERATIONS_READY is True


def test_application_descriptor_uses_direct_slot_fields_until_open():
    capabilities = ("calculator_history",)
    session = _ApplicationScenarioSession(capabilities)

    for field in ("capabilities", "step_limits", "no_progress_limits"):
        assert field in _ApplicationScenarioSession.__slots__
        assert not isinstance(
            _ApplicationScenarioSession.__dict__[field], property)
    assert session.capabilities is capabilities
    assert session.step_limits == ()
    assert session.no_progress_limits == ()
    assert session.completed_capability is None
    assert session.completed_count == 0
    assert session.completed_operations == 0


def test_application_descriptor_caches_bound_limits_in_direct_slots():
    class BoundSession:
        def __init__(self):
            self.step_limit_reads = 0
            self.no_progress_limit_reads = 0
            self.closes = 0

        @property
        def step_limits(self):
            self.step_limit_reads += 1
            if self.step_limit_reads > 1:
                raise AssertionError("descriptor must cache step limits")
            return (1,)

        @property
        def no_progress_limits(self):
            self.no_progress_limit_reads += 1
            if self.no_progress_limit_reads > 1:
                raise AssertionError("descriptor must cache no-progress limits")
            return (1,)

        def close(self):
            self.closes += 1
            return True

    class Adapter:
        def __init__(self):
            self.open_calls = []
            self.session = BoundSession()

        def open_bounded_session(self, runtime, capabilities):
            self.open_calls.append((runtime, capabilities))
            return self.session

    capabilities = ("calculator_history",)
    session = _ApplicationScenarioSession(capabilities)
    adapter = Adapter()
    runtime = _runtime(adapter, mode="resident")

    assert adapter.open_calls == []
    session.open(runtime)

    assert adapter.open_calls == [(runtime, capabilities)]
    assert session.step_limits == (1,)
    assert session.step_limits == (1,)
    assert session.no_progress_limits == (1,)
    assert session.no_progress_limits == (1,)
    assert adapter.session.step_limit_reads == 1
    assert adapter.session.no_progress_limit_reads == 1
    assert session.close() is True
    assert adapter.session.closes == 1


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


class _BoundedMatrixSession:
    def __init__(self, capabilities):
        self.capabilities = capabilities
        self.step_limits = (1,) * len(capabilities)
        self.no_progress_limits = (1,) * len(capabilities)
        self.calls = []
        self.completed_capability = None
        self.completed_count = 0
        self.completed_operations = 0
        self.closes = 0

    def step(self, round_index, capability_index):
        capability = self.capabilities[capability_index]
        self.calls.append((round_index, capability))
        self.completed_capability = capability
        self.completed_count += 1
        self.completed_operations += APPLICATION_OPERATION_COUNTS[
            APPLICATION_CAPABILITIES.index(capability)]
        return STEP_DONE

    def close(self):
        self.closes += 1
        return True


class _BoundedMatrixController:
    def __init__(self):
        self.open_calls = []
        self.session = None

    def supports(self, capability):
        return capability in APPLICATION_CAPABILITIES

    def open_bounded_session(self, runtime, capabilities):
        self.open_calls.append((runtime, capabilities))
        self.session = _BoundedMatrixSession(capabilities)
        return self.session


def test_resident_bounded_controller_resolves_adapter_at_open_once():
    scenario = application_matrix()
    controller = _BoundedMatrixController()
    adapter = ResidentApplicationScenarioAdapter(controller)
    runtime = _runtime(adapter, mode="resident")

    report = run(runtime, scenario)

    assert controller.open_calls == [(runtime, APPLICATION_CAPABILITIES)]
    assert controller.session.closes == 1
    assert controller.session.calls == [
        (round_index, capability)
        for round_index in range(5)
        for capability in APPLICATION_CAPABILITIES
    ]
    assert report.rounds_completed == 5
    assert report.scenarios_completed == 35
    assert report.accepted
    for action in range(1, 8):
        verdict = adapter.verdict(action)
        assert verdict.status == PASS
        assert verdict.rounds_completed == 5
        assert (verdict.operations
                == 5 * APPLICATION_OPERATION_COUNTS[action - 1])
        assert verdict.restored


def test_resident_matrix_rejects_legacy_aggregate_controller():
    controller = _MatrixController()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter, mode="resident"),
        application_matrix(),
    )

    assert controller.calls == []
    assert report.errors == 1
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    assert not report.accepted
    for action in range(1, 8):
        verdict = adapter.verdict(action)
        assert verdict.status == UNAVAILABLE
        assert verdict.restored


def test_legacy_perform_is_explicitly_in_memory_only():
    controller = _MatrixController()
    adapter = ResidentApplicationScenarioAdapter(controller)

    with pytest.raises(ScenarioUnavailable):
        adapter.perform(
            _runtime(adapter, mode="resident"),
            ACTION_CALCULATOR_HISTORY,
            0,
        )

    assert controller.calls == []
    verdict = adapter.verdict(ACTION_CALCULATOR_HISTORY)
    assert verdict.status == UNAVAILABLE
    assert verdict.restored


def test_bounded_limit_getter_oom_closes_the_controller_transaction():
    primary = MemoryError("bounded limits OOM")

    class Session:
        def __init__(self):
            self.closes = 0

        @property
        def step_limits(self):
            raise primary

        @property
        def no_progress_limits(self):
            raise AssertionError("step limits must fail first")

        def step(self, _round_index, _capability_index):
            raise AssertionError("limit failure must not step")

        def close(self):
            self.closes += 1
            return True

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "calculator_history"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=1)[0],
    )

    assert controller.session.closes == 1
    assert report.primary_error is primary
    assert report.memory_errors == 1
    assert report.errors == 0
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    verdict = adapter.verdict(ACTION_CALCULATOR_HISTORY)
    assert verdict.status == FAILED
    assert verdict.reason == "MemoryError"
    assert verdict.restored


def test_bounded_no_progress_getter_oom_closes_the_controller_transaction():
    primary = MemoryError("bounded no-progress limits OOM")

    class Session:
        step_limits = (1,)

        def __init__(self):
            self.closes = 0

        @property
        def no_progress_limits(self):
            raise primary

        def step(self, _round_index, _capability_index):
            raise AssertionError("limit failure must not step")

        def close(self):
            self.closes += 1
            return True

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "calculator_history"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=1)[0],
    )

    assert controller.session.closes == 1
    assert report.primary_error is primary
    assert report.memory_errors == 1
    assert report.errors == 0
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    verdict = adapter.verdict(ACTION_CALCULATOR_HISTORY)
    assert verdict.status == FAILED
    assert verdict.reason == "MemoryError"
    assert verdict.restored


@pytest.mark.parametrize("reported_count", (2, True))
def test_bounded_bridge_rejects_completion_count_jump_and_bool(reported_count):
    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0
            self.completed_operations = 0

        def step(self, _round_index, _capability_index):
            self.completed_capability = "plot_pipeline"
            self.completed_count = reported_count
            self.completed_operations = 5
            return STEP_DONE

        def close(self):
            self.closes += 1
            return True

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=1)[3],
    )

    assert controller.session.closes == 1
    assert report.errors == 1
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    assert not report.accepted
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Invalid bounded completion proof"
    assert verdict.restored


@pytest.mark.parametrize(
    "proofs, rounds",
    (
        ((True,), 1),
        ((None,), 1),
        ((6,), 1),
        ((5, 4), 2),
    ),
)
def test_bounded_bridge_rejects_invalid_semantic_operation_proofs(
        proofs, rounds):
    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0

        def step(self, _round_index, _capability_index):
            self.completed_capability = "plot_pipeline"
            self.completed_count += 1
            proof = proofs[self.completed_count - 1]
            if proof is not None:
                self.completed_operations = proof
            return STEP_DONE

        def close(self):
            self.closes += 1
            return True

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=rounds)[3],
    )

    assert controller.session.closes == 1
    assert report.errors == 1
    assert not report.accepted
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Invalid bounded completion proof"
    assert verdict.restored


def test_bounded_bridge_keeps_operation_proof_oom_primary_when_close_fails():
    primary = MemoryError("bounded operation proof OOM")
    cleanup = RuntimeError("bounded restore failed")

    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0

        @property
        def completed_operations(self):
            raise primary

        def step(self, _round_index, _capability_index):
            self.completed_capability = "plot_pipeline"
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            raise cleanup

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=1)[3],
    )

    assert controller.session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is False
    assert report.primary_error is primary
    assert report.secondary_error is cleanup
    assert report.memory_errors == 1
    assert report.runtime_steps == 4
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert not verdict.restored


@pytest.mark.parametrize("invalid_limit", ("step_limits", "no_progress_limits"))
def test_bounded_bridge_rejects_bool_limit_before_runner(invalid_limit):
    class Session:
        def __init__(self):
            self.closes = 0
            self.steps = 0

        @property
        def step_limits(self):
            return (True,) if invalid_limit == "step_limits" else (1,)

        @property
        def no_progress_limits(self):
            return (
                (True,) if invalid_limit == "no_progress_limits" else (1,))

        def step(self, _round_index, _capability_index):
            self.steps += 1
            raise AssertionError("invalid limits must not step")

        def close(self):
            self.closes += 1
            return True

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "calculator_history"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=1)[0],
    )

    assert controller.session.steps == 0
    assert controller.session.closes == 1
    assert report.errors == 1
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    verdict = adapter.verdict(ACTION_CALCULATOR_HISTORY)
    assert verdict.status == FAILED
    assert verdict.reason == "Invalid bounded session limits"
    assert verdict.restored


def test_bounded_bridge_keeps_step_oom_primary_when_close_fails():
    primary = MemoryError("bounded step OOM")
    cleanup = RuntimeError("bounded restore failed")

    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0
            self.completed_operations = 0

        def step(self, _round_index, _capability_index):
            raise primary

        def close(self):
            self.closes += 1
            raise cleanup

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    scenario = application_scenarios(rounds=1)[3]
    report = run(_runtime(adapter), scenario)

    outer_session = scenario[2][0][2]
    bridge = outer_session._bound_session
    assert controller.session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is False
    assert report.primary_error is primary
    assert report.secondary_error is cleanup
    assert report.memory_errors == 1
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert not verdict.restored
    assert outer_session._bound_session is bridge
    assert bridge._controller_session is controller.session


def test_bounded_bridge_promotes_cleanup_oom_over_ordinary_step_error():
    step_error = RuntimeError("bounded step failed")
    cleanup = MemoryError("bounded restore OOM")

    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0
            self.completed_operations = 0

        def step(self, _round_index, _capability_index):
            raise step_error

        def close(self):
            self.closes += 1
            raise cleanup

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=1)[3],
    )

    assert controller.session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is False
    assert report.primary_error is cleanup
    assert report.secondary_error is step_error
    assert report.memory_errors == 1
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert not verdict.restored


def test_bounded_bridge_keeps_step_oom_primary_over_close_oom():
    primary = MemoryError("bounded step OOM")
    cleanup = MemoryError("bounded restore OOM")

    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0

        def step(self, _round_index, _capability_index):
            raise primary

        def close(self):
            self.closes += 1
            raise cleanup

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=1)[3],
    )

    assert controller.session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is False
    assert report.primary_error is primary
    assert report.secondary_error is cleanup
    assert report.memory_errors == 1
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert not verdict.restored


def test_bounded_bridge_close_only_oom_is_primary_and_not_accepted():
    cleanup = MemoryError("bounded restore OOM")

    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0
            self.completed_operations = 0

        def step(self, _round_index, _capability_index):
            self.completed_capability = "plot_pipeline"
            self.completed_count += 1
            self.completed_operations += 5
            return STEP_DONE

        def close(self):
            self.closes += 1
            raise cleanup

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=1)[3],
    )

    assert controller.session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is False
    assert report.primary_error is cleanup
    assert report.secondary_error is cleanup
    assert report.memory_errors == 1
    assert report.errors == 0
    assert report.failure_mask & FAIL_MEMORY
    assert report.rounds_completed == 1
    assert report.scenarios_completed == 1
    assert not report.accepted
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert not verdict.restored


def test_bounded_bridge_close_only_error_is_primary_and_not_accepted():
    cleanup = RuntimeError("bounded restore failed")

    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0
            self.completed_operations = 0

        def step(self, _round_index, _capability_index):
            self.completed_capability = "plot_pipeline"
            self.completed_count += 1
            self.completed_operations += 5
            return STEP_DONE

        def close(self):
            self.closes += 1
            raise cleanup

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)

    report = run(
        _runtime(adapter),
        application_scenarios(rounds=1)[3],
    )

    assert controller.session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is False
    assert report.primary_error is cleanup
    assert report.secondary_error is None
    assert report.memory_errors == 0
    assert report.errors == 1
    assert report.failure_mask & FAIL_ERROR
    assert report.rounds_completed == 1
    assert report.scenarios_completed == 1
    assert not report.accepted
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert not verdict.restored


def test_bounded_runner_retries_one_close_failure_then_releases_the_bridge():
    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.close_attempts = 0
            self.completed_capability = None
            self.completed_count = 0
            self.completed_operations = 0

        def step(self, _round_index, _capability_index):
            self.completed_capability = "plot_pipeline"
            self.completed_count += 1
            self.completed_operations += 5
            return STEP_DONE

        def close(self):
            self.close_attempts += 1
            return self.close_attempts == 2

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)
    scenario = application_scenarios(rounds=1)[3]

    report = run(_runtime(adapter), scenario)

    outer_session = scenario[2][0][2]
    assert controller.session.close_attempts == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is True
    assert report.runtime_steps == 4
    assert report.errors == 1
    assert report.primary_error is None
    assert report.secondary_error is None
    assert not report.accepted
    assert outer_session._bound_session is None
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.restored


def test_bounded_bridge_retains_false_restore_for_retry_then_releases_refs():
    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.close_attempts = 0
            self.completed_capability = None
            self.completed_count = 0
            self.completed_operations = 0

        def step(self, _round_index, _capability_index):
            raise AssertionError("close retry regression does not step")

        def close(self):
            self.close_attempts += 1
            return self.close_attempts == 2

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)
    scenario = _ApplicationScenarioSession(("plot_pipeline",))
    scenario.open(_runtime(adapter))
    bridge = scenario._bound_session

    with pytest.raises(RuntimeError, match="Scenario restore failed"):
        scenario.close()

    assert scenario._bound_session is bridge
    assert bridge._controller_session is controller.session
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert not verdict.restored

    assert scenario.close() is True
    assert controller.session.close_attempts == 2
    assert scenario._bound_session is None
    assert bridge._controller_session is None
    assert verdict.status == FAILED
    assert verdict.restored
    assert scenario.close() is True
    assert controller.session.close_attempts == 2


def test_bounded_bridge_retries_the_same_close_memory_error_then_releases_refs():
    primary = MemoryError("bounded restore OOM")

    class Session:
        step_limits = (1,)
        no_progress_limits = (1,)

        def __init__(self):
            self.close_attempts = 0
            self.completed_capability = None
            self.completed_count = 0
            self.completed_operations = 0

        def step(self, _round_index, _capability_index):
            raise AssertionError("close retry regression does not step")

        def close(self):
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise primary
            return True

    class Controller:
        def __init__(self):
            self.session = Session()

        def supports(self, capability):
            return capability == "plot_pipeline"

        def open_bounded_session(self, _runtime, _capabilities):
            return self.session

    controller = Controller()
    adapter = ResidentApplicationScenarioAdapter(controller)
    scenario = _ApplicationScenarioSession(("plot_pipeline",))
    scenario.open(_runtime(adapter))
    bridge = scenario._bound_session

    with pytest.raises(MemoryError) as caught:
        scenario.close()

    assert caught.value is primary
    assert scenario._bound_session is bridge
    assert bridge._controller_session is controller.session
    verdict = adapter.verdict(ACTION_PLOT_PIPELINE)
    assert verdict.status == FAILED
    assert verdict.reason == "Scenario restore failed"
    assert not verdict.restored

    assert scenario.close() is True
    assert controller.session.close_attempts == 2
    assert scenario._bound_session is None
    assert bridge._controller_session is None
    assert verdict.status == FAILED
    assert verdict.restored


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
    for action in range(1, 8):
        assert adapter.verdict(action).operations == 1


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
        assert adapter.verdict(action).operations == 5


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
        assert (verdict.operations
                == 5 * APPLICATION_OPERATION_COUNTS[action - 1])
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
