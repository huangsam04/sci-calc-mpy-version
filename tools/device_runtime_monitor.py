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


TOTAL_ROUND_TRIPS = 10
FRAME_PACE_MS = 16
MAX_FIRST_FRAME_US = 32000
MAX_ANIMATION_FRAME_US = 16000
MIN_TRANSITION_FRAMES = 12
MAX_HEAP_DRIFT_BYTES = 512


def _heap_free():
    reporter = getattr(gc, "mem_free", None)
    return reporter() if reporter is not None else -1


def _minimum(current, candidate):
    if current < 0:
        return candidate
    if candidate < 0:
        return current
    return min(current, candidate)


def _pace_frame(started):
    if not FRAME_PACE_MS:
        return
    elapsed_us = max(0, time.ticks_diff(time.ticks_us(), started))
    remaining_ms = FRAME_PACE_MS - ((elapsed_us + 999) // 1000)
    if remaining_ms > 0:
        time.sleep_ms(remaining_ms)


def _drive(nav):
    """Drive one navigation exactly as the main active-frame loop does."""
    frames = 0
    elapsed_total = 0
    elapsed_max = 0
    first_present_us = 0
    motion_frames = 0
    motion_elapsed_total = 0
    motion_elapsed_max = 0
    transition_frames = 0
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
        motion_frames += 1
        transition_frames += 1
        elapsed_total += elapsed
        motion_elapsed_total += elapsed
        elapsed_max = max(elapsed_max, elapsed)
        motion_elapsed_max = max(motion_elapsed_max, elapsed)
        heap_min = _minimum(heap_min, _heap_free())
        if nav.is_transitioning():
            _pace_frame(started)

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
        if was_active:
            motion_frames += 1
            motion_elapsed_total += elapsed
            motion_elapsed_max = max(motion_elapsed_max, elapsed)
        elapsed_total += elapsed
        elapsed_max = max(elapsed_max, elapsed)
        heap_min = _minimum(heap_min, _heap_free())
        if settling or active_animation_count():
            _pace_frame(started)

    nav.restore_optional_resources()
    heap_min = _minimum(heap_min, _heap_free())
    return (animated, frames, elapsed_total, elapsed_max, heap_min,
            first_present_us, motion_frames, motion_elapsed_total,
            motion_elapsed_max, transition_frames)


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
    transition_frames_min = -1
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
                 first_present_us, motion_frames, motion_elapsed_total,
                 motion_elapsed_max, transition_frames) = _drive(nav)
                input_to_first_max = max(
                    input_to_first_max, nav_elapsed + first_present_us)
                transition_frames_min = (
                    transition_frames if transition_frames_min < 0
                    else min(transition_frames_min, transition_frames))
                if animated:
                    animated_count += 1
                    animated_frame_count += motion_frames
                    animated_frame_total += motion_elapsed_total
                    animated_frame_max = max(
                        animated_frame_max, motion_elapsed_max)
                else:
                    direct_count += 1
                    direct_frame_count += frames
                    direct_frame_total += elapsed_total
                    direct_frame_max = max(direct_frame_max, elapsed_max)
                frame_count += frames
                frame_elapsed_total += elapsed_total
                frame_elapsed_max = max(frame_elapsed_max, elapsed_max)
                heap_min = _minimum(heap_min, drive_heap)
            except MemoryError:
                failures += 1
                print("MONITOR_MEMORY_ERROR")
                nav.reset(root)

    cancel_all_animations()
    gc.collect()
    heap_after = _heap_free()
    average = frame_elapsed_total // max(1, frame_count)
    animated_average = animated_frame_total // max(1, animated_frame_count)
    direct_average = direct_frame_total // max(1, direct_frame_count)
    buffers = ",".join(sorted(nav.memory._buffers.keys()))
    print("MONITOR_SCREEN name="
          + getattr(target, "transition_title", target.__class__.__name__)
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
          + " transition_frames_min=" + str(transition_frames_min)
          + " heap_before=" + str(heap_before)
          + " heap_min=" + str(heap_min)
          + " heap_after=" + str(heap_after)
          + " heap_delta=" + str(heap_after - heap_before)
          + " failures=" + str(failures)
          + " animations_left=" + str(active_animation_count())
          + " buffers=" + buffers)
    return (failures, input_to_first_max, animated_frame_max,
            transition_frames_min, direct_count)


def _guard_swap_during_animation(nav):
    """Count any page SWAP filesystem seam entered during visible motion."""
    swap = nav.residency.swap
    originals = []
    state = {"violations": 0}

    def wrap(method, name):
        def guarded(*args):
            if nav.is_transitioning() or active_animation_count():
                state["violations"] += 1
                print("MONITOR_SD_DURING_ANIMATION operation=" + name)
            return method(*args)
        return guarded

    for name in ("read", "write_packed", "discard"):
        method = getattr(swap, name)
        originals.append((name, method))
        setattr(swap, name, wrap(method, name))
    return swap, originals, state


def _restore_swap_methods(swap, originals):
    for name, method in originals:
        setattr(swap, name, method)


def run(runtime=None):
    if runtime is None:
        runtime = metrics.runtime()
    if runtime is None:
        raise RuntimeError("SCI-CALC runtime is unavailable")

    nav, root, targets = runtime
    if nav.current is not root:
        nav.reset(root)
    cancel_all_animations()
    if not nav.residency.swap.available:
        nav.residency.swap.start_session()
    stat = os.statvfs("/sd")
    failures = 0
    input_to_first_max = 0
    animation_frame_max = 0
    transition_frames_min = -1
    direct_count = 0
    swap, original_swap_methods, swap_guard = _guard_swap_during_animation(nav)

    try:
        # Warm every real page through the same settle path before taking the
        # heap baseline. This excludes one-time imports/font layouts while
        # retaining repeatable transition, SWAP and workspace allocations.
        for target in targets:
            nav.go_to(target)
            _drive(nav)
            nav.go_back()
            _drive(nav)
        gc.collect()
        heap_before = _heap_free()
        buffers_before = tuple(sorted(nav.memory._buffers.keys()))
        print("MONITOR_START heap_free=" + str(heap_before)
              + " heap_alloc=" + str(gc.mem_alloc())
              + " sd_free=" + str(stat[0] * stat[3])
              + " targets=" + str(len(targets)))

        base_cycles = TOTAL_ROUND_TRIPS // max(1, len(targets))
        remainder = TOTAL_ROUND_TRIPS % max(1, len(targets))
        for index, target in enumerate(targets):
            cycles = base_cycles + (1 if index < remainder else 0)
            (screen_failures, screen_first_max, screen_animation_max,
             screen_transition_min, screen_direct) = _exercise(
                nav, root, target, cycles)
            failures += screen_failures
            input_to_first_max = max(input_to_first_max, screen_first_max)
            animation_frame_max = max(
                animation_frame_max, screen_animation_max)
            transition_frames_min = (
                screen_transition_min if transition_frames_min < 0
                else min(transition_frames_min, screen_transition_min))
            direct_count += screen_direct
    except Exception:
        _restore_swap_methods(swap, original_swap_methods)
        raise

    if nav.current is not root:
        nav.reset(root)
    cancel_all_animations()
    gc.collect()
    heap_after = _heap_free()
    heap_delta = heap_after - heap_before
    buffers_after = tuple(sorted(nav.memory._buffers.keys()))
    _restore_swap_methods(swap, original_swap_methods)
    acceptance_failures = []
    if failures:
        acceptance_failures.append("memory_errors=" + str(failures))
    if input_to_first_max > MAX_FIRST_FRAME_US:
        acceptance_failures.append(
            "first_frame_us=" + str(input_to_first_max))
    if animation_frame_max > MAX_ANIMATION_FRAME_US:
        acceptance_failures.append(
            "animation_frame_us=" + str(animation_frame_max))
    if transition_frames_min < MIN_TRANSITION_FRAMES:
        acceptance_failures.append(
            "transition_frames=" + str(transition_frames_min))
    if direct_count:
        acceptance_failures.append("direct_transitions=" + str(direct_count))
    if swap_guard["violations"]:
        acceptance_failures.append(
            "sd_during_animation=" + str(swap_guard["violations"]))
    if abs(heap_delta) > MAX_HEAP_DRIFT_BYTES:
        acceptance_failures.append("heap_delta=" + str(heap_delta))
    if buffers_after != buffers_before:
        acceptance_failures.append("buffer_set_changed")
    print("MONITOR_END heap_free=" + str(heap_after)
          + " heap_alloc=" + str(gc.mem_alloc())
          + " heap_delta=" + str(heap_delta)
          + " failures=" + str(failures)
          + " animations_left=" + str(active_animation_count()))
    if acceptance_failures:
        print("MONITOR_ACCEPTANCE FAIL " + ",".join(acceptance_failures))
        raise RuntimeError("Device runtime acceptance failed")
    print("MONITOR_ACCEPTANCE PASS round_trips="
          + str(TOTAL_ROUND_TRIPS)
          + " first_frame_max_us=" + str(input_to_first_max)
          + " animation_frame_max_us=" + str(animation_frame_max)
          + " transition_frames_min=" + str(transition_frames_min)
          + " sd_during_animation=" + str(swap_guard["violations"])
          + " heap_delta=" + str(heap_delta)
          + " buffers=" + ",".join(buffers_after))


if __name__ == "__main__":
    run()
