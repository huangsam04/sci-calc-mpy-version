"""Navigation benchmark Adapter over the shared runtime acceptance seam."""

from performance import metrics as _metrics
from runtime_acceptance import (
    PHASE_ENTER,
    RUN_STEP,
    VISIT_TARGET,
    RuntimeHandle,
    get_resident_runtime,
    run as run_acceptance,
)


BENCHMARK_ROUNDS = 5
SCENARIO_NAME = "navigation"


class _MetricsObserver:
    """Translate acceptance events into bounded PerformanceMetrics samples."""

    __slots__ = ("metrics",)

    def __init__(self, metrics):
        self.metrics = metrics

    def __call__(self, event, report):
        if event != RUN_STEP:
            return
        self.metrics.record_frame(report.step_us)
        if report.phase == PHASE_ENTER:
            self.metrics.record_input_to_present(report.step_us)


def _target_name(target):
    return getattr(
        target, "transition_title", target.__class__.__name__)


def navigation_scenario(runtime, rounds=BENCHMARK_ROUNDS):
    """Build one immutable matrix; every round visits every target."""
    if not runtime.targets:
        raise RuntimeError("Benchmark runner has no navigation targets")
    steps = []
    for index, target in enumerate(runtime.targets):
        steps.append((_target_name(target), VISIT_TARGET, index))
    return (SCENARIO_NAME, max(0, int(rounds)), tuple(steps))


def _emit_report(report, metrics_report, emit):
    phases = ",".join(
        name + ":" + str(elapsed)
        for name, elapsed in metrics_report["boot_phases_ms"])
    emit("BENCH boot_phases_ms=" + phases)
    emit("BENCH input_to_present_p95_us="
         + str(metrics_report["input_to_present_us"]["p95_us"])
         + " input_to_present_max_us="
         + str(metrics_report["input_to_present_us"]["max_us"]))
    emit("BENCH loop_step_p95_us="
         + str(metrics_report["frame_us"]["p95_us"])
         + " loop_step_max_us="
         + str(metrics_report["frame_us"]["max_us"]))
    emit("BENCH heap_before=" + str(report.heap_before)
         + " heap_after=" + str(report.heap_after)
         + " heap_delta=" + str(report.heap_delta)
         + " heap_min=" + str(report.heap_min)
         + " blocking_max_us=" + str(report.blocking_max_us))
    emit("BENCH acceptance=" + ("PASS" if report.accepted else "FAIL")
         + " failure_mask=" + str(report.failure_mask))


def build_runtime(mode="benchmark"):
    """Construct an explicit non-resident runtime for intentional benchmarks."""
    from main import main

    runtime = main(
        run_loop=False, runtime_mode=mode, publish_runtime=False)
    if not isinstance(runtime, RuntimeHandle):
        raise RuntimeError("Benchmark runtime builder returned no handle")
    if runtime.mode != mode:
        raise RuntimeError("Benchmark runtime mode mismatch")
    return runtime


def _build_warmup_view(runtime):
    if runtime.mode not in ("resident", "release"):
        return runtime
    return RuntimeHandle(
        runtime.nav,
        runtime.root,
        runtime.targets,
        mode="benchmark",
        version=runtime.version,
        optional_buffers=runtime.optional_buffers,
        optional_buffer_target=runtime.optional_buffer_target,
        scenario_adapter=runtime.scenario_adapter,
    )


def run(runtime=None, cycles=BENCHMARK_ROUNDS, emit=print, metrics=_metrics,
        frame_pace_ms=0, gc_runs=1, build_runtime=None):
    """Warm once, then measure a complete target matrix for every round."""
    cycles = int(cycles)
    if cycles <= 0:
        raise ValueError("Benchmark cycles must be positive")
    if frame_pace_ms:
        raise ValueError("Frame pacing is not part of runtime acceptance")
    if int(gc_runs) != 1:
        raise ValueError("Runtime acceptance owns its GC baseline")

    if runtime is None and build_runtime is not None:
        runtime = build_runtime()
    if runtime is None:
        runtime = get_resident_runtime()
    if runtime is None:
        runtime = globals()["build_runtime"]()
    if not isinstance(runtime, RuntimeHandle):
        raise TypeError("Benchmark runtime must be a RuntimeHandle")
    if runtime.mode in ("resident", "release") and cycles != BENCHMARK_ROUNDS:
        raise ValueError(
            "Resident/release benchmark requires exactly 5 cycles")

    warmup_runtime = _build_warmup_view(runtime)
    warmup_report = run_acceptance(
        warmup_runtime, navigation_scenario(warmup_runtime, 1))
    if not warmup_report.accepted:
        raise RuntimeError("Benchmark warmup acceptance failed")
    del warmup_report
    del warmup_runtime
    metrics.reset_run()
    report = run_acceptance(
        runtime,
        navigation_scenario(runtime, cycles),
        _MetricsObserver(metrics),
    )
    metrics_report = metrics.snapshot()
    if emit is not None:
        _emit_report(report, metrics_report, emit)
    return report
