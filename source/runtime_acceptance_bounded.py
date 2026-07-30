"""Cold transaction runner loaded only for bounded acceptance scenarios."""

import time

from runtime_acceptance import (
    ERROR_EXCEPTION, ERROR_MEMORY, ERROR_NONE, FAIL_INCOMPLETE,
    MAX_BOUNDED_NO_PROGRESS_STEPS, MAX_BOUNDED_STEPS, PHASE_ACTION,
    RUN_BOUNDED, RUN_ERROR, RUN_MEMORY_ERROR, RUN_STEP,
    STEP_DONE, STEP_MORE, STEP_WAIT,
    _finish_failed_phase, _finish_runtime_step, _notify, _record_failure)


_CLOSE_FAILED_ERROR = RuntimeError("Bounded session close failed")


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


def _remember_secondary_error_code(report, code):
    if report.secondary_error_code == ERROR_NONE:
        report.secondary_error_code = code


def _remember_final_secondary_error(report, error):
    """Keep only the terminal close fault when retry history has no object."""
    if report.secondary_error_code == ERROR_NONE:
        report.secondary_error_code = _error_code(error)
    if report.mode == "in_memory" and report._secondary_error is None:
        report._secondary_error = error


def _remember_primary_as_secondary(report):
    if (report.secondary_error_code != ERROR_NONE
            or report.primary_error_code == ERROR_NONE):
        return
    report.secondary_error_code = report.primary_error_code
    if report.mode == "in_memory":
        report._secondary_error = report._primary_error


def _remember_retry_close_failure(report, event, error):
    """Record only an initial close OOM before the bounded retry."""
    if event == RUN_MEMORY_ERROR:
        if report.primary_error_code != ERROR_MEMORY:
            if report.primary_error_code != ERROR_NONE:
                _remember_primary_as_secondary(report)
            _remember_primary_error(report, error)
        else:
            _remember_secondary_error_code(report, ERROR_MEMORY)


def _remember_recovered_retry_close_failure(report):
    """Publish a recovered ordinary close fault only when it is still primary."""
    if report.primary_error_code == ERROR_NONE:
        report.primary_error_code = ERROR_EXCEPTION


def _remember_final_close_failure(report, event, error):
    """Record the terminal close failure after any retry-only fault is gone."""
    if error is None:
        error = _CLOSE_FAILED_ERROR
    if event == RUN_MEMORY_ERROR:
        if report.primary_error_code != ERROR_MEMORY:
            if report.primary_error_code != ERROR_NONE:
                _remember_primary_as_secondary(report)
            _remember_primary_error(report, error)
        else:
            _remember_final_secondary_error(report, error)
        return
    if report.primary_error_code == ERROR_NONE:
        _remember_primary_error(report, error)
    elif (report.primary_error_code == ERROR_EXCEPTION
          and report.mode == "in_memory"
          and report._primary_error is None):
        _store_primary_error(report, error, ERROR_EXCEPTION)
    else:
        _remember_final_secondary_error(report, error)


def _bounded_scalar(session, name, default, maximum):
    value = getattr(session, name, default)
    if (not isinstance(value, int) or value <= 0
            or value > maximum):
        raise ValueError("Invalid bounded session scalar")
    return value


def _bounded_limit(
        session, plural_name, scalar_name, index, capability_count,
        default, maximum):
    values = getattr(session, plural_name, None)
    if values is None:
        return _bounded_scalar(session, scalar_name, default, maximum)
    if not isinstance(values, tuple) or len(values) != capability_count:
        raise ValueError("Invalid bounded session limits")
    value = values[index]
    if (not isinstance(value, int) or value <= 0
            or value > maximum):
        raise ValueError("Invalid bounded session limit")
    return value


def _bounded_failure(
        report, observer, event, error=None, incomplete=False):
    if error is not None:
        _remember_primary_error(report, error)
    _record_failure(report, event)
    if incomplete:
        report.failure_mask |= FAIL_INCOMPLETE
    observer_event = _notify(observer, event, report)
    if event == RUN_MEMORY_ERROR:
        return RUN_MEMORY_ERROR
    return observer_event if observer_event else RUN_ERROR


def _bounded_contract_failure(report, observer, incomplete=False):
    return _bounded_failure(
        report, observer, RUN_ERROR, incomplete=incomplete)


def _finish_bounded_close(
        runtime, report, observer, close_session, primary_event):
    """Make one close attempt, then one bounded retry only when necessary."""
    first_failed = False
    first_close_event = RUN_STEP
    for attempt in range(2):
        started = time.ticks_us()
        report.bounded_close_attempts += 1
        close_event = RUN_STEP
        close_error = None
        try:
            restored = close_session() is True
            if not restored:
                close_event = RUN_ERROR
        except MemoryError as error:
            restored = False
            close_event = RUN_MEMORY_ERROR
            close_error = error
            if report.mode != "in_memory":
                _detach_memory_error_frames(error)
        except Exception as error:
            restored = False
            close_event = RUN_ERROR
            close_error = error

        if restored:
            report.bounded_session_restored = True
            event = _finish_runtime_step(
                runtime, report, observer, PHASE_ACTION, started)
            if first_failed and first_close_event == RUN_ERROR:
                _remember_recovered_retry_close_failure(report)
            if event == RUN_MEMORY_ERROR:
                primary_event = RUN_MEMORY_ERROR
            if first_failed or primary_event:
                return primary_event
            return event

        if attempt:
            _remember_final_close_failure(report, close_event, close_error)
        else:
            _remember_retry_close_failure(report, close_event, close_error)
        if close_event == RUN_MEMORY_ERROR:
            if primary_event != RUN_MEMORY_ERROR:
                _record_failure(report, RUN_MEMORY_ERROR)
                primary_event = RUN_MEMORY_ERROR
        elif not primary_event:
            _record_failure(report, RUN_ERROR)
            primary_event = RUN_ERROR
        event = _finish_runtime_step(
            runtime, report, observer, PHASE_ACTION, started, close_event)
        if event == RUN_MEMORY_ERROR:
            primary_event = RUN_MEMORY_ERROR
        if attempt:
            return primary_event
        close_error = None
        first_close_event = close_event
        first_failed = True
    return primary_event


def run_bounded_session(
        runtime, report, observer, steps, session, capabilities, rounds):
    """Run one transaction session through an immutable capability sequence."""
    primary_event = 0
    close_session = None
    all_rounds_completed = False
    capability_count = len(capabilities)
    completed_count = 0

    try:
        started = time.ticks_us()
        try:
            close_session = session.close
            open_session = session.open
            step_session = session.step
            open_session(runtime)
        except MemoryError as error:
            _remember_primary_error(report, error)
            _finish_failed_phase(
                runtime, report, observer, PHASE_ACTION, started,
                RUN_MEMORY_ERROR)
            primary_event = RUN_MEMORY_ERROR
        except Exception as error:
            _remember_primary_error(report, error)
            _finish_failed_phase(
                runtime, report, observer, PHASE_ACTION, started,
                RUN_ERROR)
            primary_event = RUN_ERROR
        else:
            primary_event = _finish_runtime_step(
                runtime, report, observer, PHASE_ACTION, started)

        round_index = 0
        while not primary_event and round_index < rounds:
            report.round_index = round_index
            capability_index = 0
            while (not primary_event
                   and capability_index < capability_count):
                expected_capability = capabilities[capability_index]
                report.step_name = steps[capability_index][0]
                try:
                    max_steps = _bounded_limit(
                        session, "step_limits", "max_steps",
                        capability_index, capability_count,
                        MAX_BOUNDED_STEPS, MAX_BOUNDED_STEPS)
                    no_progress_limit = _bounded_limit(
                        session, "no_progress_limits", "no_progress_limit",
                        capability_index, capability_count,
                        MAX_BOUNDED_NO_PROGRESS_STEPS,
                        MAX_BOUNDED_NO_PROGRESS_STEPS)
                except MemoryError as error:
                    primary_event = _bounded_failure(
                        report, observer, RUN_MEMORY_ERROR, error,
                        incomplete=True)
                    break
                except Exception as error:
                    primary_event = _bounded_failure(
                        report, observer, RUN_ERROR, error,
                        incomplete=True)
                    break

                step_count = 0
                no_progress_count = 0
                capability_complete = False
                while not primary_event and not capability_complete:
                    if step_count >= max_steps:
                        primary_event = _bounded_contract_failure(
                            report, observer, incomplete=True)
                        break
                    started = time.ticks_us()
                    try:
                        status = step_session(round_index, capability_index)
                    except MemoryError as error:
                        _remember_primary_error(report, error)
                        _finish_failed_phase(
                            runtime, report, observer, PHASE_ACTION, started,
                            RUN_MEMORY_ERROR)
                        primary_event = RUN_MEMORY_ERROR
                        break
                    except Exception as error:
                        _remember_primary_error(report, error)
                        _finish_failed_phase(
                            runtime, report, observer, PHASE_ACTION, started,
                            RUN_ERROR)
                        primary_event = RUN_ERROR
                        break

                    step_count += 1
                    event = _finish_runtime_step(
                        runtime, report, observer, PHASE_ACTION, started)
                    if event:
                        primary_event = event
                        break
                    if status == STEP_DONE:
                        try:
                            reported_capability = getattr(
                                session, "completed_capability", None)
                            reported_count = getattr(
                                session, "completed_count", None)
                        except MemoryError as error:
                            primary_event = _bounded_failure(
                                report, observer, RUN_MEMORY_ERROR, error,
                                incomplete=True)
                        except Exception as error:
                            primary_event = _bounded_failure(
                                report, observer, RUN_ERROR, error,
                                incomplete=True)
                        if (not primary_event
                                and (reported_capability
                                     != expected_capability
                                     or not isinstance(reported_count, int)
                                     or reported_count
                                     != completed_count + 1)):
                            primary_event = _bounded_contract_failure(
                                report, observer, incomplete=True)
                        elif not primary_event:
                            completed_count = reported_count
                            report.scenarios_completed += 1
                            capability_complete = True
                    elif status == STEP_MORE:
                        no_progress_count = 0
                    elif status == STEP_WAIT:
                        no_progress_count += 1
                        if no_progress_count >= no_progress_limit:
                            primary_event = _bounded_contract_failure(
                                report, observer, incomplete=True)
                    else:
                        primary_event = _bounded_contract_failure(
                            report, observer, incomplete=True)
                capability_index += 1
            if not primary_event and capability_index == capability_count:
                report.rounds_completed += 1
            round_index += 1
        all_rounds_completed = (
            not primary_event and report.rounds_completed == rounds)
    finally:
        if close_session is not None:
            primary_event = _finish_bounded_close(
                runtime, report, observer, close_session, primary_event)

    return all_rounds_completed and not primary_event


def validate_bounded_session(steps):
    """Validate one immutable sequence backed by one transaction session."""
    for step in steps:
        if step[1] != RUN_BOUNDED:
            raise ValueError("Bounded scenarios cannot mix transaction entries")

    session = steps[0][2]
    capabilities = getattr(session, "capabilities", None)
    if (not isinstance(capabilities, tuple)
            or len(capabilities) != len(steps)):
        raise ValueError("Bounded session capability sequence is invalid")
    for index in range(len(steps)):
        step_name, _kind, step_session = steps[index]
        if step_session is not session:
            raise ValueError("Bounded entries require one transaction session")
        if capabilities[index] != step_name:
            raise ValueError("Bounded capability sequence does not match steps")
    return session, capabilities
