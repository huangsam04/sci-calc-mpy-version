"""Single composition and low-memory presentation path for UI frames."""
import sys
import time

from ui.memory import MemoryManager
from ui.theme import CONTENT_W


# A transition leaves the outgoing page in the SSD1322's own display RAM and
# renders the incoming page into the normal live framebuffer.  Only a narrow
# packed strip is copied for each incremental hardware reveal.  Four
# controller columns are 16 pixels / 8 GS4 bytes per row, or 512 bytes at
# 64px.  Seven KiB remains a hard start gate; destination capture can still
# degrade safely on pressure.
TRANSITION_STRIP_GROUPS = 4
TRANSITION_ACTIVE_HEADROOM = 7 * 1024
TRANSITION_TOTAL_GROUPS = (CONTENT_W + 3) // 4


try:
    import micropython  # type: ignore
except ImportError:
    micropython = None


_HAS_FAST_REGION_COPY = (micropython is not None
                         and sys.implementation.name == "micropython")


if _HAS_FAST_REGION_COPY:
    @micropython.viper
    def _copy_packed_region(dst: ptr8, src: ptr8, src_offset: int,
                            row_bytes: int, rows: int, stride: int):
        row: int = 0
        while row < rows:
            dst_base: int = row * row_bytes
            src_base: int = row * stride + src_offset
            index: int = 0
            while index < row_bytes:
                dst[dst_base + index] = src[src_base + index]
                index += 1
            row += 1
else:
    def _copy_packed_region(dst, src, src_offset, row_bytes, rows, stride):
        """Host fallback matching the allocation-free Viper copier."""
        for row in range(rows):
            dst_base = row * row_bytes
            src_base = row * stride + src_offset
            for index in range(row_bytes):
                dst[dst_base + index] = src[src_base + index]


class Renderer:
    """Compose frames and reveal pages through the SSD1322's own RAM."""

    def __init__(self, display, sidebar, memory=None):
        self.display = display
        self.sidebar = sidebar
        self.memory = memory or MemoryManager()
        self.last_present_us = 0
        self._transition_strip = None
        self._transition_views = None
        self._transition_groups_presented = 0
        self._outgoing_screen = None
        self._transition_allocation_enabled = False
        self._transitions_available = True
        self._chrome_ready = False

    def reserve_transition_buffers(self):
        """Legacy explicit opt-in for standalone callers.

        Production boot uses ``enable_transition_buffers`` only after the
        core page has reached the display.  This compatibility name reserves
        only the small reveal strip, never the mutually-exclusive plot area.
        """
        return self.enable_transition_buffers()

    def enable_transition_buffers(self):
        """Try to acquire the fixed reveal strip for this memory phase."""
        self._transition_allocation_enabled = True
        self._transitions_available = True
        if self._transition_strip is None and not self._has_allocation_headroom():
            self._transition_allocation_enabled = False
            self._transitions_available = False
            return False
        return self._ensure_transition_buffers()

    def release_transition_buffers(self):
        """Free the optional strip before a memory-intensive operation."""
        had_buffer = self._transition_strip is not None
        self._transition_views = None
        self._transition_strip = None
        self._transition_groups_presented = 0
        self._outgoing_screen = None
        self._transition_allocation_enabled = False
        self._transitions_available = True
        released = self.memory.release_buffer("transition_strip")
        if had_buffer or released:
            self.memory.collect()
        return had_buffer or released

    def _ensure_transition_buffers(self):
        """Allocate the reveal strip only during an explicit optional phase."""
        if self._transition_strip is not None:
            return True
        if (not self._transition_allocation_enabled
                or not self._transitions_available):
            return False

        buffer_length = TRANSITION_STRIP_GROUPS * 2 * self.display.height
        try:
            strip = self.memory.reserve_buffer(
                "transition_strip", buffer_length, bytearray)
            if strip is None:
                raise MemoryError()
            base_view = memoryview(strip)
            views = tuple(
                base_view[:groups * 2 * self.display.height]
                for groups in range(1, TRANSITION_STRIP_GROUPS + 1))
        except MemoryError:
            self.memory.release_buffer("transition_strip")
            views = None
            base_view = None
            strip = None
            self.memory.collect()
            self._transitions_available = False
            self._transition_allocation_enabled = False
            return False

        self._transition_strip = strip
        self._transition_views = views
        return True

    def _transition_buffer_length(self):
        return TRANSITION_STRIP_GROUPS * 2 * self.display.height

    def _has_allocation_headroom(self):
        return self.memory.has_headroom(
            self._transition_buffer_length() + TRANSITION_ACTIVE_HEADROOM)

    def can_start_transition(self):
        """Return false before a page reveal would consume unsafe headroom."""
        return (self._transition_strip is not None
                and self.memory.has_headroom(TRANSITION_ACTIVE_HEADROOM))

    def capture_outgoing(self, outgoing):
        """Keep the outgoing page in hardware RAM while preparing the wipe."""
        if not self._ensure_transition_buffers():
            return False
        self.hold_outgoing(outgoing)
        return True

    def hold_outgoing(self, outgoing):
        """Ensure the old page is in OLED RAM without allocating reveal RAM."""
        if self._outgoing_screen is not outgoing:
            self.present(outgoing)
        return True

    def _draw_minimal_default(self, incoming):
        """Draw an allocation-bounded page shell after a page draw OOM."""
        title = getattr(incoming, "transition_title",
                        incoming.__class__.__name__)
        if not isinstance(title, str) or len(title) > 20:
            title = "Loading"
        self.display.draw_rectangle(1, 1, CONTENT_W - 2,
                                    self.display.height - 2, 8)
        self.display.draw_text8x8(6, 5, title, gs=15)
        self.display.draw_hline(5, 16, CONTENT_W - 10, 6)
        self.display.draw_text8x8(6, self.display.height - 11,
                                  "Loading...", gs=8)

    def _draw_incoming(self, incoming, default):
        drawer = (getattr(incoming, "draw_transition_default", None)
                  if default else None)
        try:
            if drawer is None:
                incoming.draw(self.display)
            else:
                drawer(self.display)
        except MemoryError:
            self.display.clear_buffers(0)
            self._draw_minimal_default(incoming)

    def _draw_sidebar_safe(self):
        try:
            self.sidebar.draw(self.display)
        except MemoryError:
            # The fixed content reveal remains valid without optional chrome.
            # A canonical idle frame will restore the sidebar after settling.
            pass

    def capture_incoming(self, incoming, default=False):
        """Render incoming once; do not overwrite the OLED until animation.

        Page-residency transitions deliberately capture the page's empty,
        allocation-bounded layout.  Real state is restored only after the
        hardware reveal has completed.
        """
        if self._transition_strip is None:
            return False
        self.display.clear_buffers(0)
        self._draw_incoming(incoming, default)
        self._draw_sidebar_safe()
        self._chrome_ready = True
        self._transition_groups_presented = 0
        return True

    def present_default(self, incoming):
        """Present a target shell at the dark midpoint of a fade fallback."""
        self.display.clear_buffers(0)
        self._draw_incoming(incoming, True)
        self._draw_sidebar_safe()
        self._chrome_ready = True
        started = time.ticks_us()
        self.display.present()
        self.last_present_us = time.ticks_diff(time.ticks_us(), started)
        self._outgoing_screen = incoming

    def capture_transition(self, outgoing, incoming):
        """Compatibility helper for callers that do not need a reclaim seam."""
        return (self.capture_outgoing(outgoing)
                and self.capture_incoming(incoming))

    def _present_composed(self):
        # Sidebar owns and clears the complete non-content region.
        self.sidebar.draw(self.display)
        self._chrome_ready = True
        started = time.ticks_us()
        self.display.present()
        self.last_present_us = time.ticks_diff(time.ticks_us(), started)

    def present(self, screen):
        """Present one canonical live page frame."""
        self.display.clear_buffers(0)
        screen.draw(self.display)
        error_drawer = getattr(screen, "draw_residency_error", None)
        if error_drawer is not None:
            error_drawer(self.display)
        self._present_composed()
        self._outgoing_screen = screen

    def present_transition(self, eased_progress, forward):
        """Reveal only newly exposed controller columns of the incoming UI."""
        target = min(TRANSITION_TOTAL_GROUPS,
                     int(TRANSITION_TOTAL_GROUPS * eased_progress))
        if eased_progress >= 1.0:
            target = TRANSITION_TOTAL_GROUPS
        previous = self._transition_groups_presented
        if target <= previous:
            self.last_present_us = 0
            return

        remaining = target - previous
        group_start = (TRANSITION_TOTAL_GROUPS - target
                       if forward else previous)
        self.last_present_us = 0
        while remaining:
            groups = min(remaining, TRANSITION_STRIP_GROUPS)
            row_bytes = groups * 2
            _copy_packed_region(
                self._transition_strip, self.display.gs4_buf,
                group_start * 2, row_bytes, self.display.height,
                self.display.byte_width)
            started = time.ticks_us()
            self.display.present_region(
                group_start, groups, self._transition_views[groups - 1])
            self.last_present_us += time.ticks_diff(time.ticks_us(), started)
            group_start += groups
            remaining -= groups
        self._transition_groups_presented = target

    def finish_transition(self, screen, forward):
        """Expose the final columns; the live framebuffer is now canonical."""
        self.present_transition(1.0, forward)
        self._outgoing_screen = screen
        return True
