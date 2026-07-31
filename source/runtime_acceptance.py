"""Shared runtime identity and acceptance seam for host and device probes."""

import gc
import time

from runtime_handle import set_resident_runtime
from runtime_materialize import RuntimeHandle, get_resident_runtime
from ui.element import SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW


VISIT_TARGET = 1
RUN_ACTION = 2
RUN_BOUNDED = 3
MAX_SETTLE_STEPS = 256
MAX_BOUNDED_STEPS = 512
MAX_BOUNDED_NO_PROGRESS_STEPS = 8
MAX_BLOCKING_STEP_US = 40_000
MIN_HEAP_FREE_BYTES = 12 * 1024
MAX_HEAP_DRIFT_BYTES = 512

RUN_START = 1
RUN_STEP = 2
RUN_MEMORY_ERROR = 3
RUN_ERROR = 4
RUN_END = 5

PHASE_ACTION = 1
PHASE_ENTER = 2
PHASE_SETTLE = 3
PHASE_BACK = 4
PHASE_COLLECT = 5

# A bounded session returns these scalar values from
# step(round_index, capability_index).
# STEP_WAIT is permitted only for a small fixed number of consecutive calls.
STEP_MORE = 1
STEP_DONE = 2
STEP_WAIT = 3

FAIL_MEMORY = 1
FAIL_ERROR = 2
FAIL_BLOCKING = 4
FAIL_HEAP = 8
FAIL_DRIFT = 16
FAIL_BUFFERS = 32
FAIL_INCOMPLETE = 64
FAIL_ROOT = 128

ERROR_NONE = 0
ERROR_MEMORY = 1
ERROR_EXCEPTION = 2

_PHASE_FAILED = -1


class RuntimeAcceptanceReport:
    """Bounded aggregate updated in place while a scenario is running."""

    __slots__ = (
        "scenario_name", "mode", "rounds_expected", "rounds_completed",
        "scenarios_completed", "runtime_steps", "memory_errors", "errors",
        "bounded_close_attempts", "bounded_session_restored",
        "heap_before", "heap_after", "heap_min", "heap_delta",
        "blocking_max_us", "buffers_before", "buffers_after",
        "buffer_peak_bytes", "buffer_change_count", "failure_mask",
        "accepted", "round_index", "step_name", "phase", "step_us",
        "step_heap_free", "step_buffers", "_primary_error",
        "_secondary_error", "primary_error_code", "secondary_error_code",
        "_error_handoff_ready", "_visit_buffer_snapshot")

    def __init__(self, scenario_name, mode, rounds):
        self.scenario_name = scenario_name
        self.mode = mode
        self.rounds_expected = rounds
        self.rounds_completed = 0
        self.scenarios_completed = 0
        self.runtime_steps = 0
        self.memory_errors = 0
        self.errors = 0
        self.bounded_close_attempts = 0
        self.bounded_session_restored = False
        self.heap_before = -1
        self.heap_after = -1
        self.heap_min = -1
        self.heap_delta = -1
        self.blocking_max_us = 0
        self.buffers_before = ()
        self.buffers_after = ()
        self.buffer_peak_bytes = 0
        self.buffer_change_count = 0
        self.failure_mask = 0
        self.accepted = False
        self.round_index = -1
        self.step_name = None
        self.phase = 0
        self.step_us = 0
        self.step_heap_free = -1
        self.step_buffers = ()
        self._primary_error = None
        self._secondary_error = None
        self.primary_error_code = ERROR_NONE
        self.secondary_error_code = ERROR_NONE
        self._error_handoff_ready = False
        self._visit_buffer_snapshot = ()

    @property
    def primary_error(self):
        """Transfer a resident/release OOM once without retaining its frames."""
        error = self._primary_error
        if self._error_handoff_ready and self.mode != "in_memory":
            self._primary_error = None
            self._secondary_error = None
        return error

    @property
    def secondary_error(self):
        """Compatibility view; resident/release retain only its scalar code."""
        return self._secondary_error


def _heap_free():
    reporter = getattr(gc, "mem_free", None)
    return reporter() if reporter is not None else -1


def _safe_collect(report):
    try:
        gc.collect()
        return 0
    except MemoryError:
        report.memory_errors += 1
        report.failure_mask |= FAIL_MEMORY
        return RUN_MEMORY_ERROR
    except Exception:
        report.errors += 1
        report.failure_mask |= FAIL_ERROR
        return RUN_ERROR


def _safe_reset_root(runtime, report, present):
    try:
        runtime.reset_root(present=present)
        return True
    except MemoryError:
        report.memory_errors += 1
        report.failure_mask |= FAIL_MEMORY | FAIL_ROOT
    except Exception:
        report.errors += 1
        report.failure_mask |= FAIL_ERROR | FAIL_ROOT
    return False


def _minimum(current, candidate):
    if current < 0:
        return candidate
    if candidate < 0:
        return current
    return min(current, candidate)


def _record_failure(report, event):
    if event == RUN_MEMORY_ERROR:
        report.memory_errors += 1
        report.failure_mask |= FAIL_MEMORY
    else:
        report.errors += 1
        report.failure_mask |= FAIL_ERROR


def _notify(observer, event, report):
    if observer is None:
        return 0
    try:
        observer(event, report)
    except MemoryError as error:
        _remember_primary_error(report, error)
        _record_failure(report, RUN_MEMORY_ERROR)
        return RUN_MEMORY_ERROR
    except Exception:
        _record_failure(report, RUN_ERROR)
        return RUN_ERROR
    return 0


def _buffer_bytes(snapshot):
    total = 0
    for item in snapshot:
        total += item[1]
    return total


def _safe_buffer_snapshot(runtime, report):
    try:
        return runtime.buffer_snapshot()
    except MemoryError:
        report.memory_errors += 1
        report.failure_mask |= FAIL_MEMORY | FAIL_BUFFERS
    except Exception:
        report.errors += 1
        report.failure_mask |= FAIL_ERROR | FAIL_BUFFERS
    return ()


def _sample_buffers(runtime, report, optional_target=None):
    try:
        snapshot = runtime.buffer_snapshot()
    except MemoryError:
        report.memory_errors += 1
        report.failure_mask |= FAIL_MEMORY | FAIL_BUFFERS
        report.step_buffers = ()
        return RUN_MEMORY_ERROR
    except Exception:
        report.errors += 1
        report.failure_mask |= FAIL_ERROR | FAIL_BUFFERS
        report.step_buffers = ()
        return RUN_ERROR
    report.step_buffers = snapshot
    size = _buffer_bytes(snapshot)
    if size > report.buffer_peak_bytes:
        report.buffer_peak_bytes = size
    if snapshot != report.buffers_before:
        report.buffer_change_count += 1
    accepted = runtime.accepts_buffer_snapshot(
        report.buffers_before, snapshot, optional_target)
    if not accepted:
        report.failure_mask |= FAIL_BUFFERS
    elif optional_target is not None:
        locked = report._visit_buffer_snapshot
        if locked:
            if snapshot != locked:
                report.failure_mask |= FAIL_BUFFERS
        elif snapshot != report.buffers_before:
            report._visit_buffer_snapshot = snapshot
    return 0


def _finish_runtime_step(
        runtime, report, observer, phase, started, event=RUN_STEP,
        optional_target=None):
    elapsed = time.ticks_diff(time.ticks_us(), started)
    heap_free = _heap_free()
    report.phase = phase
    report.step_us = elapsed
    report.step_heap_free = heap_free
    report.runtime_steps += 1
    report.heap_min = _minimum(report.heap_min, heap_free)
    if elapsed > report.blocking_max_us:
        report.blocking_max_us = elapsed
    sample_event = _sample_buffers(runtime, report, optional_target)
    if event == RUN_STEP and sample_event:
        event = sample_event
    observer_event = _notify(observer, event, report)
    if observer_event == RUN_MEMORY_ERROR:
        return RUN_MEMORY_ERROR
    if event != RUN_STEP:
        return event
    return observer_event


def _finish_failed_phase(
        runtime, report, observer, phase, started, event,
        optional_target=None):
    _record_failure(report, event)
    _finish_runtime_step(
        runtime, report, observer, phase, started, event,
        optional_target=optional_target)
    return _PHASE_FAILED


def _execute_phase(
        runtime, report, observer, phase, target=None,
        optional_target=None):
    nav = runtime.nav
    started = time.ticks_us()
    event = RUN_STEP
    result = 0
    try:
        if phase == PHASE_ENTER:
            if isinstance(target, int):
                nav.open(target)
            else:
                nav.go_to(target)
            nav.present_current()
        elif phase == PHASE_SETTLE:
            flags = nav.settle_current()
            if flags & SETTLE_COLLECT:
                collect_event = _safe_collect(report)
                if collect_event:
                    event = collect_event
            if flags & SETTLE_REDRAW:
                nav.present_current()
            result = flags
        elif phase == PHASE_BACK:
            back = getattr(nav, "back", None)
            if back is None:
                nav.go_back()
            else:
                back()
            nav.present_current()
        elif phase == PHASE_COLLECT:
            nav.collect_pending()
        else:
            raise ValueError("Unknown timed runtime phase")
    except MemoryError:
        return _finish_failed_phase(
            runtime, report, observer, phase, started,
            RUN_MEMORY_ERROR, optional_target=optional_target)
    except Exception:
        return _finish_failed_phase(
            runtime, report, observer, phase, started,
            RUN_ERROR, optional_target=optional_target)
    _finish_runtime_step(
        runtime, report, observer, phase, started, event,
        optional_target=optional_target)
    return result


def _settle(runtime, report, observer, optional_target):
    for _ in range(MAX_SETTLE_STEPS):
        flags = _execute_phase(
            runtime, report, observer, PHASE_SETTLE,
            optional_target=optional_target)
        if flags == _PHASE_FAILED:
            return False
        if not flags & SETTLE_MORE:
            return True
    raise RuntimeError("Page settle work exceeded its fixed bound")


def _visit_target(runtime, target, report, observer):
    nav = runtime.nav
    report._visit_buffer_snapshot = ()
    if _execute_phase(
            runtime, report, observer, PHASE_ENTER, target,
            optional_target=target) == _PHASE_FAILED:
        return False
    if not _settle(runtime, report, observer, target):
        return False
    if _execute_phase(
            runtime, report, observer,
            PHASE_BACK) == _PHASE_FAILED:
        return False
    if not _settle(runtime, report, observer, None):
        return False
    collector = getattr(nav, "collect_pending", None)
    if collector is not None:
        if _execute_phase(
                runtime, report, observer,
                PHASE_COLLECT) == _PHASE_FAILED:
            return False
    return True


def _error_code(error):
    if isinstance(error, MemoryError):
        return ERROR_MEMORY
    return ERROR_EXCEPTION


def _detach_memory_error_frames(error):
    """Keep OOM identity for the device handoff without retaining call frames."""
    try:
        error.__traceback__ = None
    except (AttributeError, TypeError):
        pass
    try:
        error.__context__ = None
    except (AttributeError, TypeError):
        pass
    try:
        error.__cause__ = None
    except (AttributeError, TypeError):
        pass


def _store_primary_error(report, error, code):
    report.primary_error_code = code
    if report.mode == "in_memory":
        report._primary_error = error
    elif code == ERROR_MEMORY:
        _detach_memory_error_frames(error)
        report._primary_error = error
    else:
        report._primary_error = None


def _remember_primary_error(report, error):
    code = _error_code(error)
    current = report.primary_error_code
    if code == ERROR_MEMORY:
        if current != ERROR_MEMORY:
            _store_primary_error(report, error, code)
    elif current == ERROR_NONE:
        _store_primary_error(report, error, code)



def _bounded_session(steps):
    """Load the transaction runner only for an all-bounded scenario."""
    bounded_count = 0
    for step in steps:
        if step[1] == RUN_BOUNDED:
            bounded_count += 1
    if not bounded_count:
        return None
    from runtime_acceptance_bounded import validate_bounded_session
    return validate_bounded_session(steps)

def run(runtime, scenario, observer=None):
    """Execute every fixed scenario step in every requested round."""
    scenario_name, requested_rounds, steps = scenario
    rounds = int(requested_rounds)
    if runtime.mode in ("resident", "release") and rounds != 5:
        raise ValueError(
            "Resident/release acceptance requires exactly 5 rounds")
    rounds = max(0, rounds)
    report = RuntimeAcceptanceReport(scenario_name, runtime.mode, rounds)
    runnable = _safe_reset_root(runtime, report, False)
    _safe_collect(report)
    report.heap_before = _heap_free()
    report.heap_min = report.heap_before
    report.buffers_before = _safe_buffer_snapshot(runtime, report)
    report.buffer_peak_bytes = _buffer_bytes(report.buffers_before)
    report.step_buffers = report.buffers_before
    _notify(observer, RUN_START, report)

    bounded = _bounded_session(steps)
    skip_post_bounded_reset = False
    if bounded is not None:
        session, capabilities = bounded
        report.step_name = capabilities[0]
        if runnable and rounds:
            from runtime_acceptance_bounded import run_bounded_session
            bounded_completed = run_bounded_session(
                    runtime, report, observer, steps, session, capabilities,
                    rounds)
            if not bounded_completed and report.bounded_session_restored:
                _safe_reset_root(runtime, report, True)
            # Only an attempted but unrecovered transaction owns state that
            # makes a root reset unsafe.  A zero-round/initial-reset failure
            # never opened a bounded session and keeps the normal final reset.
            skip_post_bounded_reset = (
                report.bounded_close_attempts > 0
                and not report.bounded_session_restored)
    else:
        for round_index in range(rounds if runnable else 0):
            report.round_index = round_index
            for step_name, kind, payload in steps:
                report.step_name = step_name
                started = None
                completed = True
                try:
                    if kind == VISIT_TARGET:
                        target = (runtime.targets[payload]
                                  if runtime.targets else payload)
                        completed = _visit_target(
                            runtime, target, report, observer)
                    elif kind == RUN_ACTION:
                        started = time.ticks_us()
                        payload(runtime, round_index)
                        _finish_runtime_step(
                            runtime, report, observer, PHASE_ACTION, started)
                    else:
                        raise ValueError("Unknown runtime scenario step")
                except MemoryError:
                    if started is None:
                        started = time.ticks_us()
                    report.memory_errors += 1
                    report.failure_mask |= FAIL_MEMORY
                    _finish_runtime_step(
                        runtime, report, observer, PHASE_ACTION, started,
                        RUN_MEMORY_ERROR)
                    _safe_reset_root(runtime, report, True)
                except Exception:
                    if started is None:
                        started = time.ticks_us()
                    report.errors += 1
                    report.failure_mask |= FAIL_ERROR
                    _finish_runtime_step(
                        runtime, report, observer, PHASE_ACTION, started,
                        RUN_ERROR)
                    _safe_reset_root(runtime, report, True)
                else:
                    if completed:
                        report.scenarios_completed += 1
                    else:
                        _safe_reset_root(runtime, report, True)
            report.rounds_completed += 1

    if not skip_post_bounded_reset:
        _safe_reset_root(runtime, report, False)
    _safe_collect(report)
    report.heap_after = _heap_free()
    report.heap_min = _minimum(report.heap_min, report.heap_after)
    if report.heap_before >= 0 and report.heap_after >= 0:
        report.heap_delta = report.heap_after - report.heap_before
    report.buffers_after = _safe_buffer_snapshot(runtime, report)
    if report.blocking_max_us >= MAX_BLOCKING_STEP_US:
        report.failure_mask |= FAIL_BLOCKING
    if report.heap_min >= 0 and report.heap_min < MIN_HEAP_FREE_BYTES:
        report.failure_mask |= FAIL_HEAP
    if (report.heap_delta != -1
            and abs(report.heap_delta) > MAX_HEAP_DRIFT_BYTES):
        report.failure_mask |= FAIL_DRIFT
    if report.buffers_after != report.buffers_before:
        report.failure_mask |= FAIL_BUFFERS
    if report.rounds_completed != report.rounds_expected:
        report.failure_mask |= FAIL_INCOMPLETE
    report.accepted = report.failure_mask == 0
    _notify(observer, RUN_END, report)
    report._error_handoff_ready = True
    report.accepted = report.failure_mask == 0
    return report
