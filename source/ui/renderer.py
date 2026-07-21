"""Single composition and presentation path for application frames."""
from framebuf import FrameBuffer, GS4_HMSB  # type: ignore

from ui.theme import CONTENT_W


class Renderer:
    """Compose page content and fixed chrome, then present exactly once."""

    def __init__(self, display, sidebar):
        self.display = display
        self.sidebar = sidebar
        buffer_length = ((CONTENT_W + 1) // 2) * display.height
        self._outgoing_buffer = bytearray(buffer_length)
        self._incoming_buffer = bytearray(buffer_length)
        self._outgoing = FrameBuffer(self._outgoing_buffer, CONTENT_W,
                                     display.height, GS4_HMSB)
        self._incoming = FrameBuffer(self._incoming_buffer, CONTENT_W,
                                     display.height, GS4_HMSB)

    def _capture(self, screen, target):
        self.display.clear_buffers(0)
        screen.draw(self.display)
        target.fill(0)
        target.blit(self.display.gs4_fb, 0, 0)

    def capture_transition(self, outgoing, incoming):
        """Capture both content-only page layers into reusable buffers."""
        self._capture(outgoing, self._outgoing)
        self._capture(incoming, self._incoming)

    def _present_composed(self):
        # Sidebar.draw first erases the entire non-content region. This also
        # clips page layers that extended into it during a slide.
        self.sidebar.draw(self.display)
        self.display.present()

    def present(self, screen):
        """Present one canonical live page frame."""
        self.display.clear_buffers(0)
        screen.draw(self.display)
        self._present_composed()

    def present_transition(self, eased_progress, forward):
        """Present one composited non-linear slide frame."""
        width = CONTENT_W
        distance = int(width * eased_progress)
        if forward:
            incoming_x, outgoing_x = width - distance, -distance
        else:
            incoming_x, outgoing_x = -width + distance, distance

        self.display.clear_buffers(0)
        self.display.gs4_fb.blit(self._outgoing, outgoing_x, 0)
        self.display.gs4_fb.blit(self._incoming, incoming_x, 0)
        self._present_composed()
