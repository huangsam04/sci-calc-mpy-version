"""Owner for the calculator's one fixed Plot sample workspace."""
import gc

from ui.theme import CONTENT_W

PLOT_GRAPH_PAD_X = 2
PLOT_SAMPLE_STEP = 2


def plot_curve_buffer_size(_display_height):
    # Plot evaluates every second horizontal pixel.  Retain one byte-sized y
    # coordinate per sample and draw those line segments directly into the
    # one display framebuffer.  The prior full 1-bit raster needed 1,404 B;
    # this fixed coordinate workspace needs 104 B on the 210-pixel panel.
    graph_columns = max(1, CONTENT_W - PLOT_GRAPH_PAD_X * 2 + 1)
    return ((graph_columns + PLOT_SAMPLE_STEP - 1)
            // PLOT_SAMPLE_STEP)


class MemoryManager:
    __slots__ = ("_gc", "_plot_curve")

    def __init__(self, gc_module=None):
        self._gc = gc if gc_module is None else gc_module
        # Reserve the only rebuildable workspace before page imports fragment
        # the ESP32 heap.  Plot clears and reuses these y coordinates; leaving
        # the fixed 104-byte block resident is cheaper and safer than keeping
        # a generic buffer dictionary and reallocating at maximum user state.
        self._plot_curve = bytearray(plot_curve_buffer_size(64))

    def collect(self):
        collector = getattr(self._gc, "collect", None)
        if collector is not None:
            collector()

    def get_plot_workspace(self, minimum_size=0):
        value = self._plot_curve
        if len(value) < minimum_size:
            return None
        return value

    def reserve_plot_workspace(self, display_height):
        return self.get_plot_workspace(plot_curve_buffer_size(display_height))

    def release_plot_workspace(self):
        # The block is an early-reserved scalar resource, not a page cache.
        # Callers still drop every reference held by Plot itself.
        return False
