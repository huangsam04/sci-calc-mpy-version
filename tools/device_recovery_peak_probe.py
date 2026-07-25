# Late-failure recovery peak probe.
# Measures the heap cost of entering the internal recovery UI while the
# resident application still owns its display and framebuffer.
# Read-only: no filesystem writes; a reset afterwards restores the app.
import gc
import sys


def run(emit=print):
    gc.collect()
    emit("RECOVERY_PEAK_BEFORE " + str(gc.mem_free()))
    emit("RECOVERY_PEAK_MODULES_BEFORE " + str(len(sys.modules)))
    try:
        from recovery import show_recovery
    except ImportError as error:
        emit("RECOVERY_PEAK_IMPORT_FAILED " + str(error))
        return
    gc.collect()
    emit("RECOVERY_PEAK_IMPORTED " + str(gc.mem_free()))
    emit("RECOVERY_PEAK_MODULES_IMPORTED " + str(len(sys.modules)))
    try:
        show_recovery(RuntimeError("injected late failure"))
    except MemoryError:
        emit("RECOVERY_PEAK_MEMORY_ERROR free=" + str(gc.mem_free()))
        return
    except Exception as error:
        emit("RECOVERY_PEAK_ERROR "
             + type(error).__name__ + ":" + str(error))
        return
    gc.collect()
    emit("RECOVERY_PEAK_AFTER " + str(gc.mem_free()))


if __name__ == "__main__":
    run()
