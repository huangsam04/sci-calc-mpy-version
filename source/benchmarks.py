"""Read-only synthetic navigation benchmark for the SCI-CALC UI."""
import gc
import time

from anim.engine import (
    active_animation_count, animate_all, cancel_all_animations)
from performance import metrics as _metrics


def _heap_free():
    free = getattr(gc, "mem_free", None)
    return free() if free is not None else -1


def _collect(metrics):
    started = time.ticks_us()
    gc.collect()
    elapsed = time.ticks_diff(time.ticks_us(), started)
    metrics.record_gc(elapsed)
    return elapsed


def _drive_transition(nav, metrics, frame_pace_ms, record=True):
    rendered = False
    while nav.is_transitioning():
        started = time.ticks_us()
        nav.draw_transition(time.ticks_ms())
        rendered = True
        if record:
            metrics.record_frame(time.ticks_diff(time.ticks_us(), started))
        if nav.is_transitioning() and frame_pace_ms:
            time.sleep_ms(frame_pace_ms)
    if not rendered:
        # Direct fallback is a real navigation path too.  Present it before
        # asking for optional layers, exactly as the main loop does.
        started = time.ticks_us()
        nav.present_current()
        if record:
            metrics.record_frame(time.ticks_diff(time.ticks_us(), started))
    settling = True
    while settling or active_animation_count():
        was_active = bool(active_animation_count())
        animate_all()
        started = time.ticks_us()
        if was_active:
            nav.present_current()
            settling = True
        else:
            settling = nav.settle_current()
        if record:
            metrics.record_frame(time.ticks_diff(time.ticks_us(), started))
        if (settling or active_animation_count()) and frame_pace_ms:
            time.sleep_ms(frame_pace_ms)
    # Plot/function-panel exits release resources intentionally.  Model the
    # next quiet-loop turn so a later normal page can regain animation.
    nav.restore_optional_resources()


def _emit_report(report, emit):
    phases = ",".join(
        name + ":" + str(elapsed)
        for name, elapsed in report["boot_phases_ms"])
    emit("BENCH boot_phases_ms=" + phases)
    emit("BENCH nav_event_p95_us="
         + str(report["input_to_present_us"]["p95_us"])
         + " nav_event_max_us="
         + str(report["input_to_present_us"]["max_us"]))
    emit("BENCH frame_p95_us=" + str(report["frame_us"]["p95_us"])
         + " frame_max_us=" + str(report["frame_us"]["max_us"]))
    emit("BENCH gc_p95_us=" + str(report["gc_us"]["p95_us"])
         + " gc_max_us=" + str(report["gc_us"]["max_us"]))
    emit("BENCH heap_before=" + str(report["heap_before"])
         + " heap_after=" + str(report["heap_after"])
         + " heap_delta=" + str(report["heap_delta"]))


def _build_runtime(metrics):
    """Reuse the production LazyScreen graph without entering its main loop."""
    from main import main

    nav, root, targets = main(run_loop=False)
    # A caller may supply an isolated metrics recorder in host tests. The
    # production builder binds its module singleton, so mirror that binding
    # onto the requested recorder without rebuilding the screen graph.
    metrics.bind_runtime(nav, root, targets)
    nav.present_current()
    nav.mark_first_frame_presented()
    nav.restore_optional_resources()


def run(cycles=50, frame_pace_ms=16, gc_runs=3, emit=print,
        metrics=_metrics, build_runtime=None):
    """Measure synthetic repeated navigation without changing user state."""
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
    cancel_all_animations()

    # Load each target and populate its bounded caches before sampling heap
    # stability. This keeps one-time font/module allocations out of the result.
    warmup_transitions = 0
    for target in targets:
        nav.go_to(target)
        _drive_transition(nav, metrics, frame_pace_ms, record=False)
        nav.go_back()
        _drive_transition(nav, metrics, frame_pace_ms, record=False)
        warmup_transitions += 2

    metrics.reset_run()

    _collect(metrics)
    heap_before = _heap_free()
    for _ in range(max(1, gc_runs) - 1):
        _collect(metrics)

    for index in range(max(0, cycles)):
        target = targets[index % len(targets)]
        metrics.record_input()
        nav.go_to(target)
        _drive_transition(nav, metrics, frame_pace_ms)
        nav.go_back()
        _drive_transition(nav, metrics, frame_pace_ms)

    cancel_all_animations()
    _collect(metrics)
    heap_after = _heap_free()
    report = metrics.snapshot()
    report["navigation_cycles"] = max(0, cycles)
    report["warmup_transitions"] = warmup_transitions
    report["heap_before"] = heap_before
    report["heap_after"] = heap_after
    report["heap_delta"] = (heap_after - heap_before
                            if heap_before >= 0 and heap_after >= 0 else -1)
    if emit is not None:
        _emit_report(report, emit)
    return report
