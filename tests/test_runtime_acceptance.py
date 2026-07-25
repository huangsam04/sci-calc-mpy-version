import pytest

import runtime_acceptance as acceptance

from runtime_acceptance import (
    RUN_ACTION,
    VISIT_TARGET,
    RuntimeHandle,
    get_resident_runtime,
    run,
    set_resident_runtime,
)


class _Renderer:
    def __init__(self, visible):
        self._visible_screen = visible


class _Memory:
    def __init__(self, buffers):
        self._buffers = buffers


class _ResidentNav:
    def __init__(self, root, buffers):
        self.current = root
        self.renderer = _Renderer(root)
        self.memory = _Memory(buffers)
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
    curve_buffer = bytearray(1404)
    nav = _ResidentNav(root, {"plot_curve": curve_buffer})
    nav.renderer.display = type("Display", (), {"gs4_buf": main_buffer})()
    runtime = RuntimeHandle(nav, root, ())

    assert runtime.buffer_snapshot() == (
        ("main", len(main_buffer), id(main_buffer)),
        ("plot_curve", len(curve_buffer), id(curve_buffer)),
    )


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


def test_runner_rejects_a_step_at_the_strict_32_ms_boundary(monkeypatch):
    root = "root"
    nav = _InMemoryNav(root)
    runtime = RuntimeHandle(nav, root, (), mode="in_memory")
    clock = iter((100, 32_100))
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
    clock = iter((100, 32_100, 32_150))
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
    clock = iter((100, 32_100))
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
    clock = iter((0, 10, 100, 32_100, 32_150))
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
    clock = iter((0, 10, 20, 30, 100, 32_100, 32_150))
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
        100, 32_100, 32_150,
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
            self.memory._buffers["plot_curve"] = curve_buffer

        def go_back(self):
            del self.memory._buffers["plot_curve"]
            self.current = root

    nav = PlotNav()
    runtime = RuntimeHandle(
        nav,
        root,
        (target,),
        mode="in_memory",
        optional_buffers=(("plot_curve", len(curve_buffer)),),
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
            self.memory._buffers["plot_curve"] = first_curve

        def settle_current(self):
            self.settle_calls += 1
            if self.current is target and self.settle_calls == 1:
                self.memory._buffers["plot_curve"] = replacement_curve
            return 0

        def go_back(self):
            del self.memory._buffers["plot_curve"]
            self.current = root

    nav = ReplacingPlotNav()
    runtime = RuntimeHandle(
        nav,
        root,
        (target,),
        mode="in_memory",
        optional_buffers=(("plot_curve", len(first_curve)),),
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
            self.memory._buffers["plot_curve"] = curve

        def go_back(self):
            del self.memory._buffers["plot_curve"]
            self.current = root

    nav = ReallocatingPlotNav(root)
    runtime = RuntimeHandle(
        nav,
        root,
        (target,),
        mode="in_memory",
        optional_buffers=(("plot_curve", 4),),
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
            self.memory._buffers["plot_curve"] = curve_buffer

        def go_back(self):
            self.current = root

    nav = LeakingPlotNav(root)
    runtime = RuntimeHandle(
        nav,
        root,
        (target,),
        mode="in_memory",
        optional_buffers=(("plot_curve", len(curve_buffer)),),
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
            self.memory._buffers["plot_curve"] = curve_buffer

        def go_back(self):
            del self.memory._buffers["plot_curve"]
            self.current = root

    nav = WrongTargetNav(root)
    runtime = RuntimeHandle(
        nav,
        root,
        (calculator, plot),
        mode="in_memory",
        optional_buffers=(("plot_curve", len(curve_buffer)),),
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
