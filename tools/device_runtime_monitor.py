"""Five-round resident page tracer with a fixed device-sized state."""

import gc
import time

from ui.element import SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW


TOTAL_ROUNDS = 5
MAX_SETTLE_STEPS = 256
MAX_BLOCKING_STEP_US = 32_000
MIN_HEAP_FREE_BYTES = 4 * 1024
MAX_HEAP_DRIFT_BYTES = 512


def _resident_runtime():
    from runtime_handle import get_resident_runtime

    return get_resident_runtime()


def _buffer_state(nav):
    display = getattr(nav.renderer, "display", None)
    main = getattr(display, "gs4_buf", None)
    plot = getattr(nav.memory, "_plot_curve", None)
    return (
        len(main) if main is not None else 0,
        id(main) if main is not None else 0,
        len(plot) if plot is not None else 0,
        id(plot) if plot is not None else 0)


def _reset_root(runtime, nav, root, present):
    reset = getattr(runtime, "reset_root", None)
    if reset is not None:
        reset(present=present)
    else:
        if nav.current is not root:
            nav.reset(root)
        if present:
            nav.present_current()


def run(runtime=None, emit=print):
    if runtime is None:
        runtime = _resident_runtime()
    if runtime is None or getattr(runtime, "mode", "resident") != "resident":
        raise RuntimeError("Release mode requires a resident runtime")
    targets = getattr(runtime, "targets", None)
    if targets is None:
        targets = runtime.screens
    nav = getattr(runtime, "nav", None)
    if nav is None:
        nav = runtime._nav
    root = getattr(runtime, "root", None)
    if root is None:
        root = targets[0]
    if not targets:
        raise RuntimeError("Runtime monitor has no resident targets")
    canonical = len(targets) == 10 and root is targets[0]
    first = 1 if canonical else 0
    stop = 6 if canonical else len(targets)

    stats = [0] * 10
    failure = [None]
    _reset_root(runtime, nav, root, False)
    gc.collect()
    heap_before = gc.mem_free() if hasattr(gc, "mem_free") else -1
    stats[5] = heap_before
    baseline = _buffer_state(nav)
    stats[7] = baseline[0] + baseline[2]
    if canonical and baseline != (8192, baseline[1], 104, baseline[3]):
        stats[9] |= 32
    emit("MONITOR_START mode=resident rounds=5 heap_before="
         + str(heap_before) + " buffer_peak_bytes=" + str(stats[7]))

    def sample(started, allow_plot, target_index, phase):
        elapsed = time.ticks_diff(time.ticks_us(), started)
        free = gc.mem_free() if hasattr(gc, "mem_free") else -1
        stats[4] += 1
        if stats[5] < 0 or (free >= 0 and free < stats[5]):
            stats[5] = free
        if elapsed > stats[6]:
            stats[6] = elapsed
        if elapsed >= MAX_BLOCKING_STEP_US:
            stats[9] |= 4
            emit("MONITOR_SLOW target=" + str(target_index)
                 + " phase=" + str(phase) + " step_us=" + str(elapsed))
        state = _buffer_state(nav)
        size = state[0] + state[2]
        if size > stats[7]:
            stats[7] = size
        if state != baseline:
            stats[8] += 1
            stats[9] |= 32

    def settle(allow_plot):
        for _ in range(MAX_SETTLE_STEPS):
            started = time.ticks_us()
            try:
                flags = nav.settle_current()
                if flags & SETTLE_COLLECT:
                    gc.collect()
                if flags & SETTLE_REDRAW:
                    nav.present_current()
            except MemoryError as error:
                stats[0] += 1
                stats[9] |= 1
                failure[0] = error
                flags = 0
            except Exception as error:
                stats[1] += 1
                stats[9] |= 2
                failure[0] = error
                flags = 0
            sample(started, allow_plot, target_index, 2)
            if failure[0] is not None or not flags & SETTLE_MORE:
                return failure[0] is None
        stats[1] += 1
        stats[9] |= 2
        failure[0] = RuntimeError("Page settle work exceeded its fixed bound")
        return False

    try:
        for round_index in range(TOTAL_ROUNDS):
            for target_index in range(first, stop):
                target = targets[target_index]
                allow_plot = canonical and target_index == 2
                started = time.ticks_us()
                try:
                    nav.go_to(target)
                    nav.present_current()
                except MemoryError as error:
                    stats[0] += 1
                    stats[9] |= 1
                    failure[0] = error
                except Exception as error:
                    stats[1] += 1
                    stats[9] |= 2
                    failure[0] = error
                sample(started, allow_plot, target_index, 1)
                if failure[0] is not None or not settle(allow_plot):
                    break

                started = time.ticks_us()
                try:
                    nav.go_back()
                    nav.present_current()
                except MemoryError as error:
                    stats[0] += 1
                    stats[9] |= 1
                    failure[0] = error
                except Exception as error:
                    stats[1] += 1
                    stats[9] |= 2
                    failure[0] = error
                sample(started, False, target_index, 3)
                if failure[0] is not None or not settle(False):
                    break
                collector = getattr(nav, "collect_pending", None)
                if collector is not None:
                    started = time.ticks_us()
                    try:
                        collector()
                    except MemoryError as error:
                        stats[0] += 1
                        stats[9] |= 1
                        failure[0] = error
                    except Exception as error:
                        stats[1] += 1
                        stats[9] |= 2
                        failure[0] = error
                    sample(started, False, target_index, 4)
                if failure[0] is not None:
                    break
                stats[3] += 1
            if failure[0] is not None:
                break
            stats[2] = round_index + 1
    finally:
        _reset_root(runtime, nav, root, True)

    if failure[0] is not None:
        emit("MONITOR_ERROR " + type(failure[0]).__name__
             + " " + str(failure[0]))
        failure[0] = None
    gc.collect()
    heap_after = gc.mem_free() if hasattr(gc, "mem_free") else -1
    if stats[5] < 0 or (heap_after >= 0 and heap_after < stats[5]):
        stats[5] = heap_after
    heap_delta = (
        heap_after - heap_before
        if heap_before >= 0 and heap_after >= 0 else -1)
    if stats[5] >= 0 and stats[5] < MIN_HEAP_FREE_BYTES:
        stats[9] |= 8
    if heap_delta != -1 and abs(heap_delta) > MAX_HEAP_DRIFT_BYTES:
        stats[9] |= 16
    if _buffer_state(nav) != baseline:
        stats[9] |= 32
    if stats[2] != TOTAL_ROUNDS:
        stats[9] |= 64

    emit("MONITOR_END mode=resident rounds_completed=" + str(stats[2])
         + " scenarios_completed=" + str(stats[3])
         + " runtime_steps=" + str(stats[4])
         + " memory_errors=" + str(stats[0])
         + " errors=" + str(stats[1])
         + " heap_after=" + str(heap_after)
         + " heap_delta=" + str(heap_delta)
         + " heap_min=" + str(stats[5])
         + " blocking_max_us=" + str(stats[6])
         + " buffer_peak_bytes=" + str(stats[7])
         + " buffer_changes=" + str(stats[8]))
    emit("MONITOR_ACCEPTANCE " + ("PASS" if stats[9] == 0 else "FAIL")
         + " failure_mask=" + str(stats[9]))
    if stats[9] != 0:
        raise RuntimeError("Device runtime acceptance failed")
    return stats


if __name__ == "__main__":
    run()
