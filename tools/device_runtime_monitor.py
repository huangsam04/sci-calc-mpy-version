"""Five-round resident page tracer over the shared acceptance runner."""

TOTAL_ROUNDS = 5


def _resident_runtime():
    from runtime_materialize import get_resident_runtime

    return get_resident_runtime()


def _run_navigation(runtime):
    from benchmarks import run as run_navigation

    return run_navigation(runtime=runtime, cycles=TOTAL_ROUNDS, emit=None)


def run(runtime=None, emit=print):
    if runtime is None:
        runtime = _resident_runtime()
    if runtime is None or getattr(runtime, "mode", None) != "resident":
        raise RuntimeError("Release mode requires a resident runtime")

    display = runtime.nav.renderer.display
    display.sleep()
    emit("MONITOR_START mode=resident rounds=5")
    try:
        report = _run_navigation(runtime)
        emit(
            "MONITOR_END mode=resident rounds_completed="
            + str(report.rounds_completed)
            + " scenarios_completed=" + str(report.scenarios_completed)
            + " runtime_steps=" + str(report.runtime_steps)
            + " memory_errors=" + str(report.memory_errors)
            + " errors=" + str(report.errors)
            + " heap_after=" + str(report.heap_after)
            + " heap_delta=" + str(report.heap_delta)
            + " heap_min=" + str(report.heap_min)
            + " blocking_max_us=" + str(report.blocking_max_us)
            + " buffer_peak_bytes=" + str(report.buffer_peak_bytes)
            + " buffer_changes=" + str(report.buffer_change_count))
        emit(
            "MONITOR_ACCEPTANCE "
            + ("PASS" if report.accepted else "FAIL")
            + " failure_mask=" + str(report.failure_mask))
        if not report.accepted:
            primary = report.primary_error
            if primary is not None:
                raise primary
            raise RuntimeError("Device runtime acceptance failed")
        return report
    finally:
        display.sleep()


if __name__ == "__main__":
    run()
