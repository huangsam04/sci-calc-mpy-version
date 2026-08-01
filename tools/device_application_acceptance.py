"""Five-round device application matrix over production page leases."""

import gc
import sys


_SCENARIO_MODULES = (
    ("screens.calculator_scenario",),
    ("screens.calculator_scenario",),
    ("calc.scenario_variables", "screens.calculator_scenario"),
    ("screens.plot_scenario",),
    ("calc.plugin_fixture", "runtime_fixture_pack"),
    ("screens.stopwatch_scenario",),
    ("nav_scenario",),
)


class _Summary:
    __slots__ = (
        "accepted", "rounds_completed", "scenarios_completed",
        "runtime_steps", "memory_errors", "errors", "heap_min",
        "heap_after", "heap_delta", "blocking_max_us",
        "buffer_peak_bytes", "failure_mask", "step_name", "phase",
        "bounded_close_attempts", "bounded_session_restored",
        "primary_error", "blocking_round", "blocking_step",
        "heap_step_name", "heap_phase", "heap_round", "heap_step",
        "scenario_name", "scenario_heap_before", "scenario_heap_after",
        "scenario_heap_delta")

    def __init__(self):
        self.accepted = False
        self.rounds_completed = 0
        self.scenarios_completed = 0
        self.runtime_steps = 0
        self.memory_errors = 0
        self.errors = 0
        self.heap_min = -1
        self.heap_after = -1
        self.heap_delta = -1
        self.blocking_max_us = 0
        self.buffer_peak_bytes = 0
        self.failure_mask = 0
        self.step_name = None
        self.phase = 0
        self.bounded_close_attempts = 0
        self.bounded_session_restored = True
        self.primary_error = None
        self.blocking_round = -1
        self.blocking_step = 0
        self.heap_step_name = None
        self.heap_phase = 0
        self.heap_round = -1
        self.heap_step = 0
        self.scenario_name = None
        self.scenario_heap_before = -1
        self.scenario_heap_after = -1
        self.scenario_heap_delta = -1

    def observe(self, event, report):
        heap_free = -1
        heap_name = None
        heap_phase = 0
        if event == 1:
            heap_free = report.heap_before
            heap_name = "acceptance_start"
        elif event == 5:
            heap_free = report.heap_after
            heap_name = "acceptance_end"
        if 2 <= event <= 4:
            if report.step_us > self.blocking_max_us:
                self.blocking_max_us = report.step_us
                self.blocking_round = report.round_index
                self.blocking_step = report.runtime_steps
                self.step_name = report.step_name
                self.phase = report.phase
            heap_free = report.step_heap_free
            heap_name = report.step_name
            heap_phase = report.phase
        if heap_free >= 0 and (
                self.heap_min < 0 or heap_free < self.heap_min):
            self.heap_min = heap_free
            self.heap_step_name = heap_name
            self.heap_phase = heap_phase
            self.heap_round = report.round_index
            self.heap_step = report.runtime_steps


def _resident_runtime():
    from runtime_materialize import get_resident_runtime

    return get_resident_runtime()


def _drop_modules(names):
    for name in names:
        module = sys.modules.pop(name, None)
        separator = name.rfind(".")
        if separator < 0:
            continue
        package = sys.modules.get(name[:separator])
        child = name[separator + 1:]
        if (module is not None and package is not None
                and getattr(package, child, None) is module):
            delattr(package, child)


def _merge(summary, report):
    summary.scenarios_completed += report.scenarios_completed
    summary.runtime_steps += report.runtime_steps
    summary.memory_errors += report.memory_errors
    summary.errors += report.errors
    if summary.heap_min < 0 or report.heap_min < summary.heap_min:
        summary.heap_min = report.heap_min
    if report.buffer_peak_bytes > summary.buffer_peak_bytes:
        summary.buffer_peak_bytes = report.buffer_peak_bytes
    summary.failure_mask |= report.failure_mask
    summary.bounded_close_attempts += report.bounded_close_attempts
    summary.bounded_session_restored = (
        summary.bounded_session_restored
        and report.bounded_session_restored)
    summary.scenario_name = report.scenario_name
    summary.scenario_heap_before = report.heap_before
    summary.scenario_heap_after = report.heap_after
    summary.scenario_heap_delta = report.heap_delta


def _warm_pages(runtime):
    nav = runtime.nav
    for page_id in range(1, 6):
        nav.open(page_id)
        nav.back()
        nav.collect_pending()


def _run_matrix(runtime):
    from runtime_acceptance import run
    import runtime_acceptance_bounded
    from runtime_scenarios import application_scenarios

    summary = _Summary()
    _warm_pages(runtime)
    gc.collect()
    heap_before = gc.mem_free()
    scenarios = application_scenarios(rounds=5)
    for index in range(len(scenarios)):
        modules = _SCENARIO_MODULES[index]
        for name in modules:
            __import__(name)
        gc.collect()
        report = run(runtime, scenarios[index], summary.observe)
        _merge(summary, report)
        if not report.accepted:
            summary.primary_error = report.primary_error
            _drop_modules(modules)
            gc.collect()
            summary.heap_after = gc.mem_free()
            summary.heap_delta = summary.heap_after - heap_before
            return summary
        report = None
        _drop_modules(modules)
        gc.collect()

    summary.rounds_completed = 5
    summary.heap_after = gc.mem_free()
    summary.heap_delta = summary.heap_after - heap_before
    # Each scenario's five-round run enforces the unchanged -512 B product
    # drift limit.  The outer delta also includes permanent qstrs created by
    # importing acceptance-only modules, so report it without classifying it
    # as product-state drift.
    summary.accepted = summary.failure_mask == 0
    return summary


def run(runtime=None, emit=print):
    if runtime is None:
        from calc.plugin_fixture import configure_transient_fixture

        configure_transient_fixture(
            "/sd/_sci_accept_support/functions")
        runtime = _resident_runtime()
    if runtime is None or getattr(runtime, "mode", None) != "resident":
        raise RuntimeError("Release mode requires a resident runtime")

    display = runtime.nav.renderer.display
    display.sleep()
    emit(
        "APPLICATION_BEGIN rounds=5 history=20 history_chars=768 "
        "variables=16 laps=20 plugins=3")
    try:
        report = _run_matrix(runtime)
        emit(
            "APPLICATION_END rounds=" + str(report.rounds_completed)
            + " scenarios=" + str(report.scenarios_completed)
            + " runtime_steps=" + str(report.runtime_steps)
            + " memory_errors=" + str(report.memory_errors)
            + " errors=" + str(report.errors)
            + " heap_min=" + str(report.heap_min)
            + " heap_after=" + str(report.heap_after)
            + " heap_delta=" + str(report.heap_delta)
            + " blocking_max_us=" + str(report.blocking_max_us)
            + " buffer_peak_bytes=" + str(report.buffer_peak_bytes)
            + " framebuffer_bytes=8192"
            + " step_name=" + str(report.step_name)
            + " phase=" + str(report.phase)
            + " bounded_close_attempts="
            + str(report.bounded_close_attempts)
            + " bounded_session_restored="
            + str(report.bounded_session_restored)
            + " blocking_round=" + str(report.blocking_round)
            + " blocking_step=" + str(report.blocking_step)
            + " heap_step_name=" + str(report.heap_step_name)
            + " heap_phase=" + str(report.heap_phase)
            + " heap_round=" + str(report.heap_round)
            + " heap_step=" + str(report.heap_step)
            + " scenario_name=" + str(report.scenario_name)
            + " scenario_heap_before=" + str(report.scenario_heap_before)
            + " scenario_heap_after=" + str(report.scenario_heap_after)
            + " scenario_heap_delta=" + str(report.scenario_heap_delta))
        if not report.accepted:
            primary = report.primary_error
            emit(
                "APPLICATION_RESULT FAIL memory_errors="
                + str(report.memory_errors)
                + " errors=" + str(report.errors)
                + " failure_mask=" + str(report.failure_mask))
            if primary is not None:
                raise primary
            raise RuntimeError("Device application matrix failed")
    finally:
        display.sleep()

    emit("APPLICATION_RESULT PASS memory_errors=0 errors=0 failure_mask=0")
    return (
        report.heap_min,
        report.heap_delta,
        report.memory_errors,
        report.errors,
    )


if __name__ == "__main__":
    run()
