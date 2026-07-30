"""Fixed-state frame scheduling and row-damage aggregation.

The main loop has one scheduler rather than rediscovering render/idle/sidebar
deadlines at every call site.  ``DamageMap`` owns two preallocated row bands;
pages only declare the pixels they can safely redraw without a full frame.
"""
import time


IDLE_FRAME_MS = 66
SIDEBAR_REFRESH_MS = 5000
IDLE_LOOP_SLEEP_MS = 4
SLEEP_SCAN_MS = 25

DAMAGE_NONE = 0
DAMAGE_PARTIAL = 1
DAMAGE_FULL = 2


class DamageMap:
    """Merge a fixed number of complete-width row bands without frame churn."""

    __slots__ = ("_ranges", "_count", "full")

    def __init__(self, capacity=2):
        capacity = max(1, int(capacity))
        self._ranges = [[0, 0] for _ in range(capacity)]
        self._count = 0
        self.full = False

    @property
    def ranges(self):
        """Stable backing rows; inactive entries have a zero count."""
        return self._ranges

    @property
    def count(self):
        return self._count

    def clear(self):
        # Always clear the whole fixed backing store.  A capacity overflow
        # first resets ``_count`` to zero; clearing only active entries after
        # that would let a later partial present resend stale row bands.
        ranges = self._ranges
        index = 0
        range_count = len(ranges)
        while index < range_count:
            row_range = ranges[index]
            row_range[0] = 0
            row_range[1] = 0
            index += 1
        self._count = 0
        self.full = False

    def request_full(self):
        # Keep inactive backing entries observably empty even while this
        # request is promoted to a full redraw.  ``ranges`` is deliberately
        # passed by stable identity to the display layer.
        self.clear()
        self.full = True

    def _remove(self, index):
        last = self._count - 1
        while index < last:
            source = self._ranges[index + 1]
            target = self._ranges[index]
            target[0] = source[0]
            target[1] = source[1]
            index += 1
        target = self._ranges[last]
        target[0] = 0
        target[1] = 0
        self._count = last

    def add(self, row_start, row_count):
        """Add one band, coalescing overlaps or falling back to a full frame."""
        if self.full:
            return False
        row_start = max(0, int(row_start))
        row_count = int(row_count)
        if row_count <= 0:
            return False
        row_end = row_start + row_count
        target_index = -1

        ranges = self._ranges
        index = 0
        active_count = self._count
        while index < active_count:
            candidate = ranges[index]
            candidate_start = candidate[0]
            candidate_end = candidate_start + candidate[1]
            if not (row_end < candidate_start or candidate_end < row_start):
                target_index = index
                break
            index += 1

        if target_index < 0:
            if active_count == len(ranges):
                self.request_full()
                return False
            target_index = active_count
            active_count += 1
            self._count = active_count
            target = ranges[target_index]
            target[0] = row_start
            target[1] = row_count
        else:
            target = ranges[target_index]
            target_start = min(target[0], row_start)
            target_end = max(target[0] + target[1], row_end)
            target[0] = target_start
            target[1] = target_end - target_start

        index = 0
        while index < self._count:
            if index == target_index:
                index += 1
                continue
            candidate = self._ranges[index]
            target_start = target[0]
            target_end = target_start + target[1]
            candidate_start = candidate[0]
            candidate_end = candidate_start + candidate[1]
            if target_end < candidate_start or candidate_end < target_start:
                index += 1
                continue
            target_start = min(target_start, candidate_start)
            target_end = max(target_end, candidate_end)
            target[0] = target_start
            target[1] = target_end - target_start
            self._remove(index)
            if index < target_index:
                target_index -= 1
                target = ranges[target_index]
        return True


class FrameScheduler:
    """Own immediate input frames and quiet-work deadlines with fixed state."""

    __slots__ = (
        "idle_frame_ms", "background_idle_ms", "sidebar_refresh_ms",
        "last_render", "last_input", "last_sidebar", "dirty")

    def __init__(self, now=0, idle_frame_ms=IDLE_FRAME_MS,
                 background_idle_ms=750,
                 sidebar_refresh_ms=SIDEBAR_REFRESH_MS):
        self.idle_frame_ms = max(1, int(idle_frame_ms))
        self.background_idle_ms = max(0, int(background_idle_ms))
        self.sidebar_refresh_ms = max(1, int(sidebar_refresh_ms))
        self.last_render = now
        self.last_input = now
        self.last_sidebar = now
        self.dirty = False

    def note_input(self, now):
        """Record physical activity without assuming it changed pixels."""
        self.last_input = now

    def request_render(self):
        self.dirty = True

    def force_render(self, now):
        self.dirty = True
        self.last_render = time.ticks_add(now, -self.idle_frame_ms)

    def should_present(self, now, continuous=False, input_changed=False,
                       continuous_frame_ms=0):
        if input_changed:
            return True
        frame_ms = (continuous_frame_ms
                    if continuous and continuous_frame_ms > 0
                    else self.idle_frame_ms)
        if time.ticks_diff(now, self.last_render) < frame_ms:
            return False
        return self.dirty or bool(continuous)

    def mark_presented(self, now):
        self.last_render = now
        self.dirty = False

    def clear_render_request(self):
        """Drop a request that produced no display transfer.

        ``last_render`` intentionally remains unchanged: a continuous page
        (for example the stopwatch) still owns its next frame deadline, while
        a no-damage input event does not become a phantom measured present.
        """
        self.dirty = False

    def sidebar_poll_due(self, now, quiet):
        if (not quiet or time.ticks_diff(now, self.last_sidebar)
                < self.sidebar_refresh_ms):
            return False
        self.last_sidebar = now
        return True

    def force_sidebar_poll(self, now):
        """Make the first post-boot quiet loop refresh cached sidebar data."""
        self.last_sidebar = time.ticks_add(now, -self.sidebar_refresh_ms)

    def background_due(self, now):
        return (time.ticks_diff(now, self.last_input)
                >= self.background_idle_ms)

    def reset(self, now, force_render=False):
        self.last_input = now
        self.last_sidebar = now
        self.dirty = True
        if force_render:
            self.last_render = time.ticks_add(now, -self.idle_frame_ms)
        else:
            self.last_render = now
