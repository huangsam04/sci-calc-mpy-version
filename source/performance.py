"""Bounded device-side timing samples shared by the UI and benchmark runner."""
import time


class _FixedSampleWindow:
    """A cyclic timing window whose backing list is allocated at boot only."""

    def __init__(self, limit):
        self.values = [0] * max(1, int(limit))
        self.count = 0
        self._next = 0

    def append(self, value):
        values = self.values
        values[self._next] = max(0, int(value))
        self._next += 1
        if self._next == len(values):
            self._next = 0
        if self.count < len(values):
            self.count += 1

    def reset(self):
        self.count = 0
        self._next = 0


class PerformanceMetrics:
    def __init__(self, sample_limit=16, frame_bucket_us=500,
                 frame_bucket_count=128):
        self.sample_limit = max(1, int(sample_limit))
        self._frame_bucket_us = max(1, int(frame_bucket_us))
        self._frame_histogram = [0] * max(1, int(frame_bucket_count))
        self._frame_sample_count = 0
        self._frame_max_us = 0
        self._boot_phases = []
        self._boot_last = None
        self._input_started = None
        # These windows are deliberately allocated with the module at boot.
        # Appending a timing value must never grow a Python list while the
        # constrained ESP32 heap is rendering a page.
        self._input_to_present_us = _FixedSampleWindow(self.sample_limit)
        self._gc_us = _FixedSampleWindow(self.sample_limit)

    def _now_ms(self):
        return time.ticks_ms()

    def _now_us(self):
        return time.ticks_us()

    def _append(self, samples, value):
        samples.append(value)

    def _reset_frames(self):
        # Repeated navigation produces far more frame samples than it is
        # sensible to retain as Python integers. A fixed histogram preserves
        # every frame for p95/max while keeping the device heap predictable.
        for index in range(len(self._frame_histogram)):
            self._frame_histogram[index] = 0
        self._frame_sample_count = 0
        self._frame_max_us = 0

    def _record_frame(self, elapsed_us):
        value = max(0, int(elapsed_us))
        self._frame_sample_count += 1
        if value > self._frame_max_us:
            self._frame_max_us = value
        bucket = value // self._frame_bucket_us
        if bucket >= len(self._frame_histogram):
            # The final bucket deliberately absorbs rare slow frames. If p95
            # lands there, _frame_summary reports the exact maximum instead
            # of hiding a regression behind an artificially low percentile.
            bucket = len(self._frame_histogram) - 1
        self._frame_histogram[bucket] += 1

    def _frame_summary(self):
        count = self._frame_sample_count
        if count == 0:
            return {"count": 0, "p95_us": 0, "max_us": 0}
        target = (count * 95 + 99) // 100
        seen = 0
        for index, bucket_count in enumerate(self._frame_histogram):
            seen += bucket_count
            if seen >= target:
                if index == len(self._frame_histogram) - 1:
                    p95 = self._frame_max_us
                else:
                    # Return the bucket's upper bound so a quantised p95 is
                    # conservative rather than understating frame cost.
                    p95 = min(self._frame_max_us,
                              (index + 1) * self._frame_bucket_us - 1)
                return {"count": count, "p95_us": p95,
                        "max_us": self._frame_max_us}
        return {"count": count, "p95_us": self._frame_max_us,
                "max_us": self._frame_max_us}

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
        self._reset_frames()
        self._input_to_present_us.reset()
        self._gc_us.reset()

    def record_input(self, now=None):
        self._input_started = self._now_us() if now is None else now

    def record_input_to_present(self, elapsed_us):
        """Record a completed edge-to-visible latency from an external runner."""
        self._append(self._input_to_present_us, elapsed_us)

    def record_frame(self, elapsed_us, now=None):
        self._record_frame(elapsed_us)
        if self._input_started is not None:
            current = self._now_us() if now is None else now
            self._append(self._input_to_present_us,
                         time.ticks_diff(current, self._input_started))
            self._input_started = None

    def record_gc(self, elapsed_us):
        self._append(self._gc_us, elapsed_us)

    def _summary(self, samples):
        count = samples.count
        if count == 0:
            return {"count": 0, "p95_us": 0, "max_us": 0}

        # Snapshotting normally happens after a benchmark, when the renderer
        # has the least spare heap.  Sort the already-reserved cyclic window
        # in place instead of allocating a temporary copy for ``list(... )``.
        ordered = samples.values
        for index in range(1, count):
            value = ordered[index]
            insert_at = index - 1
            while insert_at >= 0 and ordered[insert_at] > value:
                ordered[insert_at + 1] = ordered[insert_at]
                insert_at -= 1
            ordered[insert_at + 1] = value
        p95_index = max(0, (count * 95 + 99) // 100 - 1)
        return {
            "count": count,
            "p95_us": ordered[p95_index],
            "max_us": ordered[count - 1],
        }

    def snapshot(self):
        return {
            "boot_phases_ms": list(self._boot_phases),
            "input_to_present_us": self._summary(self._input_to_present_us),
            "frame_us": self._frame_summary(),
            "gc_us": self._summary(self._gc_us),
        }


metrics = PerformanceMetrics()
