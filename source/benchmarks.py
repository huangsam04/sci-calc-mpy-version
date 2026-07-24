"""Read-only five-round navigation benchmark for the immediate UI."""
import gc
import time

from performance import metrics as _metrics
from ui.element import SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW


BENCHMARK_ROUNDS = 5
MAX_SETTLE_STEPS = 256


def _heap_free():
    reporter = getattr(gc, "mem_free", None)
    return reporter() if reporter is not None else -1


def _collect(metrics):
    started = time.ticks_us()
    gc.collect()
    elapsed = time.ticks_diff(time.ticks_us(), started)
    metrics.record_gc(elapsed)
    return elapsed


def _present(nav, metrics, started, record):
    nav.present_current()
    elapsed = time.ticks_diff(time.ticks_us(), started)
    if record:
        metrics.record_frame(elapsed)
    return elapsed


def _settle(nav, metrics, frame_pace_ms, record):
    steps = 0
    while steps < MAX_SETTLE_STEPS:
        started = time.ticks_us()
        flags = nav.settle_current()
        if flags & SETTLE_COLLECT:
            gc.collect()
        if flags & SETTLE_REDRAW:
            nav.present_current()
        elapsed = time.ticks_diff(time.ticks_us(), started)
        if record and flags:
            metrics.record_frame(elapsed)
        steps += 1
        if not flags & SETTLE_MORE:
            return steps
        if frame_pace_ms:
            time.sleep_ms(frame_pace_ms)
    raise RuntimeError("Page settle work exceeded its fixed bound")


def _navigate(nav, target, forward, metrics, frame_pace_ms, record):
    if record and forward:
        metrics.record_input()
    started = time.ticks_us()
    if forward:
        nav.go_to(target)
    else:
        nav.go_back()
    _present(nav, metrics, started, record)
    steps = _settle(nav, metrics, frame_pace_ms, record)
    collector = getattr(nav, "collect_pending", None)
    if not forward and collector is not None:
        started = time.ticks_us()
        collected = collector()
        if record and collected:
            metrics.record_frame(
                time.ticks_diff(time.ticks_us(), started))
    return steps


def _emit_report(report, emit):
    phases = ",".join(
        name + ":" + str(elapsed)
        for name, elapsed in report["boot_phases_ms"])
    emit("BENCH boot_phases_ms=" + phases)
    emit("BENCH input_to_present_p95_us="
         + str(report["input_to_present_us"]["p95_us"])
         + " input_to_present_max_us="
         + str(report["input_to_present_us"]["max_us"]))
    emit("BENCH loop_step_p95_us=" + str(report["frame_us"]["p95_us"])
         + " loop_step_max_us=" + str(report["frame_us"]["max_us"]))
    emit("BENCH heap_before=" + str(report["heap_before"])
         + " heap_after=" + str(report["heap_after"])
         + " heap_delta=" + str(report["heap_delta"]))


def _build_runtime(metrics):
    from main import main

    nav, root, targets = main(run_loop=False)
    metrics.bind_runtime(nav, root, targets)


def run(cycles=BENCHMARK_ROUNDS, frame_pace_ms=0, gc_runs=1, emit=print,
        metrics=_metrics, build_runtime=None):
    """Measure five round trips unless an explicit smaller host case is used."""
    runtime = metrics.runtime()
    if runtime is None:
        (build_runtime or _build_runtime)(metrics)
        runtime = metrics.runtime()
    if runtime is None:
        raise RuntimeError("Benchmark runtime builder did not bind navigation")
    nav, root, targets = runtime
    if not targets:
        raise RuntimeError("Benchmark runner has no navigation targets")
    if nav.current is not root:
        nav.reset(root)

    warmup_navigations = 0
    for target in targets:
        _navigate(nav, target, True, metrics, frame_pace_ms, False)
        _navigate(nav, target, False, metrics, frame_pace_ms, False)
        warmup_navigations += 2

    metrics.reset_run()
    _collect(metrics)
    heap_before = _heap_free()
    for _ in range(max(1, gc_runs) - 1):
        _collect(metrics)

    rounds = max(0, int(cycles))
    for index in range(rounds):
        target = targets[index % len(targets)]
        _navigate(nav, target, True, metrics, frame_pace_ms, True)
        _navigate(nav, target, False, metrics, frame_pace_ms, True)

    _collect(metrics)
    heap_after = _heap_free()
    report = metrics.snapshot()
    report["navigation_cycles"] = rounds
    report["warmup_navigations"] = warmup_navigations
    report["heap_before"] = heap_before
    report["heap_after"] = heap_after
    report["heap_delta"] = (heap_after - heap_before
                            if heap_before >= 0 and heap_after >= 0 else -1)
    if emit is not None:
        _emit_report(report, emit)
    return report
