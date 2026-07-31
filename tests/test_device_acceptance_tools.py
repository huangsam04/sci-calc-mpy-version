import sys
from pathlib import Path

import pytest

import runtime_acceptance
from runtime_acceptance import (
    RuntimeHandle,
    get_resident_runtime,
    set_resident_runtime,
)

TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import device_runtime_monitor
import device_interaction_acceptance
import device_application_acceptance


@pytest.fixture(autouse=True)
def _avoid_host_animation_waits(monkeypatch):
    monkeypatch.setattr(
        device_interaction_acceptance.time, "sleep_ms", lambda _ms: None)


def test_runtime_monitor_reuses_the_warmed_navigation_runner():
    source = (TOOLS / "device_runtime_monitor.py").read_text(encoding="utf-8")

    assert "from benchmarks import run as run_navigation" in source
    assert "_binding_state" not in source
    assert ".screens" not in source


def test_interaction_device_path_does_not_load_the_generic_runner():
    source = (TOOLS / "device_interaction_acceptance.py").read_text(
        encoding="utf-8")

    assert "from runtime_acceptance import" not in source
    assert "import runtime_acceptance" not in source
    assert "from benchmarks import" not in source


def test_page_round_trip_stage_only_loads_its_navigation_transaction():
    assert device_application_acceptance._SCENARIO_MODULES[-1] == (
        "nav_scenario",
    )


def test_application_matrix_accepts_recovered_heap(monkeypatch):
    class WarmNav:
        def open(self, _page_id):
            return object()

        def back(self):
            return None

        def collect_pending(self):
            return False

    class Runtime:
        nav = WarmNav()

    import runtime_scenarios

    monkeypatch.setattr(
        runtime_scenarios, "application_scenarios", lambda rounds=5: ())
    heap = iter((10_000, 11_552))
    monkeypatch.setattr(
        device_application_acceptance.gc,
        "mem_free",
        lambda: next(heap),
        raising=False,
    )

    report = device_application_acceptance._run_matrix(Runtime())

    assert report.heap_delta == 1_552
    assert report.accepted


def test_application_matrix_rejects_heap_loss_over_512_bytes(monkeypatch):
    class WarmNav:
        def open(self, _page_id):
            return object()

        def back(self):
            return None

        def collect_pending(self):
            return False

    class Runtime:
        nav = WarmNav()

    import runtime_scenarios

    monkeypatch.setattr(
        runtime_scenarios, "application_scenarios", lambda rounds=5: ())
    heap = iter((10_000, 9_487))
    monkeypatch.setattr(
        device_application_acceptance.gc,
        "mem_free",
        lambda: next(heap),
        raising=False,
    )

    report = device_application_acceptance._run_matrix(Runtime())

    assert report.heap_delta == -513
    assert report.failure_mask & runtime_acceptance.FAIL_DRIFT
    assert not report.accepted


class _Memory:
    def __init__(self, buffers=None):
        buffers = {} if buffers is None else buffers
        self._plot_curve = buffers.get("plot_curve", bytearray(104))


class _MonitorDisplay:
    def __init__(self):
        self.gs4_buf = bytearray(8192)
        self.sleep_count = 0

    def sleep(self):
        self.sleep_count += 1


class _Renderer:
    def __init__(self, root):
        self._visible_screen = root
        self.display = _MonitorDisplay()


class _Nav:
    def __init__(self, root, replace_buffer=False, fail_navigation=False):
        self._root = root
        self.current = root
        self.memory = _Memory(
            {"plot_curve": bytearray(8)} if replace_buffer else None)
        self.renderer = _Renderer(root)
        self._replace_buffer = replace_buffer
        self._fail_navigation = fail_navigation
        self.visited = []

    def go_to(self, target):
        if self._fail_navigation:
            raise MemoryError("injected navigation failure")
        self.current = target
        self.visited.append(target)
        if self._replace_buffer:
            self.memory._plot_curve = bytearray(104)
            self._replace_buffer = False

    def go_back(self):
        self.current = self._root

    def present_current(self):
        self.renderer._visible_screen = self.current

    def settle_current(self):
        return 0

    def collect_pending(self):
        return False

    def reset(self, root):
        self.current = root

    def poll_event(self, keyboard):
        return keyboard.pop_key_event()


class _Target:
    transition_title = "Calculator"


class _FailingPresentNav(_Nav):
    def present_current(self):
        raise RuntimeError("injected present failure")


class _InputBox:
    def __init__(self, value):
        self.value = value

    def get_str(self):
        return self.value

    def set_str(self, value, immediate=False):
        self.value = value

    def clear_str(self):
        self.value = ""


class _PlotTarget:
    transition_title = "Plot"

    def __init__(self, expression):
        self.expr = expression
        self.input_box = _InputBox(expression)


class _MenuState:
    def __init__(self):
        self._state = [None] * 6
        self._state[5] = [(str(index), object()) for index in range(5)]
        self.cursor_pos = 0
        self.view_offset = 0

    def activate(self):
        pass


class _InteractionRoot:
    def __init__(self, log):
        self.log = log
        self.menu = _MenuState()
        self._motion_steps = 0

    def activate(self):
        self.log.append("activate:root")

    def update(self, keyboard, event):
        self.log.append("update:root")
        before = self.menu.cursor_pos
        if event[:2] == (3, 1):
            self.menu.cursor_pos = min(
                len(self.menu._state[5]) - 1, self.menu.cursor_pos + 1)
        elif event[:2] == (1, 1):
            self.menu.cursor_pos = max(0, self.menu.cursor_pos - 1)
        if self.menu.cursor_pos != before:
            self._motion_steps = 2

    @property
    def motion_active(self):
        return self._motion_steps > 0

    def advance_motion(self, now):
        self._motion_steps -= 1
        self.log.append("motion:root")
        return True


class _NoOpInteractionRoot(_InteractionRoot):
    def update(self, keyboard, event):
        self.log.append("update:root")


class _CalculatorTarget:
    transition_title = "Calculator"

    def __init__(self, log, value=""):
        self.log = log
        self.mode = 0
        self.input_box = _InputBox(value)

    def update(self, keyboard, event):
        self.log.append("update:calculator")
        digits = {
            (3, 0): "1",
            (3, 1): "2",
            (3, 2): "3",
            (2, 0): "4",
            (2, 1): "5",
        }
        key = event[:2]
        if key in digits:
            self.input_box.set_str(
                self.input_box.get_str() + digits[key], immediate=True)
        elif key == (4, 3):
            self.input_box.set_str(
                self.input_box.get_str()[:-1], immediate=True)


class _FailingCalculatorTarget(_CalculatorTarget):
    def update(self, keyboard, event):
        super().update(keyboard, event)
        raise RuntimeError("injected calculator update failure")


class _InteractionDisplay:
    def __init__(self, log):
        self.log = log

    def wake(self):
        self.log.append("display:wake")

    def sleep(self):
        self.log.append("display:sleep")


class _InteractionRenderer(_Renderer):
    def __init__(self, root, log):
        super().__init__(root)
        self.log = log
        self.display = _InteractionDisplay(log)

    def invalidate(self):
        pass

    def present(self, screen):
        self._visible_screen = screen
        name = (
            "root" if isinstance(screen, _InteractionRoot)
            else "calculator")
        self.log.append("present:" + name)


class _InteractionNav(_Nav):
    def __init__(self, root, log):
        super().__init__(root)
        self.log = log
        self.renderer = _InteractionRenderer(root, log)
        self._motion_steps = 0

    def go_to(self, target):
        self.current = target
        self.log.append("go_to:calculator")

    def open(self, page_id, trigger_event=None):
        assert page_id == 1
        self.go_to(self.calculator)
        if trigger_event is not None:
            self._motion_steps = 2
        return self.calculator

    def go_back(self):
        self.current = self._root
        self.log.append("go_back:root")

    def back(self, trigger_event=None):
        self.go_back()
        if trigger_event is not None:
            self._motion_steps = 2

    @property
    def motion_active(self):
        return self._motion_steps > 0

    def present_current(self, now=None):
        if self._motion_steps:
            self._motion_steps -= 1
            self.log.append("motion:page")
        self.renderer.present(self.current)

    def settle_current(self):
        name = (
            "root" if isinstance(self.current, _InteractionRoot)
            else "calculator")
        self.log.append("settle:" + name)
        return 0

    def collect_pending(self):
        self.log.append("collect:root")
        return False


class _CollectRequestedInteractionNav(_InteractionNav):
    def settle_current(self):
        from ui.element import SETTLE_COLLECT

        super().settle_current()
        return SETTLE_COLLECT


class _InteractionBinding:
    def __init__(self, nav, root, calculator):
        self.mode = "resident"
        self.nav = nav
        self.root = root
        nav.calculator = calculator


_ROOT = object()


def test_runtime_monitor_release_mode_requires_a_resident_runtime():
    previous = get_resident_runtime()
    set_resident_runtime(None)
    try:
        with pytest.raises(RuntimeError, match="resident runtime"):
            device_runtime_monitor.run(
                runtime=None,
                emit=lambda _line: None,
            )
    finally:
        set_resident_runtime(previous)


def test_runtime_monitor_labels_the_resident_run():
    lines = []

    device_runtime_monitor.run(
        runtime=RuntimeHandle(
            _Nav(_ROOT), _ROOT, (_Target(),), mode="resident"),
        emit=lines.append,
    )

    assert lines
    assert "mode=resident" in lines[0]


def test_runtime_monitor_rejects_gc_heap_drift_over_512_bytes(monkeypatch):
    nav = _Nav(_ROOT)

    def mem_free():
        if not nav.visited:
            return 16_384
        if len(nav.visited) == 1:
            return 15_784
        return 15_184

    monkeypatch.setattr(runtime_acceptance.gc, "mem_free", mem_free,
                        raising=False)

    with pytest.raises(RuntimeError, match="acceptance failed"):
        device_runtime_monitor.run(
            runtime=RuntimeHandle(
                nav, _ROOT, (_Target(),), mode="resident"),
            emit=lambda _line: None,
        )


def test_runtime_monitor_rejects_same_named_buffer_replacement():
    with pytest.raises(RuntimeError, match="acceptance failed"):
        device_runtime_monitor.run(
            runtime=RuntimeHandle(
                _Nav(_ROOT, replace_buffer=True),
                _ROOT,
                (_Target(),),
                mode="resident",
            ),
            emit=lambda _line: None,
        )


def test_runtime_monitor_restores_the_users_plot_expression():
    plot = _PlotTarget("sin(x)")

    device_runtime_monitor.run(
        runtime=RuntimeHandle(
            _Nav(_ROOT), _ROOT, (plot,), mode="resident"),
        emit=lambda _line: None,
    )

    assert plot.expr == "sin(x)"
    assert plot.input_box.get_str() == "sin(x)"


def test_runtime_monitor_restores_plot_state_after_memory_error():
    plot = _PlotTarget("cos(x)")

    with pytest.raises(RuntimeError, match="acceptance failed"):
        device_runtime_monitor.run(
            runtime=RuntimeHandle(
                _Nav(_ROOT, fail_navigation=True),
                _ROOT,
                (plot,),
                mode="resident",
            ),
            emit=lambda _line: None,
        )

    assert plot.expr == "cos(x)"
    assert plot.input_box.get_str() == "cos(x)"


def test_runtime_monitor_warms_each_target_before_five_measured_rounds():
    nav = _Nav(_ROOT)
    first = _Target()
    second = _Target()

    device_runtime_monitor.run(
        runtime=RuntimeHandle(
            nav, _ROOT, (first, second), mode="resident"),
        emit=lambda _line: None,
    )

    assert nav.visited == [first, second] * 6


def test_runtime_monitor_returns_to_root_after_unexpected_failure():
    nav = _FailingPresentNav(_ROOT)

    with pytest.raises(RuntimeError, match="acceptance failed"):
        device_runtime_monitor.run(
            runtime=RuntimeHandle(
                nav, _ROOT, (_Target(),), mode="resident"),
            emit=lambda _line: None,
        )

    assert nav.current is _ROOT


def test_interaction_release_mode_requires_a_resident_runtime():
    previous = get_resident_runtime()
    set_resident_runtime(None)
    try:
        with pytest.raises(RuntimeError, match="resident runtime"):
            device_interaction_acceptance.run(
                runtime=None,
                emit=lambda _line: None,
            )
    finally:
        set_resident_runtime(previous)


def test_interaction_scenario_keeps_animation_frames_in_the_existing_tracer():
    source = (TOOLS / "device_interaction_acceptance.py").read_text(
        encoding="utf-8")

    assert device_interaction_acceptance.MAX_BLOCKING_STEP_US == 40_000
    assert "animation_frames=" in source
    assert "animation_alloc_nonzero=" in source
    assert "def _scenario(" not in source


def test_interaction_presents_captured_edges_before_quiet_settle_work():
    log = []
    root = _InteractionRoot(log)
    calculator = _CalculatorTarget(log)
    runtime = _InteractionBinding(
        _InteractionNav(root, log), root, calculator)

    report = device_interaction_acceptance.run(
        runtime=runtime,
        emit=lambda _line: None,
    )

    first_root_update = log.index("update:root")
    root_present = log.index("present:root", first_root_update)
    root_settle = log.index("settle:root", first_root_update)
    first_calc_update = log.index("update:calculator")
    calc_present = log.index("present:calculator", first_calc_update)
    calc_settle = log.index("settle:calculator", first_calc_update)
    first_root_collect = log.index("collect:root", first_root_update)
    assert first_root_update < root_present < root_settle
    assert first_calc_update < calc_present < calc_settle
    assert first_root_collect > calc_present
    assert log.count("update:root") == 5
    assert log.count("update:calculator") == 25
    assert log.count("collect:root") == 6
    assert report[2] == 5


def test_interaction_times_visible_frames_with_oled_awake():
    log = []
    root = _InteractionRoot(log)
    calculator = _CalculatorTarget(log)
    runtime = _InteractionBinding(
        _InteractionNav(root, log), root, calculator)

    device_interaction_acceptance.run(
        runtime=runtime,
        emit=lambda _line: None,
    )

    assert log.count("display:wake") == 1
    assert log.count("display:sleep") == 1
    assert log.index("display:wake") < log.index("update:root")
    assert log[-1] == "display:sleep"


def test_interaction_defers_requested_gc_until_after_visible_commit(
        monkeypatch):
    log = []
    root = _InteractionRoot(log)
    calculator = _CalculatorTarget(log)
    runtime = _InteractionBinding(
        _CollectRequestedInteractionNav(root, log), root, calculator)
    monkeypatch.setattr(
        device_interaction_acceptance.gc,
        "collect",
        lambda: log.append("gc"),
    )

    device_interaction_acceptance.run(
        runtime=runtime,
        emit=lambda _line: None,
    )

    first_update = log.index("update:root")
    first_present = log.index("present:root", first_update)
    first_gc = log.index("gc", first_update)
    assert first_update < first_present < first_gc


def test_interaction_rejects_a_dispatched_menu_edge_that_does_not_move_cursor():
    log = []
    root = _NoOpInteractionRoot(log)
    calculator = _CalculatorTarget(log)
    runtime = _InteractionBinding(
        _InteractionNav(root, log), root, calculator)

    with pytest.raises(RuntimeError, match="interaction"):
        device_interaction_acceptance.run(
            runtime=runtime,
            emit=lambda _line: None,
        )


def test_interaction_restores_user_state_after_update_failure():
    log = []
    root = _InteractionRoot(log)
    root.menu.cursor_pos = 3
    root.menu.view_offset = 1
    calculator = _FailingCalculatorTarget(log, value="9+8")
    nav = _InteractionNav(root, log)
    runtime = _InteractionBinding(nav, root, calculator)

    with pytest.raises(RuntimeError, match="interaction screen tracer failed"):
        device_interaction_acceptance.run(
            runtime=runtime,
            emit=lambda _line: None,
        )

    assert calculator.input_box.get_str() == "9+8"
    assert root.menu.cursor_pos == 3
    assert root.menu.view_offset == 1
    assert nav.current is root
    assert nav.renderer._visible_screen is root


def test_interaction_screen_tracer_reports_its_narrow_measurement_scope():
    log = []
    lines = []
    root = _InteractionRoot(log)
    calculator = _CalculatorTarget(log)
    runtime = _InteractionBinding(
        _InteractionNav(root, log), root, calculator)

    device_interaction_acceptance.run(
        runtime=runtime,
        emit=lines.append,
    )

    text = "\n".join(lines)
    assert lines[0].startswith("INTERACTION_SCREEN_TRACER_START ")
    assert "mode=resident" in lines[0]
    assert (
        "coverage=captured_edge_to_screen_update_present"
        in lines[0]
    )
    assert "scan_interval_us=8000" in lines[0]
    assert "debounce_us=8000" in lines[0]
    assert "edge_to_present_max_us=" in text
    assert "heap_after=" in text
    assert "heap_delta=" in text
    assert "input_batch_us" not in text
    assert "INTERACTION_ACCEPTANCE" not in text
    assert lines[-1].startswith("INTERACTION_SCREEN_TRACER_RESULT PASS ")
    assert "animation_frames=" in text
    assert "animation_max_us=" in text
    assert "animation_alloc_nonzero=0" in text


def test_interaction_rejects_a_nonzero_animation_frame_heap_delta(
        monkeypatch):
    calls = [0]

    def mem_alloc():
        calls[0] += 1
        return 100 if calls[0] == 1 else 101

    monkeypatch.setattr(
        device_interaction_acceptance.gc, "mem_alloc", mem_alloc,
        raising=False)
    lines = []
    root = _InteractionRoot([])
    calculator = _CalculatorTarget([])
    runtime = _InteractionBinding(
        _InteractionNav(root, []), root, calculator)

    with pytest.raises(RuntimeError, match="interaction screen tracer failed"):
        device_interaction_acceptance.run(runtime=runtime, emit=lines.append)

    assert "animation_alloc_nonzero=1" in lines[-2]
    assert lines[-1].startswith("INTERACTION_SCREEN_TRACER_RESULT FAIL ")


def test_interaction_requires_captured_edge_frames_below_20_ms(monkeypatch):
    now = [0]

    def ticks_us():
        value = now[0]
        now[0] += 20_000
        return value

    monkeypatch.setattr(
        device_interaction_acceptance.time, "ticks_us", ticks_us)
    monkeypatch.setattr(
        device_interaction_acceptance.time,
        "ticks_diff",
        lambda end, start: end - start,
    )
    log = []
    root = _InteractionRoot(log)
    calculator = _CalculatorTarget(log)
    runtime = _InteractionBinding(
        _InteractionNav(root, log), root, calculator)

    with pytest.raises(RuntimeError, match="interaction screen tracer failed"):
        device_interaction_acceptance.run(
            runtime=runtime,
            emit=lambda _line: None,
        )
