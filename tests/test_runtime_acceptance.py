import sys

import pytest

import runtime_acceptance as acceptance

from runtime_acceptance import (
    RUN_ACTION,
    RUN_BOUNDED,
    STEP_DONE,
    STEP_MORE,
    STEP_WAIT,
    VISIT_TARGET,
    RuntimeHandle,
    get_resident_runtime,
    run,
    set_resident_runtime,
)


def test_runtime_heap_gate_matches_the_animation_contract():
    assert acceptance.MIN_HEAP_FREE_BYTES == 12 * 1024
    assert acceptance.MAX_BLOCKING_STEP_US == 40_000


class _Renderer:
    def __init__(self, visible):
        self._visible_screen = visible


class _Memory:
    def __init__(self, buffers):
        self._plot_curve = buffers.get("plot_curve")


class _ResidentNav:
    def __init__(self, root, buffers):
        self.current = root
        self.renderer = _Renderer(root)
        self.memory = _Memory(buffers)
        main_buffer = buffers.get("main")
        if main_buffer is not None:
            self.renderer.display = type(
                "Display", (), {"gs4_buf": main_buffer})()
        self.resets = 0
        self.presents = 0

    def reset(self, root):
        self.current = root
        self.resets += 1

    def present_current(self):
        self.renderer._visible_screen = self.current
        self.presents += 1


def test_resident_runtime_handle_exposes_identity_without_building_another_ui():
    root = object()
    target = object()
    framebuffer = bytearray(8)
    nav = _ResidentNav(root, {"main": framebuffer})
    targets = (target,)

    handle = RuntimeHandle(
        nav, root, targets, mode="resident", version="1.3.0")
    set_resident_runtime(handle)

    assert get_resident_runtime() is handle
    assert handle.nav is nav
    assert handle.root is root
    assert handle.targets is targets
    assert handle.mode == "resident"
    assert handle.version == "1.3.0"
    assert handle.at_root()
    assert handle.root_visible()
    assert handle.buffer_snapshot() == (
        ("main", len(framebuffer), id(framebuffer)),
    )
    assert nav.resets == 0
    assert nav.presents == 0


def test_benchmark_handle_cannot_replace_the_resident_runtime():
    resident = RuntimeHandle(
        _ResidentNav("root", {}), "root", (), mode="resident")
    set_resident_runtime(resident)
    benchmark = RuntimeHandle(
        _ResidentNav("root", {}), "root", (), mode="benchmark")

    with pytest.raises(ValueError, match="resident"):
        set_resident_runtime(benchmark)

    assert get_resident_runtime() is resident


def test_buffer_snapshot_includes_the_single_display_framebuffer():
    root = object()
    main_buffer = bytearray(8192)
    curve_buffer = bytearray(104)
    nav = _ResidentNav(root, {"plot_curve": curve_buffer})
    nav.renderer.display = type("Display", (), {"gs4_buf": main_buffer})()
    runtime = RuntimeHandle(nav, root, ())

    assert runtime.buffer_snapshot() == (
        ("main", len(main_buffer), id(main_buffer)),
        ("plot_curve", len(curve_buffer), id(curve_buffer)),
    )


def test_buffer_snapshot_reuses_identity_until_a_live_buffer_changes():
    root = object()
    main_buffer = bytearray(8192)
    nav = _ResidentNav(root, {"main": main_buffer})
    runtime = RuntimeHandle(nav, root, ())

    baseline = runtime.buffer_snapshot()
    assert runtime.buffer_snapshot() is baseline

    curve_buffer = bytearray(104)
    nav.memory._plot_curve = curve_buffer
    with_curve = runtime.buffer_snapshot()
    assert with_curve is not baseline
    assert runtime.buffer_snapshot() is with_curve

    nav.memory._plot_curve = None
    restored = runtime.buffer_snapshot()
    assert restored == baseline
    assert restored is not with_curve
    assert runtime.buffer_snapshot() is restored


class _InMemoryNav:
    def __init__(self, root):
        self.current = root
        self.visited = []
        self.presents = []
        self.memory = _Memory({})

    def reset(self, root):
        self.current = root

    def go_to(self, target):
        self.current = target
        self.visited.append(target)

    def go_back(self):
        self.current = "root"

    def present_current(self):
        self.presents.append(self.current)

    def settle_current(self):
        return 0


class _ResetTrackingRuntime(RuntimeHandle):
    __slots__ = ("reset_calls",)

    def __init__(self, nav, root):
        RuntimeHandle.__init__(self, nav, root, (), mode="in_memory")
        self.reset_calls = []

    def reset_root(self, present=True):
        self.reset_calls.append(present)
        return RuntimeHandle.reset_root(self, present)


def _bounded_measurement(monkeypatch, physical_steps):
    clock = iter(range(physical_steps * 2))
    heap = iter((16_000,) * (physical_steps + 2))
    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)


def test_bounded_acceptance_code_loads_only_for_a_bounded_scenario():
    sys.modules.pop("runtime_acceptance_bounded", None)
    runtime = RuntimeHandle(
        _InMemoryNav("root"), "root", (), mode="in_memory")

    run(runtime, ("empty", 0, ()))

    assert "runtime_acceptance_bounded" not in sys.modules

    class Session:
        capabilities = ("bounded",)

    session = Session()
    run(runtime, (
        "bounded", 0, (("bounded", RUN_BOUNDED, session),)))

    assert "runtime_acceptance_bounded" in sys.modules


def test_runner_repeats_the_complete_scenario_matrix_each_round():
    root = "root"
    targets = ("alpha", "beta")
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, targets, mode="in_memory")
    scenario = (
        "navigation",
        2,
        (
            ("alpha", VISIT_TARGET, 0),
            ("beta", VISIT_TARGET, 1),
        ),
    )

    report = run(runtime, scenario)

    assert nav.visited == ["alpha", "beta", "alpha", "beta"]
    assert nav.current is root
    assert report.scenario_name == "navigation"
    assert report.mode == "in_memory"
    assert report.rounds_expected == 2
    assert report.rounds_completed == 2
    assert report.scenarios_completed == 4
    assert report.accepted


@pytest.mark.parametrize(
    ("mode", "rounds"),
    (("resident", 1), ("release", 6)),
)
def test_release_runtime_rejects_a_non_release_round_count(mode, rounds):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode=mode)

    with pytest.raises(ValueError, match="5 rounds"):
        run(runtime, ("release_gate", rounds, ()))


def test_runner_observes_end_to_end_step_time_and_gc_stable_heap(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    clock = iter((100, 200))
    heap = iter((16_000, 15_000, 15_500))
    collections = []
    action_rounds = []
    observed = []

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(
        acceptance.gc, "collect", lambda: collections.append(True))

    def action(handle, round_index):
        assert handle is runtime
        action_rounds.append(round_index)

    def observer(event, report):
        observed.append((
            event,
            report.round_index,
            report.step_name,
            report.step_us,
            report.step_heap_free,
        ))

    report = run(
        runtime,
        ("input", 1, (("edge_to_present", RUN_ACTION, action),)),
        observer,
    )

    assert action_rounds == [0]
    assert [event[0] for event in observed] == [
        acceptance.RUN_START,
        acceptance.RUN_STEP,
        acceptance.RUN_END,
    ]
    assert observed[1] == (
        acceptance.RUN_STEP, 0, "edge_to_present", 100, 15_000)
    assert report.runtime_steps == 1
    assert report.blocking_max_us == 100
    assert report.heap_before == 16_000
    assert report.heap_min == 15_000
    assert report.heap_after == 15_500
    assert report.heap_delta == -500
    assert collections == [True, True]
    assert report.accepted


def test_runner_rejects_a_step_at_the_strict_40_ms_boundary(monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    clock = iter((100, 40_100))
    heap = iter((16_000, 16_000, 16_000))

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("strict_blocking_limit", 1,
         (("step", RUN_ACTION, lambda handle, round_index: None),)),
    )

    assert report.blocking_max_us == acceptance.MAX_BLOCKING_STEP_US
    assert report.failure_mask & acceptance.FAIL_BLOCKING
    assert not report.accepted


def test_runner_records_memory_error_and_recovers_root(monkeypatch):
    root = "root"
    target = "calculator"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (target,), mode="in_memory")
    clock = iter((10, 60))
    heap = iter((16_000, 14_000, 15_800))
    events = []

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    def exhaust_heap(handle, round_index):
        handle.nav.go_to(target)
        raise MemoryError

    report = run(
        runtime,
        ("oom", 1, (("calculator_input", RUN_ACTION, exhaust_heap),)),
        lambda event, report: events.append(event),
    )

    assert events == [
        acceptance.RUN_START,
        acceptance.RUN_MEMORY_ERROR,
        acceptance.RUN_END,
    ]
    assert report.memory_errors == 1
    assert report.errors == 0
    assert report.runtime_steps == 1
    assert report.blocking_max_us == 50
    assert report.heap_min == 14_000
    assert report.failure_mask & acceptance.FAIL_MEMORY
    assert not report.accepted
    assert nav.current is root
    assert nav.presents[-1] is root


def test_failing_enter_keeps_its_phase_start_and_recovers_root(monkeypatch):
    root = "root"
    target = "plot"

    class FailingEnterNav(_InMemoryNav):
        def go_to(self, selected):
            raise RuntimeError("enter failed")

    nav = FailingEnterNav(root)
    runtime = RuntimeHandle(nav, root, (target,), mode="in_memory")
    clock = iter((100, 40_100, 40_150))
    heap = iter((16_000, 16_000, 16_000))
    failures = []

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("enter_failure", 1, (("plot", VISIT_TARGET, 0),)),
        lambda event, current: (
            failures.append(
                (event, current.phase, current.step_us))
            if event == acceptance.RUN_ERROR else None),
    )

    assert failures == [(
        acceptance.RUN_ERROR,
        acceptance.PHASE_ENTER,
        acceptance.MAX_BLOCKING_STEP_US,
    )]
    assert report.errors == 1
    assert report.runtime_steps == 1
    assert report.failure_mask & acceptance.FAIL_ERROR
    assert report.failure_mask & acceptance.FAIL_BLOCKING
    assert nav.current is root
    assert nav.presents == [root]


def test_failing_present_is_recorded_once_in_its_enter_phase(monkeypatch):
    root = "root"
    target = "plot"

    class FailingPresentNav(_InMemoryNav):
        def present_current(self):
            if self.current is target:
                raise MemoryError
            super().present_current()

    nav = FailingPresentNav(root)
    runtime = RuntimeHandle(nav, root, (target,), mode="in_memory")
    clock = iter((100, 40_100))
    heap = iter((16_000, 16_000, 16_000))
    failures = []

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("present_failure", 1, (("plot", VISIT_TARGET, 0),)),
        lambda event, current: (
            failures.append(
                (event, current.phase, current.step_us))
            if event == acceptance.RUN_MEMORY_ERROR else None),
    )

    assert failures == [(
        acceptance.RUN_MEMORY_ERROR,
        acceptance.PHASE_ENTER,
        acceptance.MAX_BLOCKING_STEP_US,
    )]
    assert report.memory_errors == 1
    assert report.errors == 0
    assert report.runtime_steps == 1
    assert report.scenarios_completed == 0
    assert report.failure_mask & acceptance.FAIL_MEMORY
    assert report.failure_mask & acceptance.FAIL_BLOCKING
    assert nav.current is root
    assert nav.presents == [root]


def test_failing_settle_keeps_its_phase_start_and_recovers_root(monkeypatch):
    root = "root"
    target = "plot"

    class FailingSettleNav(_InMemoryNav):
        def settle_current(self):
            raise RuntimeError("settle failed")

    nav = FailingSettleNav(root)
    runtime = RuntimeHandle(nav, root, (target,), mode="in_memory")
    clock = iter((0, 10, 100, 40_100, 40_150))
    heap = iter((16_000, 16_000, 16_000, 16_000))
    failures = []

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("settle_failure", 1, (("plot", VISIT_TARGET, 0),)),
        lambda event, current: (
            failures.append(
                (event, current.phase, current.step_us))
            if event == acceptance.RUN_ERROR else None),
    )

    assert failures == [(
        acceptance.RUN_ERROR,
        acceptance.PHASE_SETTLE,
        acceptance.MAX_BLOCKING_STEP_US,
    )]
    assert report.errors == 1
    assert report.runtime_steps == 2
    assert report.scenarios_completed == 0
    assert report.failure_mask & acceptance.FAIL_ERROR
    assert report.failure_mask & acceptance.FAIL_BLOCKING
    assert nav.current is root
    assert nav.presents == [target, root]


def test_failing_back_keeps_its_phase_start_and_recovers_root(monkeypatch):
    root = "root"
    target = "plot"

    class FailingBackNav(_InMemoryNav):
        def go_back(self):
            raise RuntimeError("back failed")

    nav = FailingBackNav(root)
    runtime = RuntimeHandle(nav, root, (target,), mode="in_memory")
    clock = iter((0, 10, 20, 30, 100, 40_100, 40_150))
    heap = iter((16_000, 16_000, 16_000, 16_000, 16_000))
    failures = []

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("back_failure", 1, (("plot", VISIT_TARGET, 0),)),
        lambda event, current: (
            failures.append(
                (event, current.phase, current.step_us))
            if event == acceptance.RUN_ERROR else None),
    )

    assert failures == [(
        acceptance.RUN_ERROR,
        acceptance.PHASE_BACK,
        acceptance.MAX_BLOCKING_STEP_US,
    )]
    assert report.errors == 1
    assert report.runtime_steps == 3
    assert report.scenarios_completed == 0
    assert report.failure_mask & acceptance.FAIL_ERROR
    assert report.failure_mask & acceptance.FAIL_BLOCKING
    assert nav.current is root
    assert nav.presents == [target, root]


def test_failing_collect_keeps_its_phase_start_and_recovers_root(monkeypatch):
    root = "root"
    target = "plot"

    class FailingCollectNav(_InMemoryNav):
        def collect_pending(self):
            raise RuntimeError("collect failed")

    nav = FailingCollectNav(root)
    runtime = RuntimeHandle(nav, root, (target,), mode="in_memory")
    clock = iter((
        0, 10,
        20, 30,
        40, 50,
        60, 70,
        100, 40_100, 40_150,
    ))
    heap = iter((16_000,) * 7)
    failures = []

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("collect_failure", 1, (("plot", VISIT_TARGET, 0),)),
        lambda event, current: (
            failures.append(
                (event, current.phase, current.step_us))
            if event == acceptance.RUN_ERROR else None),
    )

    assert failures == [(
        acceptance.RUN_ERROR,
        acceptance.PHASE_COLLECT,
        acceptance.MAX_BLOCKING_STEP_US,
    )]
    assert report.errors == 1
    assert report.runtime_steps == 5
    assert report.scenarios_completed == 0
    assert report.failure_mask & acceptance.FAIL_ERROR
    assert report.failure_mask & acceptance.FAIL_BLOCKING
    assert nav.current is root
    assert nav.presents == [target, root, root]


def test_visit_target_times_each_runtime_step_instead_of_the_whole_round_trip(
        monkeypatch):
    root = "root"
    target = "alpha"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (target,), mode="in_memory")
    clock = iter((0, 10, 10, 30, 30, 60, 60, 100))
    heap = iter((16_000, 15_900, 15_800, 15_700, 15_600, 15_500))
    phases = []

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("navigation", 1, (("alpha", VISIT_TARGET, 0),)),
        lambda event, report: (
            phases.append(report.phase)
            if event == acceptance.RUN_STEP else None),
    )

    assert phases == [
        acceptance.PHASE_ENTER,
        acceptance.PHASE_SETTLE,
        acceptance.PHASE_BACK,
        acceptance.PHASE_SETTLE,
    ]
    assert report.runtime_steps == 4
    assert report.blocking_max_us == 40
    assert report.heap_min == 15_500
    assert nav.presents == [target, root]
    assert report.accepted


def test_settle_gc_memory_error_marks_that_physical_step(monkeypatch):
    root = "root"
    target = "plot"

    class CollectingNav(_InMemoryNav):
        def __init__(self):
            super().__init__(root)
            self.settle_calls = 0

        def settle_current(self):
            self.settle_calls += 1
            if self.settle_calls == 1:
                return acceptance.SETTLE_COLLECT
            return 0

    nav = CollectingNav()
    runtime = RuntimeHandle(nav, root, (target,), mode="in_memory")
    clock = iter((0, 1, 1, 2, 2, 3, 3, 4))
    heap = iter((16_000, 16_000, 16_000, 16_000, 16_000, 16_000))
    collect_calls = 0
    observed = []

    def collect():
        nonlocal collect_calls
        collect_calls += 1
        if collect_calls == 2:
            raise MemoryError

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", collect)

    report = run(
        runtime,
        ("plot", 1, (("plot", VISIT_TARGET, 0),)),
        lambda event, report: observed.append((event, report.phase)),
    )

    assert (acceptance.RUN_MEMORY_ERROR, acceptance.PHASE_SETTLE) in observed
    assert report.runtime_steps == 4
    assert report.memory_errors == 1
    assert not report.accepted


def test_visit_allows_declared_plot_buffer_only_until_back(monkeypatch):
    root = "root"
    target = "plot"
    main_buffer = bytearray(8192)
    curve_buffer = bytearray(4)

    class PlotNav(_InMemoryNav):
        def __init__(self):
            super().__init__(root)
            self.renderer = _Renderer(root)
            self.renderer.display = type(
                "Display", (), {"gs4_buf": main_buffer})()

        def go_to(self, selected):
            super().go_to(selected)
            self.memory._plot_curve = curve_buffer

        def go_back(self):
            self.memory._plot_curve = None
            self.current = root

    nav = PlotNav()
    runtime = RuntimeHandle(
        nav,
        root,
        (target,),
        mode="in_memory",
        optional_buffer_size=len(curve_buffer),
        optional_buffer_target=target,
    )
    clock = iter((0, 1, 1, 2, 2, 3, 3, 4))
    heap = iter((16_000, 16_000, 16_000, 16_000, 16_000, 16_000))

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("plot", 1, (("plot", VISIT_TARGET, 0),)),
    )

    assert report.buffer_change_count == 2
    assert report.buffer_peak_bytes == len(main_buffer) + len(curve_buffer)
    assert report.buffers_before == report.buffers_after == (
        ("main", len(main_buffer), id(main_buffer)),
    )
    assert not report.failure_mask & acceptance.FAIL_BUFFERS
    assert report.accepted


def test_plot_visit_rejects_same_size_buffer_identity_replacement(monkeypatch):
    root = "root"
    target = "plot"
    first_curve = bytearray(4)
    replacement_curve = bytearray(4)

    class ReplacingPlotNav(_InMemoryNav):
        def __init__(self):
            super().__init__(root)
            self.settle_calls = 0

        def go_to(self, selected):
            super().go_to(selected)
            self.memory._plot_curve = first_curve

        def settle_current(self):
            self.settle_calls += 1
            if self.current is target and self.settle_calls == 1:
                self.memory._plot_curve = replacement_curve
            return 0

        def go_back(self):
            self.memory._plot_curve = None
            self.current = root

    nav = ReplacingPlotNav()
    runtime = RuntimeHandle(
        nav,
        root,
        (target,),
        mode="in_memory",
        optional_buffer_size=len(first_curve),
        optional_buffer_target=target,
    )
    clock = iter((0, 1, 1, 2, 2, 3, 3, 4))
    heap = iter((16_000,) * 6)

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("plot", 1, (("plot", VISIT_TARGET, 0),)),
    )

    assert report.buffers_before == report.buffers_after == ()
    assert report.failure_mask & acceptance.FAIL_BUFFERS
    assert not report.accepted


def test_later_plot_visit_may_use_a_new_stable_buffer_identity(monkeypatch):
    root = "root"
    target = "plot"
    created = []

    class ReallocatingPlotNav(_InMemoryNav):
        def go_to(self, selected):
            super().go_to(selected)
            curve = bytearray(4)
            created.append(curve)
            self.memory._plot_curve = curve

        def go_back(self):
            self.memory._plot_curve = None
            self.current = root

    nav = ReallocatingPlotNav(root)
    runtime = RuntimeHandle(
        nav,
        root,
        (target,),
        mode="in_memory",
        optional_buffer_size=4,
        optional_buffer_target=target,
    )
    clock = iter(range(16))
    heap = iter((16_000,) * 10)

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("plot", 2, (("plot", VISIT_TARGET, 0),)),
    )

    assert len(created) == 2
    assert id(created[0]) != id(created[1])
    assert report.buffers_before == report.buffers_after == ()
    assert not report.failure_mask & acceptance.FAIL_BUFFERS
    assert report.accepted


def test_visit_rejects_declared_plot_buffer_if_it_survives_back(monkeypatch):
    root = "root"
    target = "plot"
    curve_buffer = bytearray(4)

    class LeakingPlotNav(_InMemoryNav):
        def go_to(self, selected):
            super().go_to(selected)
            self.memory._plot_curve = curve_buffer

        def go_back(self):
            self.current = root

    nav = LeakingPlotNav(root)
    runtime = RuntimeHandle(
        nav,
        root,
        (target,),
        mode="in_memory",
        optional_buffer_size=len(curve_buffer),
        optional_buffer_target=target,
    )
    clock = iter((0, 1, 1, 2, 2, 3, 3, 4))
    heap = iter((16_000, 16_000, 16_000, 16_000, 16_000, 16_000))

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("plot", 1, (("plot", VISIT_TARGET, 0),)),
    )

    assert report.failure_mask & acceptance.FAIL_BUFFERS
    assert report.buffers_after != report.buffers_before
    assert not report.accepted


def test_plot_buffer_allowlist_does_not_apply_to_another_target(monkeypatch):
    root = "root"
    calculator = object()
    plot = object()
    curve_buffer = bytearray(4)

    class WrongTargetNav(_InMemoryNav):
        def go_to(self, selected):
            super().go_to(selected)
            self.memory._plot_curve = curve_buffer

        def go_back(self):
            self.memory._plot_curve = None
            self.current = root

    nav = WrongTargetNav(root)
    runtime = RuntimeHandle(
        nav,
        root,
        (calculator, plot),
        mode="in_memory",
        optional_buffer_size=len(curve_buffer),
        optional_buffer_target=plot,
    )
    clock = iter((0, 1, 1, 2, 2, 3, 3, 4))
    heap = iter((16_000, 16_000, 16_000, 16_000, 16_000, 16_000))

    monkeypatch.setattr(
        acceptance.time, "ticks_us", lambda: next(clock))
    monkeypatch.setattr(
        acceptance.time, "ticks_diff", lambda end, start: end - start)
    monkeypatch.setattr(
        acceptance.gc, "mem_free", lambda: next(heap), raising=False)
    monkeypatch.setattr(acceptance.gc, "collect", lambda: None)

    report = run(
        runtime,
        ("calculator", 1, (("calculator", VISIT_TARGET, 0),)),
    )

    assert report.failure_mask & acceptance.FAIL_BUFFERS
    assert not report.accepted


def test_observer_failure_becomes_a_failed_verdict_and_runtime_returns_root():
    root = "root"
    target = "calculator"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (target,), mode="in_memory")

    def leave_root(handle, round_index):
        handle.nav.go_to(target)

    def broken_observer(event, report):
        if event == acceptance.RUN_START:
            raise RuntimeError("serial observer failed")

    report = run(
        runtime,
        ("observer", 1, (("input", RUN_ACTION, leave_root),)),
        broken_observer,
    )

    assert report.errors == 1
    assert report.failure_mask & acceptance.FAIL_ERROR
    assert not report.accepted
    assert nav.current is root


def test_measurement_allocation_failure_is_counted_without_double_step():
    root = "root"
    nav = _InMemoryNav(root)

    class SnapshotOOMRuntime(RuntimeHandle):
        __slots__ = ("snapshot_calls",)

        def __init__(self):
            super().__init__(nav, root, (), mode="in_memory")
            self.snapshot_calls = 0

        def buffer_snapshot(self):
            self.snapshot_calls += 1
            if self.snapshot_calls == 2:
                raise MemoryError
            return ()

    runtime = SnapshotOOMRuntime()

    report = run(
        runtime,
        ("instrumentation_oom", 1, (("step", RUN_ACTION,
                                     lambda handle, round_index: None),)),
    )

    assert report.runtime_steps == 1
    assert report.memory_errors == 1
    assert report.failure_mask & acceptance.FAIL_MEMORY
    assert not report.accepted
    assert nav.current is root


def test_initial_buffer_probe_memory_error_cannot_escape_the_runner():
    root = "root"
    nav = _InMemoryNav(root)

    class InitialSnapshotOOMRuntime(RuntimeHandle):
        __slots__ = ("first",)

        def __init__(self):
            super().__init__(nav, root, (), mode="in_memory")
            self.first = True

        def buffer_snapshot(self):
            if self.first:
                self.first = False
                raise MemoryError
            return ()

    report = run(
        InitialSnapshotOOMRuntime(),
        ("initial_probe_oom", 1, (("step", RUN_ACTION,
                                  lambda handle, round_index: None),)),
    )

    assert report.memory_errors == 1
    assert report.failure_mask & acceptance.FAIL_MEMORY
    assert not report.accepted
    assert nav.current is root


def test_bounded_session_opens_once_and_times_each_physical_action(
        monkeypatch):
    root = "root"

    class CollectingNav(_InMemoryNav):
        def __init__(self, selected_root):
            _InMemoryNav.__init__(self, selected_root)
            self.collects = 0

        def collect_pending(self):
            self.collects += 1
            return True

    nav = CollectingNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")

    class Session:
        __slots__ = (
            "opens", "closes", "rounds", "half_round",
            "completed_capability", "completed_count")

        capabilities = ("bounded_action",)
        max_steps = 2

        def __init__(self):
            self.opens = 0
            self.closes = 0
            self.rounds = []
            self.half_round = False
            self.completed_capability = None
            self.completed_count = 0

        def open(self, handle):
            assert handle is runtime
            self.opens += 1

        def step(self, round_index, capability_index):
            assert capability_index == 0
            self.rounds.append((round_index, capability_index))
            if not self.half_round:
                self.half_round = True
                return STEP_MORE
            self.half_round = False
            self.completed_capability = self.capabilities[capability_index]
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=8)
    phases = []

    report = run(
        runtime,
        ("bounded", 2, (("bounded_action", RUN_BOUNDED, session),)),
        lambda event, current: (
            phases.append(current.phase)
            if event == acceptance.RUN_STEP else None),
    )

    assert session.opens == 1
    assert session.closes == 1
    assert session.rounds == [(0, 0), (0, 0), (1, 0), (1, 0)]
    assert nav.collects == 2
    assert phases.count(acceptance.PHASE_COLLECT) == 2
    assert report.runtime_steps == 8
    assert report.rounds_completed == 2
    assert report.scenarios_completed == 2
    assert report.accepted


def test_bounded_matrix_reports_all_five_rounds_without_parallel_sessions(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="resident")

    class MatrixSession:
        __slots__ = (
            "opens", "closes", "rounds", "completed_capability",
            "completed_count")

        capabilities = (
            "calculator_history",
            "error_lifecycle",
            "variable_quota_restart",
            "plot_pipeline",
            "plugin_reload",
            "stopwatch_laps",
            "page_round_trips",
        )
        step_limits = (1, 1, 1, 1, 1, 1, 1)

        def __init__(self):
            self.opens = 0
            self.closes = 0
            self.rounds = []
            self.completed_capability = None
            self.completed_count = 0

        def open(self, handle):
            assert handle is runtime
            self.opens += 1

        def step(self, round_index, capability_index):
            self.rounds.append((round_index, capability_index))
            self.completed_capability = self.capabilities[capability_index]
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            return True

    session = MatrixSession()
    _bounded_measurement(monkeypatch, physical_steps=37)

    report = run(
        runtime,
        ("matrix", 5, (
            ("calculator_history", RUN_BOUNDED, session),
            ("error_lifecycle", RUN_BOUNDED, session),
            ("variable_quota_restart", RUN_BOUNDED, session),
            ("plot_pipeline", RUN_BOUNDED, session),
            ("plugin_reload", RUN_BOUNDED, session),
            ("stopwatch_laps", RUN_BOUNDED, session),
            ("page_round_trips", RUN_BOUNDED, session),
        )),
    )

    assert session.opens == 1
    assert session.closes == 1
    assert session.rounds == [
        (round_index, capability_index)
        for round_index in range(5)
        for capability_index in range(7)
    ]
    assert report.runtime_steps == 37
    assert report.rounds_completed == 5
    assert report.scenarios_completed == 35
    assert report.accepted


def test_bounded_open_failure_still_closes_once(monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    primary = MemoryError("open OOM")

    class Session:
        __slots__ = ("closes",)

        capabilities = ("open",)

        def __init__(self):
            self.closes = 0

        def open(self, _handle):
            raise primary

        def step(self, _round_index, _capability_index):
            raise AssertionError("open failure must not step")

        def close(self):
            self.closes += 1
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=2)

    report = run(
        runtime,
        ("open_oom", 1, (("open", RUN_BOUNDED, session),)),
    )

    assert session.closes == 1
    assert report.primary_error is primary
    assert report.memory_errors == 1
    assert report.runtime_steps == 2
    assert report.rounds_completed == 0
    assert not report.accepted
    assert nav.presents == [root]


def test_bounded_step_memory_error_remains_primary_over_close_memory_error(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    primary = MemoryError("step OOM")
    secondary = MemoryError("restore OOM")

    class Session:
        __slots__ = ("closes",)

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, _capability_index):
            raise primary

        def close(self):
            self.closes += 1
            raise secondary

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    report = run(
        runtime,
        ("step_oom", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 2
    assert report.primary_error is primary
    assert report.secondary_error is secondary
    assert report.memory_errors == 1
    assert report.errors == 0
    assert report.runtime_steps == 4
    assert report.rounds_completed == 0
    assert not report.accepted
    assert nav.presents == []


def test_bounded_observer_failure_closes_the_open_transaction(monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")

    class Session:
        __slots__ = (
            "closes", "steps", "completed_capability", "completed_count")

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0
            self.steps = 0
            self.completed_capability = None
            self.completed_count = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, capability_index):
            self.steps += 1
            self.completed_capability = self.capabilities[capability_index]
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=2)
    observer_failed = False

    def observer(event, _report):
        nonlocal observer_failed
        if event == acceptance.RUN_STEP and not observer_failed:
            observer_failed = True
            raise RuntimeError("observer failed")

    report = run(
        runtime,
        ("observer", 1, (("step", RUN_BOUNDED, session),)),
        observer,
    )

    assert session.steps == 0
    assert session.closes == 1
    assert report.runtime_steps == 2
    assert report.errors == 1
    assert report.rounds_completed == 0
    assert not report.accepted


def test_bounded_close_memory_error_is_not_hidden_by_step_error(monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    step_error = RuntimeError("step failed")
    cleanup = MemoryError("restore OOM")

    class Session:
        __slots__ = ("closes",)

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, _capability_index):
            raise step_error

        def close(self):
            self.closes += 1
            raise cleanup

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    report = run(
        runtime,
        ("close_oom", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 2
    assert report.primary_error is cleanup
    assert report.secondary_error is step_error
    assert report.memory_errors == 1
    assert report.errors == 1
    assert report.runtime_steps == 4
    assert not report.accepted


def test_bounded_cleanup_only_memory_error_is_a_timed_failure(monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    cleanup = MemoryError("restore OOM")

    class Session:
        __slots__ = ("closes", "completed_capability", "completed_count")

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, capability_index):
            self.completed_capability = self.capabilities[capability_index]
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            raise cleanup

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    report = run(
        runtime,
        ("cleanup_oom", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 2
    assert report.primary_error is cleanup
    assert report.memory_errors == 1
    assert report.errors == 0
    assert report.runtime_steps == 4
    assert report.rounds_completed == 1
    assert report.scenarios_completed == 1
    assert not report.accepted


def test_bounded_cleanup_only_false_return_is_a_timed_failure(monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")

    class Session:
        __slots__ = ("closes", "completed_capability", "completed_count")

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, capability_index):
            self.completed_capability = self.capabilities[capability_index]
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            return False

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    report = run(
        runtime,
        ("cleanup_false", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 2
    assert isinstance(report.primary_error, RuntimeError)
    assert str(report.primary_error) == "Bounded session close failed"
    assert report.secondary_error is None
    assert report.memory_errors == 0
    assert report.errors == 1
    assert report.runtime_steps == 4
    assert report.rounds_completed == 1
    assert report.scenarios_completed == 1
    assert report.failure_mask & acceptance.FAIL_ERROR
    assert not report.accepted


def test_bounded_step_oom_remains_primary_over_false_close_return(monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    primary = MemoryError("step OOM")

    class Session:
        __slots__ = ("closes",)

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, _capability_index):
            raise primary

        def close(self):
            self.closes += 1
            return False

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    report = run(
        runtime,
        ("step_oom_false_close", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 2
    assert report.primary_error is primary
    assert isinstance(report.secondary_error, RuntimeError)
    assert str(report.secondary_error) == "Bounded session close failed"
    assert report.memory_errors == 1
    assert report.errors == 0
    assert report.runtime_steps == 4
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    assert not report.accepted


def test_bounded_close_retries_once_keeps_failure_and_releases_after_success(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = _ResetTrackingRuntime(nav, root)
    first_error = RuntimeError("first close failure")
    collections = []

    class Session:
        __slots__ = ("closes", "completed_capability", "completed_count")

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, capability_index):
            self.completed_capability = self.capabilities[capability_index]
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            if self.closes == 1:
                raise first_error
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)
    monkeypatch.setattr(
        acceptance.gc, "collect", lambda: collections.append(True))

    report = run(
        runtime,
        ("retry_close", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is True
    assert report.runtime_steps == 4
    assert report.errors == 1
    assert report.primary_error_code == acceptance.ERROR_EXCEPTION
    assert report.primary_error is None
    assert report.secondary_error is None
    assert report.failure_mask & acceptance.FAIL_ERROR
    assert not report.accepted
    assert runtime.reset_calls == [False, True, False]
    assert collections == [True, True]


def test_bounded_close_double_failure_keeps_session_and_skips_root_reset(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = _ResetTrackingRuntime(nav, root)

    class Session:
        __slots__ = (
            "closes", "retained", "completed_capability", "completed_count")

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0
            self.retained = True
            self.completed_capability = None
            self.completed_count = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, capability_index):
            self.completed_capability = self.capabilities[capability_index]
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            return False

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    report = run(
        runtime,
        ("double_close_failure", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 2
    assert session.retained is True
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is False
    assert report.runtime_steps == 4
    assert report.errors == 1
    assert report.failure_mask & acceptance.FAIL_ERROR
    assert not report.accepted
    assert runtime.reset_calls == [False]


def test_unopened_bounded_session_keeps_the_normal_final_root_reset():
    root = "root"
    nav = _InMemoryNav(root)
    runtime = _ResetTrackingRuntime(nav, root)

    class Session:
        capabilities = ("step",)

        def open(self, _handle):
            raise AssertionError("zero rounds must not open")

        def step(self, _round_index, _capability_index):
            raise AssertionError("zero rounds must not step")

        def close(self):
            raise AssertionError("zero rounds must not close")

    report = run(
        runtime,
        ("zero_round_bounded", 0, (("step", RUN_BOUNDED, Session()),)),
    )

    assert report.bounded_close_attempts == 0
    assert report.bounded_session_restored is False
    assert runtime.reset_calls == [False, False]


def test_bounded_retry_oom_promotes_memory_without_retaining_first_close_error(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = _ResetTrackingRuntime(nav, root)
    first_error = RuntimeError("ordinary close failure")
    retry_oom = MemoryError("retry close OOM")

    class Session:
        __slots__ = ("closes", "completed_capability", "completed_count")

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, capability_index):
            self.completed_capability = self.capabilities[capability_index]
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            if self.closes == 1:
                raise first_error
            raise retry_oom

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    report = run(
        runtime,
        ("retry_close_oom", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is False
    assert report.primary_error is retry_oom
    assert report.primary_error_code == acceptance.ERROR_MEMORY
    assert report.secondary_error_code == acceptance.ERROR_NONE
    assert report.secondary_error is None
    assert report.memory_errors == 1
    assert report.errors == 1
    assert not report.accepted
    assert runtime.reset_calls == [False]


def test_bounded_close_observer_oom_supersedes_a_retried_close_failure(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = _ResetTrackingRuntime(nav, root)
    first_error = RuntimeError("first close failure")
    observer_oom = MemoryError("close observer OOM")

    class Session:
        __slots__ = ("closes", "completed_capability", "completed_count")

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, capability_index):
            self.completed_capability = self.capabilities[capability_index]
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            if self.closes == 1:
                raise first_error
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    def observer(event, _report):
        if event == acceptance.RUN_ERROR:
            raise observer_oom

    report = run(
        runtime,
        ("close_observer_oom", 1, (("step", RUN_BOUNDED, session),)),
        observer,
    )

    assert session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is True
    assert report.primary_error is observer_oom
    assert report.primary_error_code == acceptance.ERROR_MEMORY
    assert report.secondary_error_code == acceptance.ERROR_NONE
    assert report.memory_errors == 1
    assert report.errors == 1
    assert not report.accepted
    assert runtime.reset_calls == [False, True, False]


def test_bounded_step_oom_remains_primary_across_a_retry_close_oom(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = _ResetTrackingRuntime(nav, root)
    primary = MemoryError("step OOM")
    retry_only = MemoryError("first close OOM")

    class Session:
        __slots__ = ("closes",)

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, _capability_index):
            raise primary

        def close(self):
            self.closes += 1
            if self.closes == 1:
                raise retry_only
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    report = run(
        runtime,
        ("step_oom_retry_close", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 2
    assert report.bounded_close_attempts == 2
    assert report.bounded_session_restored is True
    assert report.primary_error is primary
    assert report.primary_error_code == acceptance.ERROR_MEMORY
    assert report.secondary_error_code == acceptance.ERROR_MEMORY
    assert report.secondary_error is None
    assert report.memory_errors == 1
    assert report.errors == 0
    assert not report.accepted
    assert runtime.reset_calls == [False, True, False]


def test_bounded_session_stops_after_its_fixed_no_progress_budget(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")

    class Session:
        __slots__ = ("closes", "steps")

        capabilities = ("step",)
        max_steps = 5
        no_progress_limit = 2

        def __init__(self):
            self.closes = 0
            self.steps = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, _capability_index):
            self.steps += 1
            return STEP_WAIT

        def close(self):
            self.closes += 1
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=4)

    report = run(
        runtime,
        ("stalled", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.steps == 2
    assert session.closes == 1
    assert report.runtime_steps == 4
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    assert report.errors == 1
    assert report.failure_mask & acceptance.FAIL_INCOMPLETE
    assert not report.accepted


def test_bounded_scenario_rejects_multiple_transaction_sessions():
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")

    class Session:
        capabilities = ("first", "second")

        def open(self, _handle):
            raise AssertionError("multiple sessions must not open")

        def step(self, _round_index, _capability_index):
            return STEP_DONE

        def close(self):
            raise AssertionError("multiple sessions must not close")

    first = Session()
    second = Session()

    with pytest.raises(ValueError, match="one transaction session"):
        run(
            runtime,
            ("parallel", 1, (
                ("first", RUN_BOUNDED, first),
                ("second", RUN_BOUNDED, second),
            )),
        )


def test_bounded_session_requires_exact_capability_completion_proof(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")

    class Session:
        __slots__ = ("closes", "completed_capability", "completed_count")

        capabilities = ("calculator_history",)

        def __init__(self):
            self.closes = 0
            self.completed_capability = None
            self.completed_count = 0

        def open(self, _handle):
            return None

        def step(self, _round_index, _capability_index):
            self.completed_capability = "plot_pipeline"
            self.completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=3)

    report = run(
        runtime,
        ("wrong_proof", 1, (
            ("calculator_history", RUN_BOUNDED, session),
        )),
    )

    assert session.closes == 1
    assert report.runtime_steps == 3
    assert report.scenarios_completed == 0
    assert report.rounds_completed == 0
    assert report.errors == 1
    assert report.failure_mask & acceptance.FAIL_INCOMPLETE
    assert not report.accepted


def test_bounded_limit_property_memory_error_becomes_failure_and_closes_once(
        monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    primary = MemoryError("step limit OOM")

    class Session:
        __slots__ = ("closes",)

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0

        @property
        def step_limits(self):
            raise primary

        def open(self, _handle):
            return None

        def step(self, _round_index, _capability_index):
            raise AssertionError("limit failure must not step")

        def close(self):
            self.closes += 1
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=2)

    report = run(
        runtime,
        ("limit_getter_oom", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 1
    assert report.primary_error is primary
    assert report.memory_errors == 1
    assert report.errors == 0
    assert report.runtime_steps == 2
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    assert report.failure_mask & acceptance.FAIL_MEMORY
    assert report.failure_mask & acceptance.FAIL_INCOMPLETE
    assert not report.accepted


@pytest.mark.parametrize(
    ("proof_name", "error_type", "memory_errors", "errors", "failure_mask"),
    (
        ("completed_capability", MemoryError, 1, 0, acceptance.FAIL_MEMORY),
        ("completed_capability", RuntimeError, 0, 1, acceptance.FAIL_ERROR),
        ("completed_count", MemoryError, 1, 0, acceptance.FAIL_MEMORY),
        ("completed_count", RuntimeError, 0, 1, acceptance.FAIL_ERROR),
    ),
)
def test_bounded_completion_proof_getter_failure_is_reported_and_closes_once(
        monkeypatch, proof_name, error_type, memory_errors, errors,
        failure_mask):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    primary = error_type(proof_name + " failure")

    class Session:
        __slots__ = (
            "closes", "_completed_capability", "_completed_count")

        capabilities = ("step",)

        def __init__(self):
            self.closes = 0
            self._completed_capability = None
            self._completed_count = 0

        @property
        def completed_capability(self):
            if proof_name == "completed_capability":
                raise primary
            return self._completed_capability

        @property
        def completed_count(self):
            if proof_name == "completed_count":
                raise primary
            return self._completed_count

        def open(self, _handle):
            return None

        def step(self, _round_index, capability_index):
            self._completed_capability = self.capabilities[capability_index]
            self._completed_count += 1
            return STEP_DONE

        def close(self):
            self.closes += 1
            return True

    session = Session()
    _bounded_measurement(monkeypatch, physical_steps=3)

    report = run(
        runtime,
        ("proof_getter_failure", 1, (("step", RUN_BOUNDED, session),)),
    )

    assert session.closes == 1
    assert report.primary_error is primary
    assert report.memory_errors == memory_errors
    assert report.errors == errors
    assert report.runtime_steps == 3
    assert report.rounds_completed == 0
    assert report.scenarios_completed == 0
    assert report.failure_mask & failure_mask
    assert report.failure_mask & acceptance.FAIL_INCOMPLETE
    assert not report.accepted
