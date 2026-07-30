import pytest

from runtime_acceptance import STEP_DONE, STEP_MORE
from runtime_scenarios import (
    APPLICATION_CAPABILITIES,
    APPLICATION_OPERATION_COUNTS,
)
from runtime_scenarios_host import (
    _InMemoryScenarioController,
    _PLUGIN_ALL_MASK,
    _PLUGIN_VALID_MASK,
)


_EXPECTED_STEP_LIMITS = (61, 40, 101, 326, 17, 61, 19)
_EXPECTED_LEGACY_OPERATIONS = APPLICATION_OPERATION_COUNTS


def _advance_capability(session, round_index, capability_index):
    limit = session.step_limits[capability_index]
    for step_index in range(limit):
        status = session.step(round_index, capability_index)
        if step_index + 1 == limit:
            assert status == STEP_DONE
        else:
            assert status == STEP_MORE


def test_host_bounded_session_runs_five_ordered_rounds_with_fixed_primitives():
    controller = _InMemoryScenarioController()
    session = controller.open_bounded_session(None, APPLICATION_CAPABILITIES)

    assert session.capabilities == APPLICATION_CAPABILITIES
    assert session.step_limits == _EXPECTED_STEP_LIMITS
    assert session.no_progress_limits == (1, 1, 1, 1, 1, 1, 1)
    with pytest.raises(AttributeError):
        session.step_limits = ()
    with pytest.raises(AttributeError):
        session.no_progress_limits = ()

    completed = 0
    for round_index in range(5):
        for capability_index, capability in enumerate(APPLICATION_CAPABILITIES):
            _advance_capability(session, round_index, capability_index)
            completed += 1
            assert session.completed_capability == capability
            assert session.completed_count == completed

    assert session.close() is True


def test_host_bounded_session_proves_expected_operations_per_completion():
    controller = _InMemoryScenarioController()
    session = controller.open_bounded_session(None, APPLICATION_CAPABILITIES)

    expected_operations = 0
    for capability_index, operations in enumerate(_EXPECTED_LEGACY_OPERATIONS):
        _advance_capability(session, 0, capability_index)
        expected_operations += operations
        assert session.completed_operations == expected_operations

    assert session.close() is True


def test_host_bounded_single_capability_uses_its_canonical_operation_total():
    controller = _InMemoryScenarioController()

    for capability, operations in zip(
            APPLICATION_CAPABILITIES, _EXPECTED_LEGACY_OPERATIONS):
        session = controller.open_bounded_session(None, (capability,))
        _advance_capability(session, 0, 0)
        assert session.completed_operations == operations
        assert session.close() is True


def test_host_bounded_session_restores_its_one_snapshot_after_step_failure():
    controller = _InMemoryScenarioController()
    session = controller.open_bounded_session(None, APPLICATION_CAPABILITIES)

    assert session.step(0, 0) == STEP_MORE
    assert controller.history == []
    with pytest.raises(RuntimeError, match="order changed"):
        session.step(0, 1)

    assert session.close() is True
    assert controller.history == [("seed", "0")]
    assert controller.variables == {"seed": 7}
    assert controller.durable_variables == {"seed": 7}
    assert controller.plot_workspace is None
    assert controller.page_stack == ["root"]


def test_host_bounded_session_uses_one_snapshot_and_one_restore_without_compare():
    class TrackingController(_InMemoryScenarioController):
        def __init__(self):
            _InMemoryScenarioController.__init__(self)
            self.snapshot_calls = 0
            self.restore_calls = 0

        def snapshot(self, capability):
            snapshot = _InMemoryScenarioController.snapshot(self, capability)
            self.snapshot_calls += 1
            return snapshot

        def _restore_snapshot(self, snapshot):
            self.restore_calls += 1
            return _InMemoryScenarioController._restore_snapshot(self, snapshot)

    controller = TrackingController()
    session = controller.open_bounded_session(
        None,
        ("calculator_history",),
    )

    assert controller.snapshot_calls == 1
    assert session.close() is True
    assert controller.snapshot_calls == 1
    assert controller.restore_calls == 1
    assert session.close() is True
    assert controller.restore_calls == 1


def test_host_bounded_close_retains_snapshot_until_restore_succeeds():
    class RetryRestoreController(_InMemoryScenarioController):
        def __init__(self):
            _InMemoryScenarioController.__init__(self)
            self.restore_calls = 0

        def _restore_snapshot(self, snapshot):
            self.restore_calls += 1
            if self.restore_calls == 1:
                raise RuntimeError("transient restore failure")
            return _InMemoryScenarioController._restore_snapshot(self, snapshot)

    controller = RetryRestoreController()
    session = controller.open_bounded_session(
        None,
        ("calculator_history",),
    )

    assert session.step(0, 0) == STEP_MORE
    assert controller.history == []
    with pytest.raises(RuntimeError, match="transient restore failure"):
        session.close()
    with pytest.raises(RuntimeError, match="transaction is closed"):
        session.step(0, 0)
    with pytest.raises(RuntimeError, match="already open"):
        controller.open_bounded_session(None, ("error_lifecycle",))

    assert session.close() is True
    assert controller.restore_calls == 2
    assert controller.history == [("seed", "0")]
    assert session.close() is True
    assert controller.restore_calls == 2


def test_host_bounded_controller_rejects_a_second_snapshot_until_close():
    class TrackingController(_InMemoryScenarioController):
        def __init__(self):
            _InMemoryScenarioController.__init__(self)
            self.snapshot_calls = 0

        def snapshot(self, capability):
            snapshot = _InMemoryScenarioController.snapshot(self, capability)
            self.snapshot_calls += 1
            return snapshot

    controller = TrackingController()
    first = controller.open_bounded_session(
        None,
        ("calculator_history",),
    )

    with pytest.raises(RuntimeError, match="already open"):
        controller.open_bounded_session(None, ("error_lifecycle",))
    with pytest.raises(RuntimeError, match="already open"):
        controller.snapshot("error_lifecycle")
    assert controller.snapshot_calls == 1

    assert first.close() is True
    second = controller.open_bounded_session(None, ("error_lifecycle",))
    assert controller.snapshot_calls == 2
    assert second.close() is True


def test_host_bounded_plugin_stage_rejects_missing_dependency_before_commit():
    controller = _InMemoryScenarioController()
    session = controller.open_bounded_session(None, ("plugin_reload",))

    controller.plugins_enabled.clear()
    controller.plugins_enabled.update(("core", "helper", "dependent", "broken"))
    controller.plugin_catalog.clear()
    controller.plugin_catalog.update({
        "core": (),
        "helper": (),
        "dependent": ("helper",),
        "broken": ("missing",),
    })
    controller.plugin_live = ("committed",)
    controller.plugin_revision = 41

    assert session._stage_plugin_candidate() is False
    assert session._plugin_candidate_mask == _PLUGIN_ALL_MASK
    assert session._plugin_stage_mask == _PLUGIN_VALID_MASK
    assert controller.plugin_live == ("committed",)
    assert controller.plugin_revision == 41
    assert session.close() is True
