"""Five-round real-device monitor for blocking time and minimum heap."""
import gc
import sys
import time


if "/sd" not in sys.path:
    sys.path.insert(0, "/sd")

from performance import metrics
from ui.element import SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW


TOTAL_ROUND_TRIPS = 5
MAX_BLOCKING_STEP_US = 32_000
MIN_HEAP_FREE_BYTES = 8 * 1024
MAX_HEAP_DRIFT_BYTES = 4 * 1024
MAX_SETTLE_STEPS = 256


def _heap_free():
    reporter = getattr(gc, "mem_free", None)
    return reporter() if reporter is not None else -1


def _minimum(current, candidate):
    if current < 0:
        return candidate
    if candidate < 0:
        return current
    return min(current, candidate)


def _settle(nav):
    step_max = 0
    heap_min = _heap_free()
    for step in range(MAX_SETTLE_STEPS):
        started = time.ticks_us()
        flags = nav.settle_current()
        if flags & SETTLE_COLLECT:
            gc.collect()
        if flags & SETTLE_REDRAW:
            nav.present_current()
        elapsed = time.ticks_diff(time.ticks_us(), started)
        step_max = max(step_max, elapsed)
        heap_min = _minimum(heap_min, _heap_free())
        if not flags & SETTLE_MORE:
            return step + 1, step_max, heap_min
    raise RuntimeError("Page settle work exceeded its fixed bound")


def _navigate(nav, target, forward):
    started = time.ticks_us()
    if forward:
        nav.go_to(target)
    else:
        nav.go_back()
    nav.present_current()
    nav_elapsed = time.ticks_diff(time.ticks_us(), started)
    settle_steps, settle_max, heap_min = _settle(nav)
    if not forward:
        started = time.ticks_us()
        nav.collect_pending()
        settle_max = max(
            settle_max, time.ticks_diff(time.ticks_us(), started))
        heap_min = _minimum(heap_min, _heap_free())
    return nav_elapsed, settle_steps, settle_max, heap_min


def run(runtime=None):
    if runtime is None:
        runtime = metrics.runtime()
    if runtime is None:
        from benchmarks import _build_runtime
        _build_runtime(metrics)
        runtime = metrics.runtime()
    if runtime is None:
        raise RuntimeError("SCI-CALC runtime build failed")

    nav, root, targets = runtime
    if nav.current is not root:
        nav.reset(root)
        nav.present_current()
    if getattr(nav.renderer, "_visible_screen", None) is not root:
        raise RuntimeError("Boot progress 8/8 was not replaced by the root UI")

    gc.collect()
    heap_before = _heap_free()
    heap_min = heap_before
    blocking_max = 0
    failures = 0
    completed = 0
    buffers_before = tuple(sorted(nav.memory._buffers))
    print("MONITOR_START rounds=" + str(TOTAL_ROUND_TRIPS)
          + " heap_free=" + str(heap_before)
          + " heap_alloc=" + str(gc.mem_alloc())
          + " targets=" + str(len(targets)))

    for round_index in range(TOTAL_ROUND_TRIPS):
        target = targets[round_index % len(targets)]
        title = getattr(target, "transition_title",
                        target.__class__.__name__)
        try:
            if title == "Plot":
                target.expr = "x^2"
                target.input_box.set_str("x^2", immediate=True)
            forward_us, forward_steps, forward_settle_us, forward_heap = (
                _navigate(nav, target, True))
            back_us, back_steps, back_settle_us, back_heap = (
                _navigate(nav, target, False))
            if title == "Plot":
                target.expr = ""
                target.input_box.clear_str()
            completed += 1
            screen_max = max(
                forward_us, back_us, forward_settle_us, back_settle_us)
            blocking_max = max(blocking_max, screen_max)
            heap_min = _minimum(
                heap_min, _minimum(forward_heap, back_heap))
            print("MONITOR_SCREEN round=" + str(round_index + 1)
                  + " name=" + title
                  + " forward_us=" + str(forward_us)
                  + " back_us=" + str(back_us)
                  + " forward_settle_steps=" + str(forward_steps)
                  + " back_settle_steps=" + str(back_steps)
                  + " settle_max_us="
                  + str(max(forward_settle_us, back_settle_us))
                  + " heap_min=" + str(
                      _minimum(forward_heap, back_heap)))
        except MemoryError as error:
            failures += 1
            print("MONITOR_MEMORY_ERROR round=" + str(round_index + 1)
                  + " name=" + title
                  + " detail=" + (str(error) or "unknown allocation"))
            nav.reset(root)
            nav.present_current()

    if nav.current is not root:
        nav.reset(root)
        nav.present_current()
    gc.collect()
    heap_after = _heap_free()
    heap_delta = heap_after - heap_before
    buffers_after = tuple(sorted(nav.memory._buffers))
    acceptance = []
    if completed != TOTAL_ROUND_TRIPS:
        acceptance.append("completed=" + str(completed))
    if failures:
        acceptance.append("memory_errors=" + str(failures))
    if blocking_max > MAX_BLOCKING_STEP_US:
        acceptance.append("blocking_step_us=" + str(blocking_max))
    if heap_min < MIN_HEAP_FREE_BYTES:
        acceptance.append("heap_min=" + str(heap_min))
    if abs(heap_delta) > MAX_HEAP_DRIFT_BYTES:
        acceptance.append("heap_delta=" + str(heap_delta))
    if buffers_after != buffers_before:
        acceptance.append("buffer_set_changed")

    print("MONITOR_END heap_free=" + str(heap_after)
          + " heap_delta=" + str(heap_delta)
          + " heap_min=" + str(heap_min)
          + " blocking_max_us=" + str(blocking_max)
          + " failures=" + str(failures)
          + " buffers=" + ",".join(buffers_after))
    if acceptance:
        print("MONITOR_ACCEPTANCE FAIL " + ",".join(acceptance))
        raise RuntimeError("Device runtime acceptance failed")
    print("MONITOR_ACCEPTANCE PASS rounds=" + str(TOTAL_ROUND_TRIPS)
          + " blocking_max_us=" + str(blocking_max)
          + " heap_delta=" + str(heap_delta)
          + " heap_min=" + str(heap_min))


if __name__ == "__main__":
    run()
