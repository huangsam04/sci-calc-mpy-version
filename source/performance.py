"""Bounded device-side timing samples shared by the UI and benchmark runner."""
import time


class PerformanceMetrics:
    def __init__(self, sample_limit=128):
        self.sample_limit = sample_limit
        self._boot_phases = []
        self._boot_last = None
        self._input_started = None
        self._frame_us = []
        self._input_to_present_us = []
        self._gc_us = []
        self._runtime = None

    def _now_ms(self):
        return time.ticks_ms()

    def _now_us(self):
        return time.ticks_us()

    def _append(self, samples, value):
        if len(samples) >= self.sample_limit:
            samples.pop(0)
        samples.append(max(0, int(value)))

    def start_boot(self, now=None):
        self._boot_phases = []
        self._boot_last = self._now_ms() if now is None else now

    def mark_boot(self, name, now=None):
        if self._boot_last is None:
            self.start_boot(now)
            return
        current = self._now_ms() if now is None else now
        self._boot_phases.append((name, time.ticks_diff(current, self._boot_last)))
        self._boot_last = current

    def reset_run(self):
        self._input_started = None
        self._frame_us = []
        self._input_to_present_us = []
        self._gc_us = []

    def record_input(self, now=None):
        self._input_started = self._now_us() if now is None else now

    def record_frame(self, elapsed_us, now=None):
        self._append(self._frame_us, elapsed_us)
        if self._input_started is not None:
            current = self._now_us() if now is None else now
            self._append(self._input_to_present_us,
                         time.ticks_diff(current, self._input_started))
            self._input_started = None

    def record_gc(self, elapsed_us):
        self._append(self._gc_us, elapsed_us)

    def bind_runtime(self, nav, root, targets):
        self._runtime = (nav, root, tuple(targets))

    def runtime(self):
        return self._runtime

    def _summary(self, samples):
        count = len(samples)
        if count == 0:
            return {"count": 0, "p95_us": 0, "max_us": 0}
        ordered = list(samples)
        ordered.sort()
        p95_index = max(0, (count * 95 + 99) // 100 - 1)
        return {
            "count": count,
            "p95_us": ordered[p95_index],
            "max_us": ordered[-1],
        }

    def snapshot(self):
        return {
            "boot_phases_ms": list(self._boot_phases),
            "input_to_present_us": self._summary(self._input_to_present_us),
            "frame_us": self._summary(self._frame_us),
            "gc_us": self._summary(self._gc_us),
        }


metrics = PerformanceMetrics()
