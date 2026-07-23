"""Read-only per-screen runtime and heap monitor for an attached device.

Run without installing it on the calculator::

    ..\.venv\python.exe -m mpremote connect COM5 run tools/device_runtime_monitor.py
"""
import gc
import os
import sys
import time


if "/sd" not in sys.path:
    sys.path.insert(0, "/sd")

from anim.engine import (active_animation_count, animate_all,
                         cancel_all_animations, update_tmp)
from performance import metrics


TOTAL_ROUND_TRIPS = 500
FRAME_PACE_MS = 16


def _heap_free():
    reporter = getattr(gc, "mem_free", None)
    return reporter() if reporter is not None else -1


def _minimum(current, candidate):
    if current < 0:
        return candidate
    if candidate < 0:
        return current
    return min(current, candidate)


def _drive(nav):
    """Drive one navigation exactly as the main active-frame loop does."""
    frames = 0
    elapsed_total = 0
    elapsed_max = 0
    first_present_us = 0
    heap_min = _heap_free()
    animated = nav.is_transitioning()

    while nav.is_transitioning():
        animate_all()
        update_tmp()
        started = time.ticks_us()
        nav.draw_transition(time.ticks_ms())
        elapsed = time.ticks_diff(time.ticks_us(), started)
        if first_present_us == 0:
            first_present_us = elapsed
        frames += 1
        elapsed_total += elapsed
        elapsed_max = max(elapsed_max, elapsed)
        heap_min = _minimum(heap_min, _heap_free())
        if nav.is_transitioning() and FRAME_PACE_MS:
            time.sleep_ms(FRAME_PACE_MS)

    # Continue through the real post-transition lifecycle. This includes page
    # SWAP encode/write/read, progressive menu rows, plot workspace rebuild,
    # curve sampling, and the curve reveal animation.
    settling = True
    while settling or active_animation_count():
        was_active = bool(active_animation_count())
        animate_all()
        update_tmp()
        started = time.ticks_us()
        if was_active:
            nav.present_current()
            settling = True
        else:
            settling = nav.settle_current()
        elapsed = time.ticks_diff(time.ticks_us(), started)
        if first_present_us == 0:
            first_present_us = elapsed
        frames += 1
        elapsed_total += elapsed
        elapsed_max = max(elapsed_max, elapsed)
        heap_min = _minimum(heap_min, _heap_free())
        if (settling or active_animation_count()) and FRAME_PACE_MS:
            time.sleep_ms(FRAME_PACE_MS)

    nav.restore_optional_resources()
    heap_min = _minimum(heap_min, _heap_free())
    return (animated, frames, elapsed_total, elapsed_max, heap_min,
            first_present_us)


def _exercise(nav, root, target, cycles):
    gc.collect()
    heap_before = _heap_free()
    heap_min = heap_before
    nav_elapsed_max = 0
    input_to_first_max = 0
    frame_elapsed_max = 0
    frame_elapsed_total = 0
    frame_count = 0
    animated_frame_max = 0
    animated_frame_total = 0
    animated_frame_count = 0
    direct_frame_max = 0
    direct_frame_total = 0
    direct_frame_count = 0
    animated_count = 0
    direct_count = 0
    failures = 0

    for _ in range(cycles):
        for forward in (True, False):
            try:
                started = time.ticks_us()
                if forward:
                    nav.go_to(target)
                else:
                    nav.go_back()
                nav_elapsed = time.ticks_diff(time.ticks_us(), started)
                nav_elapsed_max = max(nav_elapsed_max, nav_elapsed)
                heap_min = _minimum(heap_min, _heap_free())

                (animated, frames, elapsed_total, elapsed_max, drive_heap,
                 first_present_us) = _drive(nav)
                input_to_first_max = max(
                    input_to_first_max, nav_elapsed + first_present_us)
                if animated:
                    animated_count += 1
                    animated_frame_count += frames
                    animated_frame_total += elapsed_total
                    animated_frame_max = max(animated_frame_max, elapsed_max)
                else:
                    direct_count += 1
                    direct_frame_count += frames
                    direct_frame_total += elapsed_total
                    direct_frame_max = max(direct_frame_max, elapsed_max)
                frame_count += frames
                frame_elapsed_total += elapsed_total
                frame_elapsed_max = max(frame_elapsed_max, elapsed_max)
                heap_min = _minimum(heap_min, drive_heap)
            except MemoryError as error:
                failures += 1
                print("MONITOR_MEMORY_ERROR screen="
                      + target.__class__.__name__ + " error=" + str(error))
                nav.reset(root)

    cancel_all_animations()
    gc.collect()
    heap_after = _heap_free()
    average = frame_elapsed_total // max(1, frame_count)
    animated_average = animated_frame_total // max(1, animated_frame_count)
    direct_average = direct_frame_total // max(1, direct_frame_count)
    buffers = ",".join(sorted(nav.memory._buffers.keys()))
    print("MONITOR_SCREEN name=" + target.__class__.__name__
          + " cycles=" + str(cycles)
          + " animated=" + str(animated_count)
          + " direct=" + str(direct_count)
          + " nav_max_us=" + str(nav_elapsed_max)
          + " input_to_first_max_us=" + str(input_to_first_max)
          + " frame_avg_us=" + str(average)
          + " frame_max_us=" + str(frame_elapsed_max)
          + " animated_frame_avg_us=" + str(animated_average)
          + " animated_frame_max_us=" + str(animated_frame_max)
          + " direct_frame_avg_us=" + str(direct_average)
          + " direct_frame_max_us=" + str(direct_frame_max)
          + " heap_before=" + str(heap_before)
          + " heap_min=" + str(heap_min)
          + " heap_after=" + str(heap_after)
          + " heap_delta=" + str(heap_after - heap_before)
          + " failures=" + str(failures)
          + " animations_left=" + str(active_animation_count())
          + " buffers=" + buffers)
    return failures


def run():
    runtime = metrics.runtime()
    if runtime is None:
        import benchmarks
        benchmarks._build_runtime(metrics)
        runtime = metrics.runtime()
    if runtime is None:
        raise RuntimeError("SCI-CALC runtime is unavailable")

    nav, root, targets = runtime
    if nav.current is not root:
        nav.reset(root)
    cancel_all_animations()
    gc.collect()
    heap_before = _heap_free()
    stat = os.statvfs("/sd")
    print("MONITOR_START heap_free=" + str(heap_before)
          + " heap_alloc=" + str(gc.mem_alloc())
          + " sd_free=" + str(stat[0] * stat[3])
          + " targets=" + str(len(targets)))

    failures = 0
    base_cycles = TOTAL_ROUND_TRIPS // max(1, len(targets))
    remainder = TOTAL_ROUND_TRIPS % max(1, len(targets))
    for index, target in enumerate(targets):
        cycles = base_cycles + (1 if index < remainder else 0)
        failures += _exercise(nav, root, target, cycles)

    if nav.current is not root:
        nav.reset(root)
    cancel_all_animations()
    gc.collect()
    heap_after = _heap_free()
    print("MONITOR_END heap_free=" + str(heap_after)
          + " heap_alloc=" + str(gc.mem_alloc())
          + " heap_delta=" + str(heap_after - heap_before)
          + " failures=" + str(failures)
          + " animations_left=" + str(active_animation_count()))


run()
