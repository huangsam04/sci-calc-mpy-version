from benchmarks import run
from performance import PerformanceMetrics


class FakeNav:
    def __init__(self, root):
        self.current = root
        self._transitioning = False
        self.frames = 0

    def reset(self, root):
        self.current = root
        self._transitioning = False

    def go_to(self, target):
        self.current = target
        self._transitioning = True

    def go_back(self):
        self.current = "root"
        self._transitioning = True

    def is_transitioning(self):
        return self._transitioning

    def draw_transition(self, now):
        self.frames += 1
        self._transitioning = False


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


def test_device_benchmark_runner_exercises_repeated_navigation_without_writes():
    metrics = PerformanceMetrics(sample_limit=16)
    root = "root"
    nav = FakeNav(root)
    metrics.start_boot(0)
    metrics.mark_boot("ready", 1)
    metrics.bind_runtime(nav, root, ("plot", "calculator"))
    lines = []

    report = run(metrics=metrics, cycles=3, frame_pace_ms=0,
                 gc_runs=1, emit=lines.append)

    assert report["navigation_cycles"] == 3
    assert report["warmup_transitions"] == 4
    assert report["input_to_present_us"]["count"] == 3
    assert report["frame_us"]["count"] == 6
    assert nav.frames == 10
    assert any(line.startswith("BENCH nav_event_p95_us=") for line in lines)
    assert any(line.startswith("BENCH frame_p95_us=") for line in lines)


def test_benchmark_runner_builds_a_standalone_runtime_when_app_state_is_absent():
    metrics = PerformanceMetrics(sample_limit=8)
    root = "root"
    nav = FakeNav(root)
    builds = []

    def build_runtime(active_metrics):
        builds.append(active_metrics)
        active_metrics.bind_runtime(nav, root, ("plot",))

    report = run(metrics=metrics, cycles=1, frame_pace_ms=0,
                 gc_runs=1, emit=None, build_runtime=build_runtime)

    assert builds == [metrics]
    assert report["navigation_cycles"] == 1
