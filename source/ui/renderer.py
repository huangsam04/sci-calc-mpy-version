"""Single composition and presentation path for application frames."""
import sys
import time

from framebuf import FrameBuffer, GS4_HMSB  # type: ignore

from ui.theme import CONTENT_W


try:
    import micropython  # type: ignore
except ImportError:
    micropython = None


_HAS_FAST_COMPOSITOR = (micropython is not None
                        and sys.implementation.name == "micropython")


if _HAS_FAST_COMPOSITOR:
    # The SSD1322 framebuffer uses 128 bytes per 256-pixel row. Page layers
    # are 210 pixels wide, so they occupy 105 packed GS4 bytes per row.
    @micropython.viper
    def _compose_forward(dst: ptr8, outgoing: ptr8, incoming: ptr8,
                         shift: int):
        row: int = 0
        split: int = 105 - shift
        while row < 64:
            dst_base: int = row * 128
            src_base: int = row * 105
            index: int = 0
            while index < split:
                dst[dst_base + index] = outgoing[src_base + shift + index]
                index += 1
            index = 0
            while index < shift:
                dst[dst_base + split + index] = incoming[src_base + index]
                index += 1
            row += 1


    @micropython.viper
    def _compose_backward(dst: ptr8, outgoing: ptr8, incoming: ptr8,
                          shift: int):
        row: int = 0
        remaining: int = 105 - shift
        while row < 64:
            dst_base: int = row * 128
            src_base: int = row * 105
            index: int = 0
            while index < shift:
                dst[dst_base + index] = incoming[src_base + remaining + index]
                index += 1
            index = 0
            while index < remaining:
                dst[dst_base + shift + index] = outgoing[src_base + index]
                index += 1
            row += 1


class Renderer:
    """Compose page content and fixed chrome, then present exactly once."""

    def __init__(self, display, sidebar):
        self.display = display
        self.sidebar = sidebar
        self.last_present_us = 0
        buffer_length = ((CONTENT_W + 1) // 2) * display.height
        self._outgoing_buffer = bytearray(buffer_length)
        self._incoming_buffer = bytearray(buffer_length)
        self._outgoing = FrameBuffer(self._outgoing_buffer, CONTENT_W,
                                     display.height, GS4_HMSB)
        self._incoming = FrameBuffer(self._incoming_buffer, CONTENT_W,
                                     display.height, GS4_HMSB)
        self._outgoing_screen = None

    def _capture(self, screen, target):
        self.display.clear_buffers(0)
        screen.draw(self.display)
        target.fill(0)
        target.blit(self.display.gs4_fb, 0, 0)

    def capture_transition(self, outgoing, incoming):
        """Capture both content-only page layers into reusable buffers."""
        # A normal present has already rendered the current page into the
        # outgoing layer. Reuse it at navigation time so an input event does
        # not synchronously redraw both pages before the first slide frame.
        if self._outgoing_screen is not outgoing:
            self._capture(outgoing, self._outgoing)
            self._outgoing_screen = outgoing
        self._capture(incoming, self._incoming)

    def _present_composed(self):
        # Sidebar.draw first erases the entire non-content region. This also
        # clips page layers that extended into it during a slide.
        self.sidebar.draw(self.display)
        started = time.ticks_us()
        self.display.present()
        self.last_present_us = time.ticks_diff(time.ticks_us(), started)

    def present(self, screen):
        """Present one canonical live page frame."""
        self.display.clear_buffers(0)
        screen.draw(self.display)
        self._outgoing.fill(0)
        self._outgoing.blit(self.display.gs4_fb, 0, 0)
        self._outgoing_screen = screen
        self._present_composed()

    def present_transition(self, eased_progress, forward):
        """Present one composited non-linear slide frame."""
        width = CONTENT_W
        distance = int(width * eased_progress)
        if (_HAS_FAST_COMPOSITOR and self.display.width == 256
                and self.display.height == 64):
            # Packed GS4 bytes hold two horizontal pixels. Quantising to an
            # even offset permits a direct native byte composition; the at
            # most one-pixel adjustment is not visible in motion.
            distance -= distance & 1
            if forward:
                _compose_forward(self.display.gs4_buf,
                                 self._outgoing_buffer,
                                 self._incoming_buffer,
                                 distance // 2)
            else:
                _compose_backward(self.display.gs4_buf,
                                  self._outgoing_buffer,
                                  self._incoming_buffer,
                                  distance // 2)
        else:
            if forward:
                incoming_x, outgoing_x = width - distance, -distance
            else:
                incoming_x, outgoing_x = -width + distance, distance

            self.display.clear_buffers(0)
            self.display.gs4_fb.blit(self._outgoing, outgoing_x, 0)
            self.display.gs4_fb.blit(self._incoming, incoming_x, 0)
        self._present_composed()
