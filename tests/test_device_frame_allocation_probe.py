import pathlib
import runpy

import pytest


TOOLS = pathlib.Path(__file__).parents[1] / "tools"


class FakeGC:
    def __init__(self, values):
        self.values = list(values)
        self.collect_calls = 0

    def mem_alloc(self):
        return self.values.pop(0)

    def collect(self):
        self.collect_calls += 1


class NoMemAllocGC:
    def collect(self):
        pass


class FakeTime:
    def __init__(self):
        self.sleeps = []

    def sleep_ms(self, milliseconds):
        self.sleeps.append(milliseconds)


class FakeStopwatch:
    transition_title = "Stopwatch"

    def __init__(self):
        laps = [(1, 11)]
        self._clock = [None, False, [True, 17, 29, laps], [0, 0, 2, 3]]
        self._render = (
            [bytearray(b"12:34:56"), bytearray(b"12:34:56:78"),
             bytearray(b"123:45:67:89")],
            [False, 4, 3, 0],
            [0, 1],
        )
        self._footer = (
            ["ENT start", b"ENT start", "TAB reset", b"TAB reset"],
            [160, 1],
        )
        self._runtime = (
            [["Lap1:  00:00:11", None, None, None], 3, 0, laps],
            [1, None, None],
        )

    def _start(self):
        self._clock[1] = True
        self._clock[2][0] = False
        self._clock[2][1] = 99
        return True


class FakeNav:
    def __init__(self, root, stopwatch, results=(), memory_error_at=None):
        self.root = root
        self.stopwatch = stopwatch
        self.current = root
        self.results = list(results)
        self.memory_error_at = memory_error_at
        self.go_to_calls = []
        self.present_count = 0
        self.footer_states_seen = []
        self.lap_cache_states_seen = []

    def go_to(self, target):
        self.go_to_calls.append(target)
        self.current = target

    def open(self, page_id):
        assert page_id == 4
        self.go_to(self.stopwatch)
        return self.stopwatch

    def present_current(self):
        self.present_count += 1
        if self.present_count == self.memory_error_at:
            raise MemoryError("injected present allocation")
        if self.current is self.stopwatch:
            self.stopwatch._render[1][0] = self.stopwatch._clock[1]
            self.stopwatch._render[1][1] = self.present_count
            self.stopwatch._render[0][0][0] = ord("9")
            # The full prewarm executes Stopwatch's running footer path.  The
            # probe must restore these cached values after finally cleanup.
            self.stopwatch._footer[0][0] = "ENT pause/DEL lap"
            self.stopwatch._footer[0][1] = b"ENT pause/DEL lap"
            self.stopwatch._footer[0][2] = "TAB reset"
            self.stopwatch._footer[0][3] = b"TAB reset"
            self.stopwatch._footer[1][0] = 142
            self.stopwatch._footer[1][1] = 2
            labels = self.stopwatch._runtime[0][0]
            labels[0] = "Lap running " + str(self.present_count)
            labels[1] = "Lap cache 1"
            labels[2] = "Lap cache 2"
            labels[3] = "Lap cache 3"
            self.stopwatch._runtime[0][1] = 100 + self.present_count
            self.stopwatch._runtime[0][2] = 1
            self.stopwatch._runtime[0][3] = self.stopwatch._clock[2][3]
            self.stopwatch._runtime[1][0] = 4
            self.footer_states_seen.append((
                self.stopwatch._footer[0][0],
                self.stopwatch._footer[0][1],
                self.stopwatch._footer[0][2],
                self.stopwatch._footer[0][3],
                self.stopwatch._footer[1][0],
                self.stopwatch._footer[1][1],
            ))
            self.lap_cache_states_seen.append((
                tuple(labels),
                self.stopwatch._runtime[0][1],
                self.stopwatch._runtime[0][2],
                self.stopwatch._runtime[0][3],
                self.stopwatch._runtime[1][0],
            ))
        return self.results.pop(0) if self.results else True


class FakeRuntime:
    mode = "resident"

    def __init__(self, stopwatch=None, nav=None, reset_error=None):
        self.root = object()
        self.stopwatch = stopwatch
        self.nav = nav
        self.reset_calls = []
        self.reset_error = reset_error

    def find_target(self, name):
        if name == "Stopwatch":
            return self.stopwatch
        return None

    def reset_root(self, present=True):
        self.reset_calls.append(present)
        self.nav.current = self.root
        if self.reset_error is not None:
            raise self.reset_error


def _load_probe():
    namespace = runpy.run_path(str(TOOLS / "device_frame_allocation_probe.py"))
    return namespace["run"].__globals__


def _runtime(results=(), memory_error_at=None, reset_error=None):
    stopwatch = FakeStopwatch()
    root = object()
    nav = FakeNav(root, stopwatch, results, memory_error_at)
    runtime = FakeRuntime(stopwatch, nav, reset_error)
    runtime.root = root
    return runtime, stopwatch, nav


def _state(stopwatch):
    return (
        stopwatch._clock[1],
        stopwatch._clock[2][0],
        stopwatch._clock[2][1],
        stopwatch._clock[2][2],
        stopwatch._clock[2][3],
        stopwatch._clock[3][0],
        stopwatch._clock[3][1],
        stopwatch._clock[3][2],
        stopwatch._clock[3][3],
        bytes(stopwatch._render[0][0]),
        bytes(stopwatch._render[0][1]),
        bytes(stopwatch._render[0][2]),
        stopwatch._render[1][0],
        stopwatch._render[1][1],
        stopwatch._render[1][2],
        stopwatch._render[1][3],
        stopwatch._render[2][0],
        stopwatch._render[2][1],
        stopwatch._footer[0][0],
        stopwatch._footer[0][1],
        stopwatch._footer[0][2],
        stopwatch._footer[0][3],
        stopwatch._footer[1][0],
        stopwatch._footer[1][1],
        tuple(stopwatch._runtime[0][0]),
        stopwatch._runtime[0][1],
        stopwatch._runtime[0][2],
        stopwatch._runtime[0][3],
        stopwatch._runtime[1][0],
    )


def test_stopwatch_probe_reports_zero_delta_partial_frames_and_restores_state(
        monkeypatch):
    module = _load_probe()
    runtime, stopwatch, nav = _runtime()
    original_state = _state(stopwatch)
    original_laps = stopwatch._clock[2][3]
    fake_gc = FakeGC((512,) * 6)
    fake_time = FakeTime()
    lines = []
    monkeypatch.setitem(module, "gc", fake_gc)
    monkeypatch.setitem(module, "time", fake_time)

    report = module["run"](runtime, frames=3, emit=lines.append)

    assert report == {
        "frames": 3,
        "deltas": (0, 0, 0),
        "total_delta": 0,
        "accepted": True,
    }
    assert [line for line in lines if "_FRAME index=" in line] == [
        "STOPWATCH_FRAME_ALLOC_FRAME index=1 before=512 after=512 "
        "delta=0 presented=1",
        "STOPWATCH_FRAME_ALLOC_FRAME index=2 before=512 after=512 "
        "delta=0 presented=1",
        "STOPWATCH_FRAME_ALLOC_FRAME index=3 before=512 after=512 "
        "delta=0 presented=1",
    ]
    assert lines[-2:] == [
        "STOPWATCH_FRAME_ALLOC_TOTAL frames=3 nonzero=0 "
        "missing_present=0 delta_sum=0",
        "STOPWATCH_FRAME_ALLOC_RESULT PASS",
    ]
    assert fake_time.sleeps == [50, 50, 50, 50]
    assert fake_gc.collect_calls == 1
    assert nav.go_to_calls == [stopwatch]
    assert nav.present_count == 5
    assert runtime.reset_calls == [True]
    assert nav.current is runtime.root
    assert stopwatch._clock[2][3] is original_laps
    assert nav.footer_states_seen[0] == (
        "ENT pause/DEL lap", b"ENT pause/DEL lap", "TAB reset",
        b"TAB reset", 142, 2,
    )
    assert nav.lap_cache_states_seen[0] == (
        ("Lap running 1", "Lap cache 1", "Lap cache 2", "Lap cache 3"),
        101, 1, stopwatch._clock[2][3], 4,
    )
    assert nav.lap_cache_states_seen[1][0][0] == "Lap running 2"
    assert _state(stopwatch) == original_state


def test_stopwatch_probe_fails_when_any_partial_frame_allocates(monkeypatch):
    module = _load_probe()
    runtime, stopwatch, _nav = _runtime()
    original_state = _state(stopwatch)
    fake_gc = FakeGC((100, 101, 101, 101))
    monkeypatch.setitem(module, "gc", fake_gc)
    monkeypatch.setitem(module, "time", FakeTime())
    lines = []

    with pytest.raises(RuntimeError, match="partial-frame allocation"):
        module["run"](runtime, frames=2, emit=lines.append)

    assert "delta=1 presented=1" in lines[0]
    assert lines[-2:] == [
        "STOPWATCH_FRAME_ALLOC_TOTAL frames=2 nonzero=1 "
        "missing_present=0 delta_sum=1",
        "STOPWATCH_FRAME_ALLOC_RESULT FAIL",
    ]
    assert runtime.reset_calls == [True]
    assert _state(stopwatch) == original_state


def test_stopwatch_probe_fails_closed_when_partial_prewarm_does_not_present(
        monkeypatch):
    module = _load_probe()
    runtime, stopwatch, nav = _runtime(results=(True, False))
    original_state = _state(stopwatch)
    fake_gc = FakeGC(())
    fake_time = FakeTime()
    lines = []
    monkeypatch.setitem(module, "gc", fake_gc)
    monkeypatch.setitem(module, "time", fake_time)

    with pytest.raises(RuntimeError, match="partial_prewarm_present"):
        module["run"](runtime, frames=1, emit=lines.append)

    assert lines == [
        "STOPWATCH_FRAME_ALLOC_UNAVAILABLE reason=partial_prewarm_present",
        "STOPWATCH_FRAME_ALLOC_RESULT FAIL",
    ]
    assert fake_time.sleeps == [50]
    assert fake_gc.collect_calls == 0
    assert nav.present_count == 2
    assert runtime.reset_calls == [True]
    assert _state(stopwatch) == original_state


def test_stopwatch_probe_fails_closed_without_heap_counter_or_target(
        monkeypatch):
    module = _load_probe()
    runtime, stopwatch, _nav = _runtime()
    monkeypatch.setitem(module, "gc", NoMemAllocGC())
    lines = []

    with pytest.raises(RuntimeError, match="gc_mem_alloc"):
        module["run"](runtime, emit=lines.append)

    assert lines == [
        "STOPWATCH_FRAME_ALLOC_UNAVAILABLE reason=gc_mem_alloc",
        "STOPWATCH_FRAME_ALLOC_RESULT FAIL",
    ]
    assert runtime.reset_calls == [True]
    assert _state(stopwatch) == _state(FakeStopwatch())

    module = _load_probe()
    runtime, _stopwatch, _nav = _runtime()
    runtime.stopwatch = None
    runtime.nav.stopwatch = None
    monkeypatch.setitem(module, "gc", FakeGC(()))
    lines = []

    with pytest.raises(RuntimeError, match="stopwatch_target"):
        module["run"](runtime, emit=lines.append)

    assert lines == [
        "STOPWATCH_FRAME_ALLOC_UNAVAILABLE reason=stopwatch_target",
        "STOPWATCH_FRAME_ALLOC_RESULT FAIL",
    ]
    assert runtime.reset_calls == [True]


def test_stopwatch_probe_propagates_memory_error_after_finally_restore(
        monkeypatch):
    module = _load_probe()
    runtime, stopwatch, _nav = _runtime(memory_error_at=2)
    original_state = _state(stopwatch)
    monkeypatch.setitem(module, "gc", FakeGC((700,)))
    monkeypatch.setitem(module, "time", FakeTime())

    with pytest.raises(MemoryError, match="injected present allocation"):
        module["run"](runtime, frames=1)

    assert runtime.reset_calls == [True]
    assert _state(stopwatch) == original_state


def test_stopwatch_probe_preserves_primary_error_when_cleanup_also_fails(
        monkeypatch):
    module = _load_probe()
    runtime, stopwatch, _nav = _runtime(
        memory_error_at=3, reset_error=RuntimeError("injected reset failure"))
    restore_attempts = []

    class RestoreFailureState:
        def __init__(self, target):
            self.target = target

        def restore(self, target):
            restore_attempts.append(target)
            raise RuntimeError("injected restore failure")

    monkeypatch.setitem(module, "_StopwatchState", RestoreFailureState)
    monkeypatch.setitem(module, "gc", FakeGC((700,)))
    monkeypatch.setitem(module, "time", FakeTime())

    with pytest.raises(MemoryError, match="injected present allocation"):
        module["run"](runtime, frames=1)

    assert restore_attempts == [stopwatch]
    assert runtime.reset_calls == [True]


def test_stopwatch_probe_surfaces_a_reset_only_cleanup_failure(monkeypatch):
    module = _load_probe()
    runtime, _stopwatch, _nav = _runtime(
        reset_error=RuntimeError("injected reset failure"))
    lines = []
    monkeypatch.setitem(module, "gc", FakeGC((700, 700)))
    monkeypatch.setitem(module, "time", FakeTime())

    with pytest.raises(RuntimeError, match="injected reset failure"):
        module["run"](runtime, frames=1, emit=lines.append)

    assert runtime.reset_calls == [True]
    assert lines[-1] != "STOPWATCH_FRAME_ALLOC_RESULT PASS"


def test_stopwatch_probe_surfaces_restore_failure_but_still_resets(
        monkeypatch):
    module = _load_probe()
    runtime, stopwatch, _nav = _runtime()
    restore_attempts = []

    class RestoreFailureState:
        def __init__(self, target):
            self.target = target

        def restore(self, target):
            restore_attempts.append(target)
            raise RuntimeError("injected restore failure")

    monkeypatch.setitem(module, "_StopwatchState", RestoreFailureState)
    monkeypatch.setitem(module, "gc", FakeGC((700, 700)))
    monkeypatch.setitem(module, "time", FakeTime())

    with pytest.raises(RuntimeError, match="injected restore failure"):
        module["run"](runtime, frames=1)

    assert restore_attempts == [stopwatch]
    assert runtime.reset_calls == [True]


def test_stopwatch_probe_rejects_unbounded_sample_requests_before_setup(
        monkeypatch):
    module = _load_probe()
    runtime, _stopwatch, nav = _runtime()
    monkeypatch.setitem(module, "gc", FakeGC(()))

    with pytest.raises(ValueError, match="within 1..16"):
        module["run"](runtime, frames=17)

    assert nav.present_count == 0
    assert runtime.reset_calls == []
