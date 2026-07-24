"""Owner for the calculator's single optional plot workspace."""
import gc

from ui.theme import CONTENT_W

PLOT_GRAPH_PAD_X = 2
PLOT_HINT_H = 10


def plot_curve_buffer_size(display_height):
    graph_width = max(1, CONTENT_W - PLOT_GRAPH_PAD_X * 2)
    graph_height = max(1, int(display_height) - PLOT_HINT_H)
    return ((graph_width + 7) // 8) * graph_height


class MemoryManager:
    __slots__ = ("_gc", "_buffers")

    def __init__(self, gc_module=None):
        self._gc = gc if gc_module is None else gc_module
        self._buffers = {}

    def collect(self):
        collector = getattr(self._gc, "collect", None)
        if collector is not None:
            collector()

    def reserve_buffer(self, name, size, allocator=bytearray,
                       retry_collect=True):
        existing = self._buffers.get(name)
        if existing is not None:
            return existing if len(existing) >= size else None
        try:
            value = allocator(max(1, int(size)))
        except MemoryError:
            if not retry_collect:
                return None
            self.collect()
            try:
                value = allocator(max(1, int(size)))
            except MemoryError:
                return None
        self._buffers[name] = value
        return value

    def get_buffer(self, name, minimum_size=0):
        value = self._buffers.get(name)
        if value is None or len(value) < minimum_size:
            return None
        return value

    def release_buffer(self, name):
        if name not in self._buffers:
            return False
        del self._buffers[name]
        return True

    def reserve_plot_workspace(self, display_height):
        return self.reserve_buffer(
            "plot_curve", plot_curve_buffer_size(display_height))

    def release_plot_workspace(self):
        return self.release_buffer("plot_curve")
