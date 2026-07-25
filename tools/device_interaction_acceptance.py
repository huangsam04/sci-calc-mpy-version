"""Narrow captured-edge-to-screen tracer for the resident device runtime."""
import gc
import sys


if "/sd" not in sys.path:
    sys.path.insert(0, "/sd")


TOTAL_ROUNDS = 5
MAX_INPUT_FRAME_US = 20_000
MAX_SETTLE_STEPS = 256
MODE_RELEASE = "release"
MODE_BENCHMARK = "benchmark"
SCENARIO_NAME = "interaction_screen_tracer"

_MENU_DOWN = ((3, 1, False),)
_MENU_UP = ((1, 1, False),)
_DIGIT_EVENTS = (
    (3, 0, False),
    (3, 1, False),
    (3, 2, False),
    (2, 0, False),
    (2, 1, False),
)


def _resident_runtime():
    from runtime_acceptance import get_resident_runtime

    return get_resident_runtime()


def _benchmark_runtime():
    from benchmarks import build_runtime

    return build_runtime(mode=MODE_BENCHMARK)


def _resolve_runtime(runtime, mode):
    if mode not in (MODE_RELEASE, MODE_BENCHMARK):
        raise ValueError("Unknown interaction mode: " + str(mode))
    if runtime is None:
        runtime = (
            _resident_runtime()
            if mode == MODE_RELEASE
            else _benchmark_runtime())
    if runtime is None:
        if mode == MODE_RELEASE:
            raise RuntimeError("Release mode requires a resident runtime")
        raise RuntimeError("Benchmark runtime build failed")
    expected_mode = "resident" if mode == MODE_RELEASE else MODE_BENCHMARK
    if getattr(runtime, "mode", None) != expected_mode:
        raise RuntimeError(
            mode + " mode requires a " + expected_mode + " runtime")
    return runtime


def _buffer_text(buffers):
    if not buffers:
        return "-"
    return ";".join(
        name + ":" + str(length) + ":" + str(identity)
        for name, length, identity in buffers)


class QueuedKeyboard:
    """Fixed tuple Adapter for edges already captured by the matrix scanner."""

    __slots__ = ("events", "index")

    def __init__(self, events):
        self.events = events if isinstance(events, tuple) else tuple(events)
        self.index = 0

    def reset(self, events):
        self.events = events if isinstance(events, tuple) else tuple(events)
        self.index = 0
        return self

    def pop_key_event(self):
        index = self.index
        if index >= len(self.events):
            return None
        self.index = index + 1
        return self.events[index]

    def any_pressed(self):
        return False

    def is_pressed(self, row, col):
        return False

    def get_hold_time(self, row, col):
        return 0

    def consume_long_press(self, row, col, threshold):
        return False


class _InteractionActions:
    __slots__ = (
        "calculator", "drain_input", "saved_input", "_menu_keyboard",
        "_calculator_keyboard", "_active_keyboard", "_active_screen",
        "_stable_handler")

    def __init__(self, calculator, drain_input):
        self.calculator = calculator
        self.drain_input = drain_input
        self.saved_input = calculator.input_box.get_str()
        self._menu_keyboard = QueuedKeyboard(_MENU_DOWN)
        self._calculator_keyboard = QueuedKeyboard(_DIGIT_EVENTS)
        self._active_keyboard = None
        self._active_screen = None
        self._stable_handler = self._update_active_screen

    def _update_active_screen(self, event):
        return self._active_screen.update(
            self._active_keyboard, event)

    @staticmethod
    def quiet_settle(runtime, round_index):
        from ui.element import SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW

        nav = runtime.nav
        redraw = False
        for _ in range(MAX_SETTLE_STEPS):
            flags = nav.settle_current()
            if flags & SETTLE_COLLECT:
                gc.collect()
            if flags & SETTLE_REDRAW:
                redraw = True
            if not flags & SETTLE_MORE:
                if redraw:
                    nav.present_current()
                return
        raise RuntimeError("Quiet settle work exceeded its fixed bound")

    def menu_edges(self, runtime, round_index):
        root = runtime.root
        menu = root.menu
        cursor_before = menu.cursor_pos
        events = (
            _MENU_DOWN
            if menu.cursor_pos < len(menu.items) - 1
            else _MENU_UP)
        keyboard = self._menu_keyboard.reset(events)
        self._active_keyboard = keyboard
        self._active_screen = root
        handled = self.drain_input(
            runtime.nav,
            keyboard,
            self._stable_handler,
        )
        if handled != len(events):
            raise AssertionError("Captured menu edges were not all dispatched")
        if menu.cursor_pos == cursor_before:
            raise AssertionError(
                "Captured menu edge did not move the visible cursor")
        runtime.nav.present_current()

    def open_calculator(self, runtime, round_index):
        self.calculator.input_box.clear_str()
        runtime.nav.go_to(self.calculator)
        runtime.nav.present_current()

    def calculator_edges(self, runtime, round_index):
        keyboard = self._calculator_keyboard.reset(_DIGIT_EVENTS)
        self._active_keyboard = keyboard
        self._active_screen = self.calculator
        handled = self.drain_input(
            runtime.nav,
            keyboard,
            self._stable_handler,
        )
        if handled != len(_DIGIT_EVENTS):
            raise AssertionError(
                "Captured Calculator edges were not all dispatched")
        if self.calculator.input_box.get_str() != "12345":
            raise AssertionError("Five-digit captured edge batch was lost")
        runtime.nav.present_current()

    def close_calculator(self, runtime, round_index):
        self.calculator.input_box.set_str(
            self.saved_input, immediate=True)
        runtime.nav.go_back()
        runtime.nav.present_current()


class _InteractionObserver:
    __slots__ = ("emit", "edge_max_us")

    def __init__(self, emit):
        self.emit = emit
        self.edge_max_us = 0

    def __call__(self, event, report):
        from runtime_acceptance import (
            RUN_END, RUN_ERROR, RUN_MEMORY_ERROR, RUN_START, RUN_STEP)

        if event == RUN_START:
            from input.keyboard import DEBOUNCE_MS, SCAN_INTERVAL

            self.emit(
                "INTERACTION_SCREEN_TRACER_START"
                + " tool=interaction_screen_tracer"
                + " mode=" + report.mode
                + " rounds=" + str(report.rounds_expected)
                + " coverage=captured_edge_to_screen_update_present"
                + " main_dispatch=not_measured"
                + " scan_debounce=contract_only"
                + " scan_interval_us=" + str(SCAN_INTERVAL * 1000)
                + " debounce_us=" + str(DEBOUNCE_MS * 1000)
                + " heap_before=" + str(report.heap_before)
                + " buffers=" + _buffer_text(report.buffers_before))
            return

        if event in (RUN_STEP, RUN_MEMORY_ERROR, RUN_ERROR):
            edge_step = report.step_name in (
                "menu_edge_to_present",
                "calculator_edge_to_present",
            )
            if edge_step and report.step_us > self.edge_max_us:
                self.edge_max_us = report.step_us
            timing_name = (
                "edge_to_present_us" if edge_step else "step_us")
            self.emit(
                "INTERACTION_SCREEN_TRACER_STEP event=" + str(event)
                + " round=" + str(report.round_index + 1)
                + " name=" + str(report.step_name)
                + " " + timing_name + "=" + str(report.step_us)
                + " heap_free=" + str(report.step_heap_free)
                + " heap_min=" + str(report.heap_min)
                + " buffers=" + _buffer_text(report.step_buffers))
            return

        if event == RUN_END:
            self.emit(
                "INTERACTION_SCREEN_TRACER_END rounds_completed="
                + str(report.rounds_completed)
                + " runtime_steps=" + str(report.runtime_steps)
                + " memory_errors=" + str(report.memory_errors)
                + " errors=" + str(report.errors)
                + " edge_to_present_max_us=" + str(self.edge_max_us)
                + " blocking_max_us=" + str(report.blocking_max_us)
                + " heap_after=" + str(report.heap_after)
                + " heap_delta=" + str(report.heap_delta)
                + " heap_min=" + str(report.heap_min)
                + " buffer_peak_bytes=" + str(report.buffer_peak_bytes)
                + " buffer_changes=" + str(report.buffer_change_count)
                + " buffers_before=" + _buffer_text(
                    report.buffers_before)
                + " buffers_after=" + _buffer_text(
                    report.buffers_after))


def _scenario(actions):
    from runtime_acceptance import RUN_ACTION

    return (
        SCENARIO_NAME,
        TOTAL_ROUNDS,
        (
            ("menu_edge_to_present", RUN_ACTION, actions.menu_edges),
            ("menu_quiet_settle", RUN_ACTION, actions.quiet_settle),
            ("calculator_open", RUN_ACTION, actions.open_calculator),
            (
                "calculator_edge_to_present",
                RUN_ACTION,
                actions.calculator_edges,
            ),
            (
                "calculator_quiet_settle",
                RUN_ACTION,
                actions.quiet_settle,
            ),
            ("calculator_close", RUN_ACTION, actions.close_calculator),
        ),
    )


def run(runtime=None, mode=MODE_RELEASE, emit=print):
    """Trace captured edges through screen update and visible presentation."""
    from main import _drain_input_batch
    from runtime_acceptance import run as run_acceptance

    runtime = _resolve_runtime(runtime, mode)
    calculator = runtime.find_target("Calculator")
    root = runtime.root
    if calculator is None or not hasattr(root, "menu"):
        raise RuntimeError(
            "Resident menu or Calculator screen is unavailable")
    if getattr(calculator, "mode", None) != 0:
        raise RuntimeError(
            "Calculator must be in input mode for interaction tracing")

    menu = root.menu
    if not menu.items:
        raise RuntimeError("Main menu has no entries")
    saved_cursor = menu.cursor_pos
    saved_offset = menu.view_offset
    actions = _InteractionActions(calculator, _drain_input_batch)
    observer = _InteractionObserver(emit)
    report = None
    try:
        report = run_acceptance(
            runtime, _scenario(actions), observer)
    finally:
        calculator.input_box.set_str(
            actions.saved_input, immediate=True)
        menu.cursor_pos = saved_cursor
        menu.view_offset = saved_offset
        root.activate()
        runtime.reset_root(present=True)

    accepted = (
        report.accepted
        and observer.edge_max_us < MAX_INPUT_FRAME_US)
    emit("INTERACTION_SCREEN_TRACER_RESULT "
         + ("PASS" if accepted else "FAIL")
         + " failure_mask=" + str(report.failure_mask)
         + " edge_to_present_max_us=" + str(observer.edge_max_us))
    if not accepted:
        raise RuntimeError("Device interaction screen tracer failed")
    return report


if __name__ == "__main__":
    run()
