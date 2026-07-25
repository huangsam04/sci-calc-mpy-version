"""Device Adapter for the five-round resident-target acceptance tracer."""
import sys


if "/sd" not in sys.path:
    sys.path.insert(0, "/sd")


TOTAL_ROUNDS = 5
MODE_RELEASE = "release"
MODE_BENCHMARK = "benchmark"
SCENARIO_NAME = "resident_target_tracer"


def _resident_runtime():
    from runtime_acceptance import get_resident_runtime

    return get_resident_runtime()


def _benchmark_runtime():
    from benchmarks import build_runtime

    return build_runtime(mode=MODE_BENCHMARK)


def _resolve_runtime(runtime, mode):
    if mode not in (MODE_RELEASE, MODE_BENCHMARK):
        raise ValueError("Unknown runtime monitor mode: " + str(mode))
    if runtime is None:
        runtime = (
            _resident_runtime()
            if mode == MODE_RELEASE
            else _benchmark_runtime())
    if runtime is None:
        if mode == MODE_RELEASE:
            raise RuntimeError("Release mode requires a resident runtime")
        raise RuntimeError("Benchmark runtime build failed")

    runtime_mode = getattr(runtime, "mode", None)
    expected_mode = "resident" if mode == MODE_RELEASE else MODE_BENCHMARK
    if runtime_mode != expected_mode:
        raise RuntimeError(
            mode + " mode requires a " + expected_mode + " runtime")
    if not getattr(runtime, "targets", ()):
        raise RuntimeError("Runtime monitor has no resident targets")
    return runtime


def _target_name(target):
    return getattr(
        target, "transition_title", target.__class__.__name__)


def _scenario(runtime):
    from runtime_acceptance import VISIT_TARGET

    steps = []
    for index, target in enumerate(runtime.targets):
        steps.append((_target_name(target), VISIT_TARGET, index))
    return (SCENARIO_NAME, TOTAL_ROUNDS, tuple(steps))


def _buffer_text(buffers):
    if not buffers:
        return "-"
    return ";".join(
        name + ":" + str(length) + ":" + str(identity)
        for name, length, identity in buffers)


def _emit_start(report, emit):
    emit("MONITOR_START mode=" + report.mode
         + " rounds=" + str(report.rounds_expected)
         + " heap_before=" + str(report.heap_before)
         + " buffers=" + _buffer_text(report.buffers_before))


def _emit_step(event, report, emit):
    emit("MONITOR_STEP event=" + str(event)
         + " round=" + str(report.round_index + 1)
         + " name=" + str(report.step_name)
         + " phase=" + str(report.phase)
         + " step_us=" + str(report.step_us)
         + " heap_free=" + str(report.step_heap_free)
         + " heap_min=" + str(report.heap_min)
         + " buffers=" + _buffer_text(report.step_buffers))


def _emit_end(report, emit):
    emit("MONITOR_END mode=" + report.mode
         + " rounds_completed=" + str(report.rounds_completed)
         + " scenarios_completed=" + str(report.scenarios_completed)
         + " runtime_steps=" + str(report.runtime_steps)
         + " memory_errors=" + str(report.memory_errors)
         + " errors=" + str(report.errors)
         + " heap_after=" + str(report.heap_after)
         + " heap_delta=" + str(report.heap_delta)
         + " heap_min=" + str(report.heap_min)
         + " blocking_max_us=" + str(report.blocking_max_us)
         + " buffer_peak_bytes=" + str(report.buffer_peak_bytes)
         + " buffer_changes=" + str(report.buffer_change_count)
         + " buffers_before=" + _buffer_text(report.buffers_before)
         + " buffers_after=" + _buffer_text(report.buffers_after))
    emit("MONITOR_ACCEPTANCE "
         + ("PASS" if report.accepted else "FAIL")
         + " failure_mask=" + str(report.failure_mask))


def _observer(emit):
    from runtime_acceptance import (
        RUN_END, RUN_ERROR, RUN_MEMORY_ERROR, RUN_START, RUN_STEP)

    def observe(event, report):
        if event == RUN_START:
            _emit_start(report, emit)
        elif event in (RUN_STEP, RUN_MEMORY_ERROR, RUN_ERROR):
            _emit_step(event, report, emit)
        elif event == RUN_END:
            _emit_end(report, emit)

    return observe


def run(runtime=None, mode=MODE_RELEASE, emit=print):
    """Run the target tracer without claiming the full seven-scenario gate."""
    from runtime_acceptance import run as run_acceptance

    runtime = _resolve_runtime(runtime, mode)
    report = None
    try:
        report = run_acceptance(
            runtime, _scenario(runtime), _observer(emit))
    finally:
        runtime.reset_root(present=True)
    if not report.accepted:
        raise RuntimeError("Device runtime acceptance failed")
    return report


if __name__ == "__main__":
    run()
