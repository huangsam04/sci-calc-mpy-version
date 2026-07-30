import sys
import types

import pytest

import benchmarks
from benchmarks import run
from performance import PerformanceMetrics
from runtime_acceptance import (
    RuntimeHandle,
    get_resident_runtime,
    set_resident_runtime,
)
from runtime_handle import ApplicationBinding


class FakeNav:
    def __init__(self, root):
        self.current = root
        self.presents = 0
        self.settles = 0
        self.visited = []
        self.memory = type("Memory", (), {"_buffers": {}})()

    def reset(self, root):
        self.current = root

    def go_to(self, target):
        self.current = target
        self.visited.append(target)

    def go_back(self):
        self.current = "root"

    def present_current(self):
        self.presents += 1

    def settle_current(self):
        self.settles += 1
        return 0


def test_navigation_scenario_selects_only_the_five_canonical_pages():
    root = object()
    pages = tuple(type(
        "Page" + str(index), (), {"transition_title": "page" + str(index)})()
                  for index in range(1, 10))
    screens = (root,) + pages
    binding = ApplicationBinding(
        screens, object(), object(), object())
    runtime = RuntimeHandle(
        FakeNav(root), root, screens, mode="resident",
        application_binding=binding)

    scenario = benchmarks.navigation_scenario(runtime, 1)

    assert tuple(step[2] for step in scenario[2]) == (1, 2, 3, 4, 5)


def test_performance_metrics_reports_phase_latency_frame_and_gc_summaries():
    metrics = PerformanceMetrics(sample_limit=8)
    metrics.start_boot(100)
    metrics.mark_boot("display", 120)
    metrics.mark_boot("ready", 180)
    metrics.record_input(1_000)
    metrics.record_frame(40, 1_240)
    for value in (10, 20, 30, 50, 60):
        metrics.record_frame(value)
    metrics.record_gc(75)

    snapshot = metrics.snapshot()

    assert snapshot["boot_phases_ms"] == [("display", 20), ("ready", 60)]
    assert snapshot["input_to_present_us"] == {
        "count": 1, "p95_us": 240, "max_us": 240}
    assert snapshot["frame_us"]["count"] == 6
    assert snapshot["frame_us"]["max_us"] == 60
    assert snapshot["gc_us"] == {"count": 1, "p95_us": 75, "max_us": 75}


def test_default_diagnostic_window_fits_the_device_memory_budget():
    metrics = PerformanceMetrics()

    assert metrics.sample_limit == 16
    assert metrics._frame_bucket_us == 1_000
    assert isinstance(metrics._frame_histogram, bytearray)
    assert len(metrics._frame_histogram) == 32
    assert (len(metrics._frame_histogram) * metrics._frame_bucket_us
            >= 25_000)


def test_boot_phase_storage_can_be_released_before_resident_construction():
    metrics = PerformanceMetrics()
    metrics.start_boot(100)
    metrics.mark_boot("display", 120)

    metrics.release_boot_samples()
    metrics.mark_boot("ignored", 140)

    assert metrics._boot_phases is None
    assert metrics.snapshot()["boot_phases_ms"] == []

    metrics.start_boot(200)
    metrics.mark_boot("ready", 230)
    assert metrics.snapshot()["boot_phases_ms"] == [("ready", 30)]


def test_frame_histogram_reuses_compact_storage_across_long_runs():
    metrics = PerformanceMetrics()
    histogram = metrics._frame_histogram

    for _ in range(300):
        metrics.record_frame(12_000)
    metrics.reset_run()

    assert metrics._frame_histogram is histogram
    assert len(histogram) == 32


def test_performance_metrics_records_data_without_owning_runtime_identity():
    metrics = PerformanceMetrics()

    assert not hasattr(metrics, "_runtime")
    assert not hasattr(metrics, "bind_runtime")
    assert not hasattr(metrics, "runtime")


def test_frame_summary_keeps_every_frame_beyond_raw_sample_limit():
    metrics = PerformanceMetrics(sample_limit=2, frame_bucket_us=100,
                                 frame_bucket_count=8)

    for _ in range(300):
        metrics.record_frame(75)

    summary = metrics.snapshot()["frame_us"]
    assert summary == {"count": 300, "p95_us": 75, "max_us": 75}


def test_frame_summary_does_not_understate_an_overflow_p95():
    metrics = PerformanceMetrics(frame_bucket_us=100, frame_bucket_count=2)

    for _ in range(94):
        metrics.record_frame(10)
    for _ in range(6):
        metrics.record_frame(250)

    assert metrics.snapshot()["frame_us"] == {
        "count": 100, "p95_us": 250, "max_us": 250}


def test_latency_and_gc_metrics_keep_fixed_storage_across_long_runs():
    metrics = PerformanceMetrics(sample_limit=2)
    input_values = metrics._input_to_present_us.values
    gc_values = metrics._gc_us.values

    for index in range(20):
        started = index * 100
        metrics.record_input(started)
        metrics.record_frame(1, started + 10)
        metrics.record_gc(index)

    before_reset = metrics.snapshot()
    assert metrics._input_to_present_us.values is input_values
    assert metrics._gc_us.values is gc_values
    assert before_reset["input_to_present_us"] == {
        "count": 2, "p95_us": 10, "max_us": 10}
    assert before_reset["gc_us"] == {"count": 2, "p95_us": 19, "max_us": 19}

    metrics.reset_run()

    assert metrics._input_to_present_us.values is input_values
    assert metrics._gc_us.values is gc_values
    assert metrics.snapshot()["input_to_present_us"]["count"] == 0


def test_device_benchmark_runner_exercises_five_complete_navigation_rounds():
    metrics = PerformanceMetrics(sample_limit=16)
    root = "root"
    nav = FakeNav(root)
    targets = ("plot", "calculator")
    runtime = RuntimeHandle(nav, root, targets, mode="in_memory")
    metrics.start_boot(0)
    metrics.mark_boot("ready", 1)
    lines = []

    report = run(
        runtime=runtime, metrics=metrics, cycles=5, emit=lines.append)

    assert report.rounds_completed == 5
    assert report.scenarios_completed == 10
    assert nav.visited == list(targets) * 6  # one warm-up plus five measured
    assert nav.presents == 24
    assert nav.settles == 24
    assert any(
        line.startswith("BENCH input_to_present_p95_us=") for line in lines)
    assert any(line.startswith("BENCH loop_step_p95_us=") for line in lines)


def test_resident_benchmark_keeps_one_warmup_and_five_measured_rounds():
    root = "root"
    nav = FakeNav(root)
    runtime = RuntimeHandle(
        nav, root, ("plot",), mode="resident")

    set_resident_runtime(runtime)
    try:
        report = run(runtime=runtime, cycles=5, emit=None)
        resident_after = get_resident_runtime()
    finally:
        set_resident_runtime(None)

    assert report.mode == "resident"
    assert report.rounds_completed == 5
    assert nav.visited == ["plot"] * 6
    assert resident_after is runtime


def test_benchmark_runner_cannot_hide_a_failed_warmup():
    class FailingWarmupNav(FakeNav):
        def __init__(self, root):
            super().__init__(root)
            self.fail_next_enter = True

        def go_to(self, target):
            if self.fail_next_enter:
                self.fail_next_enter = False
                raise MemoryError
            super().go_to(target)

    root = "root"
    runtime = RuntimeHandle(
        FailingWarmupNav(root), root, ("plot",), mode="in_memory")

    with pytest.raises(RuntimeError, match="warmup"):
        run(runtime=runtime, cycles=1, emit=None)


@pytest.mark.parametrize("cycles", (0, -1))
def test_benchmark_rejects_nonpositive_cycles_before_warmup(cycles):
    root = "root"
    nav = FakeNav(root)
    runtime = RuntimeHandle(
        nav, root, ("plot",), mode="benchmark")

    with pytest.raises(ValueError, match="cycles"):
        run(runtime=runtime, cycles=cycles, emit=None)

    assert nav.visited == []


def test_benchmark_runner_builds_a_standalone_runtime_when_app_state_is_absent():
    metrics = PerformanceMetrics(sample_limit=8)
    root = "root"
    nav = FakeNav(root)
    builds = []
    runtime = RuntimeHandle(nav, root, ("plot",), mode="benchmark")

    def build_runtime():
        builds.append(True)
        return runtime

    resident = RuntimeHandle(
        FakeNav(root), root, ("resident",), mode="resident")
    set_resident_runtime(resident)
    try:
        report = run(metrics=metrics, cycles=1, frame_pace_ms=0,
                     gc_runs=1, emit=None, build_runtime=build_runtime)
    finally:
        set_resident_runtime(None)

    assert builds == [True]
    assert report.rounds_completed == 1


def test_public_benchmark_builder_does_not_publish_a_benchmark_as_resident(
        monkeypatch):
    root = "root"
    resident = RuntimeHandle(
        FakeNav(root), root, ("resident",), mode="resident")
    built = RuntimeHandle(
        FakeNav(root), root, ("plot",), mode="benchmark")
    calls = []
    main_module = types.ModuleType("main")

    def fake_main(**kwargs):
        calls.append(kwargs)
        return built

    main_module.main = fake_main
    monkeypatch.setitem(sys.modules, "main", main_module)
    set_resident_runtime(resident)

    assert benchmarks.build_runtime() is built
    assert calls == [{
        "run_loop": False,
        "runtime_mode": "benchmark",
        "publish_runtime": False,
    }]
    assert get_resident_runtime() is resident
