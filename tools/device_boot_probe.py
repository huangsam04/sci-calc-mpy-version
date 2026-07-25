"""Versioned resident-runtime boot probe for release acceptance."""
import micropython
import sys


if "/sd" not in sys.path:
    sys.path.insert(0, "/sd")


try:
    @micropython.viper
    def _viper_identity(value: int) -> int:
        return value
except AttributeError:
    # CPython hosts run the probe for behaviour tests; viper is device-only.
    def _viper_identity(value):
        return value


def _resident_runtime():
    from runtime_handle import get_resident_runtime

    return get_resident_runtime()


def _buffer_text(buffers):
    if not buffers:
        return "-"
    return ",".join(
        name + ":" + str(length) + ":" + str(identity)
        for name, length, identity in buffers)


def _validate_boot_buffers(buffers):
    if not isinstance(buffers, tuple) or len(buffers) != 1:
        raise RuntimeError("SCI-CALC boot framebuffer contract is invalid")
    item = buffers[0]
    if not isinstance(item, tuple) or len(item) != 3:
        raise RuntimeError("SCI-CALC boot framebuffer contract is invalid")
    name, length, identity = item
    if (name != "main" or length != 8192
            or type(identity) is not int or identity <= 0):
        raise RuntimeError("SCI-CALC boot framebuffer contract is invalid")


def run(runtime=None, emit=print):
    """Report the version, visible root, and existing buffer identities."""
    if runtime is None:
        runtime = _resident_runtime()
    if runtime is None or getattr(runtime, "mode", None) != "resident":
        raise RuntimeError("SCI-CALC resident runtime is unavailable")

    from version import VERSION
    import version as _version_module

    version = getattr(runtime, "version", None)
    if not version or version != VERSION:
        raise RuntimeError("SCI-CALC resident runtime does not match device version")
    root_visible = bool(runtime.at_root() and runtime.root_visible())
    if not root_visible:
        raise RuntimeError("SCI-CALC root UI is not visible")
    buffers = runtime.buffer_snapshot()
    _validate_boot_buffers(buffers)
    build_file = getattr(_version_module, "__file__", "") or ""
    build_mode = "mpy" if build_file.endswith(".mpy") else "source"
    viper_ok = _viper_identity(41) == 41
    report = {
        "version": version,
        "runtime_ready": True,
        "root_visible": root_visible,
        "buffers": buffers,
        "build_mode": build_mode,
        "viper_ok": viper_ok,
    }

    emit("BOOT_VERSION " + version)
    emit("BOOT_RUNTIME_READY True")
    emit("BOOT_ROOT_VISIBLE " + str(root_visible))
    emit("BOOT_BUFFERS " + _buffer_text(buffers))
    emit("BOOT_MODE " + build_mode)
    emit("BOOT_ABI_VIPER " + ("ok" if viper_ok else "failed"))
    return report


if __name__ == "__main__":
    run()
