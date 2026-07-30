from pathlib import Path

from runtime_acceptance import STEP_DONE
from runtime_handle import (
    ApplicationBinding,
    get_resident_runtime,
    set_resident_runtime,
)
from runtime_materialize import RuntimeHandle
from runtime_scenarios import (
    APPLICATION_CAPABILITIES,
    APPLICATION_OPERATION_COUNTS,
    ResidentApplicationScenarioAdapter,
    application_matrix,
)


SOURCE = Path(__file__).parents[1] / "source"


class _PoisonedNav:
    """Fail if a bounded scenario reaches into Nav's lifecycle registry."""

    __slots__ = ()

    @property
    def _managed(self):
        raise AssertionError("bounded scenarios must not read nav._managed")


class _RepeatedDisplayTitle:
    """Legacy target discovery would read this property and fail the test."""

    __slots__ = ()

    @property
    def transition_title(self):
        raise AssertionError("bounded scenarios must not use display titles")


class _PoisonedResidentRuntime(RuntimeHandle):
    """Keep the legacy lookup API present but unusable for this contract."""

    __slots__ = ()

    def find_target(self, _name):
        raise AssertionError("bounded scenarios must not call find_target")


class _BindingOnlySession:
    __slots__ = (
        "_binding", "capabilities", "step_limits", "no_progress_limits",
        "completed_capability", "completed_count", "completed_operations",
        "closed")

    def __init__(self, binding, capabilities):
        self._binding = binding
        self.capabilities = capabilities
        self.step_limits = (1,) * len(capabilities)
        self.no_progress_limits = (1,) * len(capabilities)
        self.completed_capability = None
        self.completed_count = 0
        self.completed_operations = 0
        self.closed = False

    def step(self, round_index, capability_index):
        assert round_index == 0
        assert self._binding is not None
        capability = self.capabilities[capability_index]
        self.completed_capability = capability
        self.completed_count += 1
        self.completed_operations += APPLICATION_OPERATION_COUNTS[
            capability_index]
        return STEP_DONE

    def close(self):
        self.closed = True
        return True


class _BindingOnlyController:
    __slots__ = ("opened_runtime", "opened_binding", "session")

    def __init__(self):
        self.opened_runtime = None
        self.opened_binding = None
        self.session = None

    def supports(self, capability):
        return capability in APPLICATION_CAPABILITIES

    def open_bounded_session(self, runtime, capabilities):
        self.opened_runtime = runtime
        binding = runtime.require_application_binding()
        self.opened_binding = binding
        self.session = _BindingOnlySession(binding, capabilities)
        return self.session


def test_published_resident_binding_is_the_only_state_for_bounded_matrix():
    screens = (object(), object(), object())
    registry = object()
    settings = object()
    persistence = object()
    binding = ApplicationBinding(screens, registry, settings, persistence)
    controller = _BindingOnlyController()
    adapter = ResidentApplicationScenarioAdapter(controller)
    runtime = _PoisonedResidentRuntime(
        _PoisonedNav(),
        object(),
        (_RepeatedDisplayTitle(), _RepeatedDisplayTitle()),
        mode="resident",
        scenario_adapter=adapter,
        application_binding=binding,
    )
    previous = get_resident_runtime()
    set_resident_runtime(runtime)
    try:
        scenario_name, rounds, steps = application_matrix(rounds=1)
        session = steps[0][2]

        assert scenario_name == "application_matrix"
        assert rounds == 1
        assert all(step[2] is session for step in steps)
        assert get_resident_runtime() is runtime

        session.open(get_resident_runtime())
        try:
            for capability_index, capability in enumerate(
                    APPLICATION_CAPABILITIES):
                assert session.step(0, capability_index) == STEP_DONE
                assert session.completed_capability == capability
                assert session.completed_count == capability_index + 1
                assert session.completed_operations == sum(
                    APPLICATION_OPERATION_COUNTS[:capability_index + 1])
        finally:
            closed = session.close()
        assert closed is True

        assert controller.opened_runtime is runtime
        assert controller.opened_binding is binding
        assert controller.session.closed
        assert binding.screens is screens
        assert binding.registry is registry
        assert binding.settings is settings
        assert binding.persistence is persistence
    finally:
        set_resident_runtime(previous)


def test_production_bounded_path_has_no_legacy_navigation_or_title_lookup():
    source = (SOURCE / "runtime_scenarios.py").read_text(encoding="utf-8")
    descriptor_start = source.index("class _ApplicationScenarioSession:")
    legacy_start = source.index("class _LegacyApplicationBoundedSession:")
    adapter_start = source.index("class ResidentApplicationScenarioAdapter:")
    descriptor = source[descriptor_start:legacy_start]
    adapter = source[adapter_start:]

    for legacy_name in (
            "find_target", "_managed", "transition_title", "runtime.nav"):
        assert legacy_name not in descriptor
        assert legacy_name not in adapter

    assert "adapter = runtime.scenario_adapter" in descriptor
    assert "bound_session = opener(runtime, self.capabilities)" in descriptor
    assert "controller_session = opener(runtime, capabilities)" in adapter
