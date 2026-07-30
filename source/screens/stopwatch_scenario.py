"""Stopwatch acceptance leases, imported only on demand."""

import time

from screens.stopwatch import LAP_COUNT, LAP_MAX


class StopwatchScenarioLease:
    """Own one scratch stopwatch state and restore it on close."""

    __slots__ = (
        "_screen", "_closed", "_saved_running", "_saved_paused",
        "_saved_start_time", "_saved_elapsed", "_saved_laps",
        "_saved_lap_cursor", "_saved_view_offset", "_saved_next_lap_num",
        "_saved_lap_revision", "_scratch_laps", "_lap_window_active",
        "_lap_window_verified", "_terminal_lap_view_offset")

    def __init__(self, screen):
        saved_laps = screen._clock[2][3]
        if len(saved_laps) > LAP_MAX:
            raise RuntimeError(
                "Stopwatch lap snapshot exceeds its fixed limit")

        self._screen = screen
        self._closed = False
        self._saved_running = screen._clock[1]
        self._saved_paused = screen._clock[2][0]
        self._saved_start_time = screen._clock[2][1]
        self._saved_elapsed = screen._clock[2][2]
        # Retain the resident list by reference.  Copying twenty lap tuples
        # would create the exact transient heap peak this lease avoids.
        self._saved_laps = saved_laps
        self._saved_lap_cursor = screen._clock[3][0]
        self._saved_view_offset = screen._clock[3][1]
        self._saved_next_lap_num = screen._clock[3][2]
        self._saved_lap_revision = screen._clock[3][3]
        # This is the only scenario-owned collection.  Allocate it before any
        # resident field changes so an OOM cannot partially enter the lease.
        self._scratch_laps = []
        self._lap_window_active = True
        self._lap_window_verified = False
        self._terminal_lap_view_offset = -1

        screen._runtime[1][1] = self
        screen._clock[1] = False
        screen._clock[2][0] = False
        screen._clock[2][1] = 0
        screen._clock[2][2] = 0
        screen._clock[2][3] = self._scratch_laps
        screen._clock[3][0] = 0
        screen._clock[3][1] = 0
        screen._clock[3][2] = 1
        screen._clock[3][3] = 0

    def _screen_for_action(self):
        screen = self._screen
        if self._closed or screen is None:
            raise RuntimeError("Stopwatch scenario lease is closed")
        if screen._runtime[1][1] is not self:
            raise RuntimeError("Stopwatch scenario lease is not active")
        if screen._clock[2][3] is not self._scratch_laps:
            raise RuntimeError("Stopwatch scenario scratch state changed")
        if not self._lap_window_active:
            raise RuntimeError("Stopwatch scenario lap window is closed")
        return screen

    @property
    def lap_window_active(self):
        """Expose only the lease-owned page-window state to the controller."""
        return not self._closed and self._lap_window_active

    @property
    def lap_window_verified(self):
        """Report whether the one bounded leave action completed its proof."""
        return not self._closed and self._lap_window_verified

    def start(self):
        """Perform only the stopwatch start/resume action on scratch state."""
        screen = self._screen_for_action()
        if screen._clock[1]:
            return False
        now = time.ticks_ms()
        if screen._clock[2][0]:
            start_time = time.ticks_add(now, -screen._clock[2][2])
        else:
            start_time = now
        screen._clock[2][1] = start_time
        screen._clock[2][0] = False
        screen._clock[1] = True
        return True

    def pause(self):
        """Perform only the stopwatch pause action on scratch state."""
        screen = self._screen_for_action()
        if not screen._clock[1]:
            return False
        elapsed = time.ticks_diff(time.ticks_ms(), screen._clock[2][1])
        screen._clock[2][2] = elapsed
        screen._clock[1] = False
        screen._clock[2][0] = True
        return True

    def lap(self):
        """Perform only one bounded lap action without rendering text."""
        screen = self._screen_for_action()
        if not screen._clock[1]:
            return False
        elapsed = time.ticks_diff(time.ticks_ms(), screen._clock[2][1])
        next_lap_num = screen._clock[3][2] + 1
        next_revision = screen._clock[3][3] + 1
        entry = (screen._clock[3][2], elapsed)
        laps = self._scratch_laps
        # Finish allocation-prone scalar work before changing screen metadata.
        laps.insert(0, entry)
        if len(laps) > LAP_MAX:
            del laps[LAP_MAX:]
        screen._clock[3][2] = next_lap_num
        screen._clock[3][3] = next_revision
        self._terminal_lap_view_offset = -1
        self._lap_window_verified = False
        return True

    def move_lap_cursor(self, direction):
        """Move one lap position and update its bounded visible window."""
        screen = self._screen_for_action()
        if direction not in (-1, 1):
            raise ValueError("Stopwatch scenario cursor direction is invalid")
        laps = self._scratch_laps
        total = len(laps)
        cursor = screen._clock[3][0]
        next_cursor = cursor + direction
        if next_cursor < 0 or next_cursor >= total:
            return False

        view_offset = screen._clock[3][1]
        if next_cursor < view_offset:
            next_view_offset = next_cursor
        elif next_cursor >= view_offset + LAP_COUNT:
            next_view_offset = next_cursor - LAP_COUNT + 1
        else:
            next_view_offset = view_offset
        max_view_offset = total - LAP_COUNT
        if max_view_offset < 0:
            max_view_offset = 0
        if next_view_offset < 0:
            next_view_offset = 0
        elif next_view_offset > max_view_offset:
            next_view_offset = max_view_offset

        screen._clock[3][0] = next_cursor
        screen._clock[3][1] = next_view_offset
        if next_view_offset == LAP_MAX - LAP_COUNT:
            self._terminal_lap_view_offset = next_view_offset
        return True

    def verify_and_leave_lap_window(self):
        """Prove the full lap traversal, then close the lease-owned window."""
        screen = self._screen_for_action()
        laps = self._scratch_laps
        if not screen._clock[1]:
            raise RuntimeError(
                "Stopwatch stopped while leaving its lap window")
        if (len(laps) != LAP_MAX
                or screen._clock[3][2] != LAP_MAX + 1
                or laps[0][0] != LAP_MAX
                or laps[-1][0] != 1):
            raise RuntimeError("Stopwatch lap retention proof failed")
        if screen._clock[3][0] != 0 or screen._clock[3][1] != 0:
            raise RuntimeError("Stopwatch lap cursor did not return")
        if self._terminal_lap_view_offset != LAP_MAX - LAP_COUNT:
            raise RuntimeError(
                "Stopwatch lap terminal window proof failed")

        self._lap_window_active = False
        self._lap_window_verified = True
        return True

    def reset(self):
        """Perform only reset, retaining the already-owned scratch list."""
        screen = self._screen_for_action()
        laps = self._scratch_laps
        changed = bool(screen._clock[1] or screen._clock[2][0] or screen._clock[2][2]
                       or laps or screen._clock[3][0] or screen._clock[3][1]
                       or screen._clock[3][2] != 1)
        if not changed:
            return False
        next_revision = screen._clock[3][3] + 1
        del laps[:]
        screen._clock[1] = False
        screen._clock[2][0] = False
        screen._clock[2][1] = 0
        screen._clock[2][2] = 0
        screen._clock[3][0] = 0
        screen._clock[3][1] = 0
        screen._clock[3][2] = 1
        screen._clock[3][3] = next_revision
        self._terminal_lap_view_offset = -1
        self._lap_window_verified = False
        return True

    def close(self):
        """Restore the exact resident checkpoint and release scratch ownership."""
        if self._closed:
            return True
        screen = self._screen
        if screen is None or screen._runtime[1][1] is not self:
            raise RuntimeError("Stopwatch scenario lease is not active")
        screen._clock[1] = self._saved_running
        screen._clock[2][0] = self._saved_paused
        screen._clock[2][1] = self._saved_start_time
        screen._clock[2][2] = self._saved_elapsed
        screen._clock[2][3] = self._saved_laps
        screen._clock[3][0] = self._saved_lap_cursor
        screen._clock[3][1] = self._saved_view_offset
        screen._clock[3][2] = self._saved_next_lap_num
        screen._clock[3][3] = self._saved_lap_revision
        screen._runtime[1][1] = None
        self._screen = None
        self._scratch_laps = None
        self._saved_laps = None
        self._lap_window_active = False
        self._lap_window_verified = False
        self._terminal_lap_view_offset = -1
        self._closed = True
        return True


class StopwatchPageScenarioLease:
    """Prepare a real Stopwatch page activation without running its timer."""

    __slots__ = (
        "_screen", "_closed", "_prepared", "_saved_running",
        "_saved_paused", "_saved_start_time", "_saved_elapsed",
        "_saved_laps", "_saved_lap_cursor", "_saved_view_offset",
        "_saved_next_lap_num", "_saved_lap_revision",
        "_saved_presented_running", "_saved_presented_elapsed_cs",
        "_saved_presented_lap_revision", "_saved_presented_lap_cursor",
        "_saved_presented_view_offset", "_saved_presented_footer_state")

    def __init__(self, screen):
        if (screen._runtime[1][1] is not None
                or screen._runtime[1][2] is not None):
            raise RuntimeError(
                "Stopwatch page scenario transaction is already active")
        if len(screen._clock[2][3]) > LAP_MAX:
            raise RuntimeError(
                "Stopwatch lap snapshot exceeds its fixed limit")

        self._screen = screen
        self._closed = False
        self._prepared = False
        self._saved_running = screen._clock[1]
        self._saved_paused = screen._clock[2][0]
        self._saved_start_time = screen._clock[2][1]
        self._saved_elapsed = screen._clock[2][2]
        self._saved_laps = screen._clock[2][3]
        self._saved_lap_cursor = screen._clock[3][0]
        self._saved_view_offset = screen._clock[3][1]
        self._saved_next_lap_num = screen._clock[3][2]
        self._saved_lap_revision = screen._clock[3][3]
        self._saved_presented_running = screen._render[1][0]
        self._saved_presented_elapsed_cs = screen._render[1][1]
        self._saved_presented_lap_revision = screen._render[1][2]
        self._saved_presented_lap_cursor = screen._render[1][3]
        self._saved_presented_view_offset = screen._render[2][0]
        self._saved_presented_footer_state = screen._render[2][1]
        screen._runtime[1][2] = self

    def _require_open(self):
        screen = self._screen
        if self._closed or screen is None:
            raise RuntimeError(
                "Stopwatch page scenario transaction is closed")
        if screen._runtime[1][2] is not self:
            raise RuntimeError(
                "Stopwatch page scenario transaction is not active")
        return screen

    def _require_pristine_state(self, screen):
        """Reject external timer/page mutations before preparing activation."""
        if (screen._clock[1] != self._saved_running
                or screen._clock[2][0] != self._saved_paused
                or screen._clock[2][1] != self._saved_start_time
                or screen._clock[2][2] != self._saved_elapsed
                or screen._clock[2][3] is not self._saved_laps
                or screen._clock[3][0] != self._saved_lap_cursor
                or screen._clock[3][1] != self._saved_view_offset
                or screen._clock[3][2] != self._saved_next_lap_num
                or screen._clock[3][3] != self._saved_lap_revision):
            raise RuntimeError("Stopwatch page scenario state changed")

    def _restore_state(self, screen):
        """Restore direct snapshots; callers retain them if this raises."""
        screen._clock[1] = self._saved_running
        screen._clock[2][0] = self._saved_paused
        screen._clock[2][1] = self._saved_start_time
        screen._clock[2][2] = self._saved_elapsed
        screen._clock[2][3] = self._saved_laps
        screen._clock[3][0] = self._saved_lap_cursor
        screen._clock[3][1] = self._saved_view_offset
        screen._clock[3][2] = self._saved_next_lap_num
        screen._clock[3][3] = self._saved_lap_revision
        screen._render[1][0] = self._saved_presented_running
        screen._render[1][1] = self._saved_presented_elapsed_cs
        screen._render[1][2] = self._saved_presented_lap_revision
        screen._render[1][3] = self._saved_presented_lap_cursor
        screen._render[2][0] = self._saved_presented_view_offset
        screen._render[2][1] = self._saved_presented_footer_state

    def step(self):
        """Prepare one activation-visible redraw state without rendering it."""
        screen = self._require_open()
        if self._prepared:
            return True
        self._require_pristine_state(screen)
        screen._render[1][0] = None
        screen._render[1][1] = None
        screen._render[1][2] = None
        screen._render[1][3] = None
        screen._render[2][0] = None
        screen._render[2][1] = None
        self._prepared = True
        return True

    def close(self, primary_error=None):
        """Restore the resident Stopwatch without hiding a primary failure."""
        if self._closed:
            if primary_error is not None:
                raise primary_error
            return True
        cleanup_error = None
        cleanup_is_memory = False
        try:
            screen = self._require_open()
            self._restore_state(screen)
        except MemoryError as error:
            cleanup_error = error
            cleanup_is_memory = True
        except Exception as error:
            cleanup_error = error

        if cleanup_error is not None:
            if cleanup_is_memory:
                if isinstance(primary_error, MemoryError):
                    raise primary_error
                raise cleanup_error
            if primary_error is not None:
                raise primary_error
            raise cleanup_error
        screen._runtime[1][2] = None
        self._screen = None
        self._saved_laps = None
        self._closed = True
        if primary_error is not None:
            raise primary_error
        return True
