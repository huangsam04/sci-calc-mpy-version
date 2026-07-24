"""Predictable fixed buffers and reclaimable page-cache lifecycle."""
import gc

from ui.theme import CONTENT_W


PLOT_GRAPH_PAD_X = 2
PLOT_HINT_H = 10


def plot_curve_buffer_size(display_height):
    """Return the fixed 1-bit workspace needed by the plot content area."""
    graph_width = max(1, CONTENT_W - PLOT_GRAPH_PAD_X * 2)
    graph_height = max(1, int(display_height) - PLOT_HINT_H)
    return ((graph_width + 7) // 8) * graph_height


class MemoryManager:
    """Own long-lived buffers and reclaim only rebuildable inactive state.

    The public interface is deliberately small: callers reserve named fixed
    buffers before the UI fragments the heap, register page objects once, and
    ask the manager to reclaim inactive pages before activating another one.
    Persistent user data is owned by screens and must not be released here.
    """

    def __init__(self, gc_module=None):
        self._gc = gc if gc_module is None else gc_module
        self._buffers = {}
        self._failed_buffers = {}
        self._screens = ()
        self._fonts = ()

    def collect(self):
        collector = getattr(self._gc, "collect", None)
        if collector is not None:
            collector()

    def has_headroom(self, minimum_free):
        """Return whether the runtime reports enough free heap for a phase.

        CPython does not expose MicroPython's ``mem_free`` and is treated as
        unconstrained so host behavior tests keep exercising the real paths.
        """
        reporter = getattr(self._gc, "mem_free", None)
        return reporter is None or reporter() >= max(0, int(minimum_free))

    def reserve_buffer(self, name, size, allocator=bytearray,
                       retry_collect=True):
        """Allocate a named fixed buffer once, with one post-GC retry."""
        size = max(1, int(size))
        existing = self._buffers.get(name)
        if existing is not None:
            return existing if len(existing) >= size else None
        if name in self._failed_buffers:
            return None

        try:
            buffer = allocator(size)
        except MemoryError:
            if not retry_collect:
                self._failed_buffers[name] = True
                return None
            self.collect()
            try:
                buffer = allocator(size)
            except MemoryError:
                self._failed_buffers[name] = True
                return None

        self._buffers[name] = buffer
        return buffer

    def get_buffer(self, name, minimum_size=0):
        """Return a preplanned buffer only when it can satisfy the request."""
        buffer = self._buffers.get(name)
        if buffer is None or len(buffer) < minimum_size:
            return None
        return buffer

    def release_buffer(self, name):
        """Drop a named optional buffer and allow a later explicit retry."""
        released = False
        if name in self._buffers:
            del self._buffers[name]
            released = True
        # A failed optional allocation is only terminal for the current
        # resource phase.  Once a caller has released a competing buffer,
        # fragmentation can genuinely be different, so let the next explicit
        # phase try again instead of permanently poisoning this name.
        if name in self._failed_buffers:
            del self._failed_buffers[name]
            released = True
        return released

    def reserve_plot_workspace(self, display_height):
        return self.reserve_buffer("plot_curve",
                                   plot_curve_buffer_size(display_height))

    def release_plot_workspace(self):
        """Release the graph bitmap after its screen dropped its wrapper."""
        return self.release_buffer("plot_curve")

    def handoff_plot_workspace(self):
        """Reuse the released graph bitmap as the next transition strip."""
        buffer = self._buffers.get("plot_curve")
        if buffer is None:
            return False
        self.release_buffer("transition_strip")
        del self._buffers["plot_curve"]
        self._buffers["transition_strip"] = buffer
        return True

    def register_screens(self, screens):
        self._screens = tuple(screens)

    def register_fonts(self, fonts):
        self._fonts = tuple(font for font in fonts if font is not None)

    def release_font_caches(self):
        """Drop shared glyph bitmaps between page residency phases."""
        released = False
        for font in self._fonts:
            cache = getattr(font, "_cache", None)
            if cache:
                cache.clear()
                released = True
        return released

    def reclaim_for(self, incoming, aggressive=False, exclude=(),
                    collect=True):
        """Release rebuildable caches held by every inactive page.

        ``incoming`` is deliberately kept intact.  A collection runs only
        after a page actually released something, keeping ordinary navigation
        smooth while still coalescing memory before a new page is activated.
        """
        released = False
        for screen in self._screens:
            if screen is incoming or screen in exclude:
                continue
            releaser = getattr(screen, "release_memory", None)
            if releaser is not None and releaser():
                released = True

        if aggressive:
            released = self.release_font_caches() or released

        if released and collect:
            self.collect()
        return released
