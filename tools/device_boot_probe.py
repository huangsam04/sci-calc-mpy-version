"""Minimal resident-binding boot evidence for release acceptance."""
import micropython


try:
    @micropython.viper
    def _viper_identity(value: int) -> int:
        return value
except AttributeError:
    def _viper_identity(value):
        return value


def _resident_binding():
    from runtime_handle import get_resident_runtime
    return get_resident_runtime()


def run(runtime=None, emit=print):
    if runtime is None:
        runtime = _resident_binding()
    state = getattr(runtime, "_binding_state", None)
    if (not isinstance(state, tuple) or len(state) != 5
            or not isinstance(state[0], tuple) or len(state[0]) != 10):
        raise RuntimeError("SCI-CALC resident runtime is unavailable")

    screens = state[0]
    nav = state[4]
    root = screens[0]
    renderer = getattr(nav, "renderer", None)
    if (nav is None or getattr(nav, "current", None) is not root
            or getattr(renderer, "_visible_screen", None) is not root):
        raise RuntimeError("SCI-CALC root UI is not visible")
    display = getattr(renderer, "display", None)
    buffer = getattr(display, "gs4_buf", None)
    memory = getattr(nav, "memory", None)
    workspace = getattr(memory, "_plot_curve", None)
    if (buffer is None or len(buffer) != 8192
            or workspace is None or len(workspace) != 104):
        raise RuntimeError("SCI-CALC boot framebuffer contract is invalid")

    from version import VERSION
    import version as version_module
    build_file = getattr(version_module, "__file__", "") or ""
    build_mode = "mpy" if build_file.endswith(".mpy") else "source"
    viper_ok = _viper_identity(41) == 41
    emit("BOOT_VERSION " + VERSION)
    emit("BOOT_RUNTIME_READY True")
    emit("BOOT_ROOT_VISIBLE True")
    emit("BOOT_BUFFERS main:8192:" + str(id(buffer)))
    emit("BOOT_WORKSPACE plot:104:" + str(id(workspace)))
    emit("BOOT_MODE " + build_mode)
    emit("BOOT_ABI_VIPER " + ("ok" if viper_ok else "failed"))


if __name__ == "__main__":
    run()
