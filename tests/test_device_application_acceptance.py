import sys
from pathlib import Path

import pytest

from runtime_handle import RuntimeHandle


TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import device_application_acceptance


class _Nav:
    def __init__(self, root):
        self.current = object()
        self.root = root
        self.present_count = 0
        self.reset_count = 0
        self.renderer = type("Renderer", (), {"display": None})()
        self.memory = type("Memory", (), {"_buffers": {}})()

    def reset(self, root):
        self.current = root
        self.reset_count += 1

    def present_current(self):
        self.present_count += 1


def _runtime(adapter):
    root = object()
    nav = _Nav(root)
    return RuntimeHandle(
        nav,
        root,
        (),
        mode="resident",
        scenario_adapter=adapter,
    )


def test_device_matrix_without_controller_is_unavailable_and_restores_runtime():
    original_adapter = object()
    runtime = _runtime(original_adapter)
    lines = []

    with pytest.raises(RuntimeError, match="controller"):
        device_application_acceptance.run(
            runtime=runtime,
            controller=None,
            emit=lines.append,
        )

    assert lines == [
        "APPLICATION_MATRIX_LIMITS single_run_action=True "
        "transient_peak_visible=False resident_controller_required=True",
        "APPLICATION_MATRIX_UNAVAILABLE reason=resident_controller_required",
        "APPLICATION_MATRIX_RESULT FAIL",
    ]
    assert runtime.scenario_adapter is original_adapter
    assert runtime.nav.current is runtime.root
    assert runtime.nav.reset_count == 1
    assert runtime.nav.present_count == 1


class _CompleteController:
    def __init__(self):
        self.calls = 0

    def supports(self, capability):
        return True

    def snapshot(self, capability):
        self.calls += 1
        return None

    def perform(self, runtime, capability, round_index):
        self.calls += 1
        return 1

    def restore(self, capability, snapshot):
        self.calls += 1
        return True


def test_device_matrix_rejects_complete_controller_until_steps_are_bounded():
    original_adapter = object()
    runtime = _runtime(original_adapter)
    controller = _CompleteController()
    lines = []

    with pytest.raises(RuntimeError, match="bounded runner steps"):
        device_application_acceptance.run(
            runtime=runtime,
            controller=controller,
            emit=lines.append,
        )

    assert lines == [
        "APPLICATION_MATRIX_LIMITS single_run_action=True "
        "transient_peak_visible=False resident_controller_required=True",
        "APPLICATION_MATRIX_UNAVAILABLE "
        "reason=bounded_multi_step_controller_required",
        "APPLICATION_MATRIX_RESULT FAIL",
    ]
    assert controller.calls == 0
    assert runtime.scenario_adapter is original_adapter
    assert runtime.nav.current is runtime.root
    assert runtime.nav.reset_count == 1
    assert runtime.nav.present_count == 1


@pytest.mark.parametrize(
    "runtime",
    (
        type("ResidentImpostor", (), {"mode": "resident"})(),
        RuntimeHandle(_Nav(object()), object(), (), mode="benchmark"),
    ),
)
def test_device_matrix_accepts_only_a_resident_runtime_handle(runtime):
    lines = []

    with pytest.raises(RuntimeError, match="resident RuntimeHandle"):
        device_application_acceptance.run(
            runtime=runtime,
            controller=_CompleteController(),
            emit=lines.append,
        )

    assert lines == []


def test_device_matrix_resets_root_when_initial_output_fails():
    original_adapter = object()
    runtime = _runtime(original_adapter)

    def fail_emit(_line):
        raise RuntimeError("injected emit failure")

    with pytest.raises(RuntimeError, match="injected emit failure"):
        device_application_acceptance.run(
            runtime=runtime,
            controller=_CompleteController(),
            emit=fail_emit,
        )

    assert runtime.scenario_adapter is original_adapter
    assert runtime.nav.current is runtime.root
    assert runtime.nav.reset_count == 1
    assert runtime.nav.present_count == 1
