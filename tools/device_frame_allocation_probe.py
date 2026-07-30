"""Controlled device-only Stopwatch partial-frame allocation probe.

This file deliberately does not execute on import.  An authorized device
workflow may call ``run()`` against the already resident application runtime.
"""
import gc
import sys
import time


if "/sd" not in sys.path:
    sys.path.insert(0, "/sd")


DEFAULT_FRAMES = 16
MAX_FRAMES = DEFAULT_FRAMES
FRAME_INTERVAL_MS = 50
_STOPWATCH_FIELDS = ("_clock", "_render", "_footer", "_runtime")


class _StopwatchState:
    """A fixed, bounded snapshot of the target state around the probe."""

    __slots__ = (
        "running", "paused", "start_time", "elapsed", "laps",
        "lap_cursor", "view_offset", "next_lap_num", "lap_revision",
        "time_short", "time_long", "time_extended", "presented_running",
        "presented_elapsed_cs", "presented_lap_revision",
        "presented_lap_cursor", "presented_view_offset",
        "presented_footer_state",
        "footer_hint", "footer_hint_bytes", "footer_right",
        "footer_right_bytes", "footer_right_x", "footer_cache_state",
        "lap_label_0", "lap_label_1", "lap_label_2", "lap_label_3",
        "lap_label_cache_revision", "lap_label_cache_view_offset",
        "lap_label_cache_laps", "lap_label_cache_total",
    )

    def __init__(self, stopwatch):
        self.running = stopwatch._clock[1]
        self.paused = stopwatch._clock[2][0]
        self.start_time = stopwatch._clock[2][1]
        self.elapsed = stopwatch._clock[2][2]
        # The probe neither adds nor removes laps, so a reference is enough.
        self.laps = stopwatch._clock[2][3]
        self.lap_cursor = stopwatch._clock[3][0]
        self.view_offset = stopwatch._clock[3][1]
        self.next_lap_num = stopwatch._clock[3][2]
        self.lap_revision = stopwatch._clock[3][3]
        # Fixed timer buffers are copied before sampling and restored in place.
        self.time_short = bytes(stopwatch._render[0][0])
        self.time_long = bytes(stopwatch._render[0][1])
        self.time_extended = bytes(stopwatch._render[0][2])
        self.presented_running = stopwatch._render[1][0]
        self.presented_elapsed_cs = stopwatch._render[1][1]
        self.presented_lap_revision = stopwatch._render[1][2]
        self.presented_lap_cursor = stopwatch._render[1][3]
        self.presented_view_offset = stopwatch._render[2][0]
        self.presented_footer_state = stopwatch._render[2][1]
        # A prewarm full frame refreshes this state even though the probe must
        # leave an idle/paused stopwatch visually and logically unchanged.
        self.footer_hint = stopwatch._footer[0][0]
        self.footer_hint_bytes = stopwatch._footer[0][1]
        self.footer_right = stopwatch._footer[0][2]
        self.footer_right_bytes = stopwatch._footer[0][3]
        self.footer_right_x = stopwatch._footer[1][0]
        self.footer_cache_state = stopwatch._footer[1][1]
        # Copy the four retained labels, never the mutable cache-list alias.
        # This work is deliberately outside the measured interval.
        labels = stopwatch._runtime[0][0]
        self.lap_label_0 = labels[0]
        self.lap_label_1 = labels[1]
        self.lap_label_2 = labels[2]
        self.lap_label_3 = labels[3]
        self.lap_label_cache_revision = stopwatch._runtime[0][1]
        self.lap_label_cache_view_offset = stopwatch._runtime[0][2]
        self.lap_label_cache_laps = stopwatch._runtime[0][3]
        self.lap_label_cache_total = stopwatch._runtime[1][0]

    def restore(self, stopwatch):
        stopwatch._clock[1] = self.running
        stopwatch._clock[2][0] = self.paused
        stopwatch._clock[2][1] = self.start_time
        stopwatch._clock[2][2] = self.elapsed
        stopwatch._clock[2][3] = self.laps
        stopwatch._clock[3][0] = self.lap_cursor
        stopwatch._clock[3][1] = self.view_offset
        stopwatch._clock[3][2] = self.next_lap_num
        stopwatch._clock[3][3] = self.lap_revision
        stopwatch._render[0][0][:] = self.time_short
        stopwatch._render[0][1][:] = self.time_long
        stopwatch._render[0][2][:] = self.time_extended
        stopwatch._render[1][0] = self.presented_running
        stopwatch._render[1][1] = self.presented_elapsed_cs
        stopwatch._render[1][2] = self.presented_lap_revision
        stopwatch._render[1][3] = self.presented_lap_cursor
        stopwatch._render[2][0] = self.presented_view_offset
        stopwatch._render[2][1] = self.presented_footer_state
        stopwatch._footer[0][0] = self.footer_hint
        stopwatch._footer[0][1] = self.footer_hint_bytes
        stopwatch._footer[0][2] = self.footer_right
        stopwatch._footer[0][3] = self.footer_right_bytes
        stopwatch._footer[1][0] = self.footer_right_x
        stopwatch._footer[1][1] = self.footer_cache_state
        labels = stopwatch._runtime[0][0]
        labels[0] = self.lap_label_0
        labels[1] = self.lap_label_1
        labels[2] = self.lap_label_2
        labels[3] = self.lap_label_3
        stopwatch._runtime[0][1] = self.lap_label_cache_revision
        stopwatch._runtime[0][2] = self.lap_label_cache_view_offset
        stopwatch._runtime[0][3] = self.lap_label_cache_laps
        stopwatch._runtime[1][0] = self.lap_label_cache_total


def _resident_runtime():
    from runtime_materialize import get_resident_runtime

    return get_resident_runtime()


def _resolve_runtime(runtime):
    if runtime is None:
        runtime = _resident_runtime()
    if runtime is None or getattr(runtime, "mode", None) != "resident":
        raise RuntimeError("Stopwatch allocation probe requires a resident runtime")
    return runtime


def _unavailable(emit, reason):
    emit("STOPWATCH_FRAME_ALLOC_UNAVAILABLE reason=" + reason)
    emit("STOPWATCH_FRAME_ALLOC_RESULT FAIL")
    raise RuntimeError("Stopwatch allocation probe unavailable: " + reason)


def _validate_stopwatch(emit, stopwatch):
    if stopwatch is None:
        _unavailable(emit, "stopwatch_target")
    if not callable(getattr(stopwatch, "_start", None)):
        _unavailable(emit, "stopwatch_start")
    for field in _STOPWATCH_FIELDS:
        if not hasattr(stopwatch, field):
            _unavailable(emit, "stopwatch_state")
    if (len(stopwatch._clock) != 4
            or len(stopwatch._render) != 3
            or len(stopwatch._footer) != 2
            or len(stopwatch._runtime) != 2
            or len(stopwatch._runtime[0][0]) != 4):
        _unavailable(emit, "stopwatch_state")


def _emit_samples(emit, before_values, after_values, deltas, presented):
    frame_count = len(deltas)
    index = 0
    nonzero = 0
    missing_present = 0
    total_delta = 0
    while index < frame_count:
        delta = deltas[index]
        if delta != 0:
            nonzero += 1
        if not presented[index]:
            missing_present += 1
        total_delta += delta
        emit(
            "STOPWATCH_FRAME_ALLOC_FRAME index=" + str(index + 1)
            + " before=" + str(before_values[index])
            + " after=" + str(after_values[index])
            + " delta=" + str(delta)
            + " presented=" + ("1" if presented[index] else "0"))
        index += 1
    emit(
        "STOPWATCH_FRAME_ALLOC_TOTAL frames=" + str(frame_count)
        + " nonzero=" + str(nonzero)
        + " missing_present=" + str(missing_present)
        + " delta_sum=" + str(total_delta))
    return nonzero, missing_present, total_delta


def run(runtime=None, frames=DEFAULT_FRAMES, emit=print):
    """Measure only Stopwatch partial presents on an existing resident runtime.

    Setup, snapshotting, sleeping, reporting and all list/text construction sit
    outside each ``mem_alloc -> present_current -> mem_alloc`` interval.
    """
    if (not isinstance(frames, int)
            or frames < 1 or frames > MAX_FRAMES):
        raise ValueError(
            "Stopwatch allocation probe frames must be within 1.."
            + str(MAX_FRAMES))

    runtime = _resolve_runtime(runtime)
    state = None
    stopwatch = None
    primary_error = None
    report = None
    try:
        mem_alloc = getattr(gc, "mem_alloc", None)
        if not callable(mem_alloc):
            _unavailable(emit, "gc_mem_alloc")

        nav = getattr(runtime, "nav", None)
        if (nav is None or not callable(getattr(nav, "go_to", None))
                or not callable(getattr(nav, "present_current", None))):
            _unavailable(emit, "nav_present")
        if not callable(getattr(runtime, "find_target", None)):
            _unavailable(emit, "runtime_targets")

        stopwatch = runtime.find_target("Stopwatch")
        _validate_stopwatch(emit, stopwatch)
        state = _StopwatchState(stopwatch)

        sleep_ms = getattr(time, "sleep_ms", None)
        if not callable(sleep_ms):
            _unavailable(emit, "time_sleep_ms")

        # Fixed backing is allocated before the first sample.  No append,
        # string formatting or emit happens within the measured interval.
        before_values = [0] * frames
        after_values = [0] * frames
        deltas = [0] * frames
        presented = [False] * frames

        nav.go_to(stopwatch)
        stopwatch._start()
        if not stopwatch._clock[1]:
            _unavailable(emit, "stopwatch_not_running")

        # A cleared presentation marker makes this one setup transfer a full
        # frame even if Stopwatch was already the visible page.
        stopwatch._render[1][0] = None
        if not nav.present_current():
            _unavailable(emit, "full_prewarm_present")

        # First warm the timer row-band route, but keep this transfer outside
        # the accounting loop.  A false return means the controlled partial
        # path did not present, so accepting later samples would be unsound.
        sleep_ms(FRAME_INTERVAL_MS)
        if not nav.present_current():
            _unavailable(emit, "partial_prewarm_present")
        collector = getattr(gc, "collect", None)
        if callable(collector):
            collector()

        index = 0
        present_current = nav.present_current
        while index < frames:
            # The elapsed centiseconds change before, never during, sampling.
            sleep_ms(FRAME_INTERVAL_MS)
            before = mem_alloc()
            was_presented = present_current()
            after = mem_alloc()
            before_values[index] = before
            after_values[index] = after
            deltas[index] = after - before
            presented[index] = bool(was_presented)
            index += 1

        nonzero, missing_present, total_delta = _emit_samples(
            emit, before_values, after_values, deltas, presented)
        if nonzero or missing_present:
            emit("STOPWATCH_FRAME_ALLOC_RESULT FAIL")
            raise RuntimeError("Stopwatch partial-frame allocation probe failed")
        report = {
            "frames": frames,
            "deltas": tuple(deltas),
            "total_delta": total_delta,
            "accepted": True,
        }
    except BaseException as error:
        # Keep the original failure authoritative.  In particular, a recovery
        # cleanup error must not hide MemoryError from the runtime's sole OOM
        # recovery seam.
        primary_error = error
        raise
    finally:
        cleanup_error = None
        try:
            if state is not None:
                state.restore(stopwatch)
        except BaseException as error:
            cleanup_error = error
        try:
            runtime.reset_root(present=True)
        except BaseException as error:
            # Always attempt reset even if restoration failed.  If no primary
            # operation failed, preserve the first cleanup failure for callers.
            if cleanup_error is None:
                cleanup_error = error
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error
    emit("STOPWATCH_FRAME_ALLOC_RESULT PASS")
    return report
