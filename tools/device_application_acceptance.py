"""Resident seven-capability application-matrix acceptance entry point."""
import sys


if "/sd" not in sys.path:
    sys.path.insert(0, "/sd")


TOTAL_ROUNDS = 5
_LIMITS = (
    "APPLICATION_MATRIX_LIMITS single_run_action=True "
    "transient_peak_visible=False resident_controller_required=True"
)


def _resident_runtime():
    from runtime_handle import get_resident_runtime

    return get_resident_runtime()


def _resolve_runtime(runtime):
    from runtime_handle import RuntimeHandle

    if runtime is None:
        runtime = _resident_runtime()
    if (not isinstance(runtime, RuntimeHandle)
            or getattr(runtime, "mode", None) != "resident"):
        raise RuntimeError(
            "Application matrix requires a resident RuntimeHandle")
    return runtime


def _unavailable(emit, reason, message):
    emit("APPLICATION_MATRIX_UNAVAILABLE reason=" + reason)
    emit("APPLICATION_MATRIX_RESULT FAIL")
    raise RuntimeError(message)


def _run_ready_matrix(runtime, emit):
    from runtime_acceptance import run as run_acceptance
    from runtime_scenarios import application_matrix

    report = run_acceptance(
        runtime,
        application_matrix(rounds=TOTAL_ROUNDS),
    )
    emit(
        "APPLICATION_MATRIX_END rounds_completed="
        + str(report.rounds_completed)
        + " scenarios_completed=" + str(report.scenarios_completed)
        + " memory_errors=" + str(report.memory_errors)
        + " errors=" + str(report.errors)
        + " heap_min=" + str(report.heap_min)
        + " heap_delta=" + str(report.heap_delta)
        + " blocking_max_us=" + str(report.blocking_max_us)
        + " buffer_peak_bytes=" + str(report.buffer_peak_bytes)
        + " buffer_changes=" + str(report.buffer_change_count)
    )
    emit(
        "APPLICATION_MATRIX_RESULT "
        + ("PASS" if report.accepted else "FAIL")
        + " failure_mask=" + str(report.failure_mask)
    )
    if not report.accepted:
        raise RuntimeError("Device application matrix failed")
    return report


def run(runtime=None, controller=None, emit=print):
    """Run only a bounded resident matrix; otherwise fail explicitly."""
    from runtime_scenarios import (
        APPLICATION_MATRIX_DEVICE_READY,
        ResidentApplicationScenarioAdapter,
    )

    runtime = _resolve_runtime(runtime)
    previous_adapter = runtime.scenario_adapter
    try:
        emit(_LIMITS)
        runtime.scenario_adapter = ResidentApplicationScenarioAdapter(
            controller)
        if controller is None:
            _unavailable(
                emit,
                "resident_controller_required",
                "Resident application scenario controller is unavailable",
            )
        if not APPLICATION_MATRIX_DEVICE_READY:
            _unavailable(
                emit,
                "bounded_multi_step_controller_required",
                "Application matrix requires bounded runner steps",
            )
        return _run_ready_matrix(runtime, emit)
    finally:
        runtime.scenario_adapter = previous_adapter
        runtime.reset_root(present=True)


if __name__ == "__main__":
    run()
