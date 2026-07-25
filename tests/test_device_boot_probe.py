import pathlib
import runpy

import pytest

from version import VERSION


TOOLS = pathlib.Path(__file__).parents[1] / "tools"


class RuntimeHandleStub:
    mode = "resident"
    version = VERSION

    def __init__(self, buffers=(("main", 8192, 12345),), at_root=True,
                 root_visible=True, version=VERSION):
        self._buffers = buffers
        self._at_root = at_root
        self._root_visible = root_visible
        self.version = version

    def at_root(self):
        return self._at_root

    def root_visible(self):
        return self._root_visible

    def buffer_snapshot(self):
        return self._buffers


def test_boot_probe_reports_version_root_and_buffer_contract():
    module = runpy.run_path(str(TOOLS / "device_boot_probe.py"))
    lines = []

    report = module["run"](RuntimeHandleStub(), emit=lines.append)

    assert report == {
        "version": VERSION,
        "runtime_ready": True,
        "root_visible": True,
        "buffers": (("main", 8192, 12345),),
        "build_mode": "source",
        "viper_ok": True,
    }
    assert lines == [
        "BOOT_VERSION " + VERSION,
        "BOOT_RUNTIME_READY True",
        "BOOT_ROOT_VISIBLE True",
        "BOOT_BUFFERS main:8192:12345",
        "BOOT_MODE source",
        "BOOT_ABI_VIPER ok",
    ]


def test_boot_probe_imports_the_lightweight_runtime_handle_only():
    source = (TOOLS / "device_boot_probe.py").read_text(encoding="utf-8")

    assert "from runtime_handle import get_resident_runtime" in source
    assert "from runtime_acceptance import" not in source


@pytest.mark.parametrize(
    "buffers",
    (
        (),
        (("main", 4096, 12345),),
        (("other", 8192, 12345),),
        (("main", 8192, 0),),
        (("main", 8192, "not-an-identity"),),
        (("main", 8192, 12345), ("main", 8192, 12345)),
        (("main", 8192, 12345), ("plot_curve", 1404, 67890)),
    ),
)
def test_boot_probe_rejects_an_invalid_root_framebuffer_contract(buffers):
    module = runpy.run_path(str(TOOLS / "device_boot_probe.py"))
    lines = []

    with pytest.raises(RuntimeError, match="framebuffer contract"):
        module["run"](RuntimeHandleStub(buffers=buffers), emit=lines.append)

    assert lines == []


@pytest.mark.parametrize("runtime_version", (None, "", "0.0.0-stale"))
def test_boot_probe_rejects_a_missing_or_mismatched_runtime_version(
        runtime_version):
    module = runpy.run_path(str(TOOLS / "device_boot_probe.py"))
    lines = []

    with pytest.raises(RuntimeError, match="device version"):
        module["run"](
            RuntimeHandleStub(version=runtime_version),
            emit=lines.append,
        )

    assert lines == []


@pytest.mark.parametrize(
    ("at_root", "root_visible"),
    ((False, True), (True, False), (False, False)),
)
def test_boot_probe_rejects_a_runtime_without_a_visible_root(
        at_root, root_visible):
    module = runpy.run_path(str(TOOLS / "device_boot_probe.py"))
    lines = []

    with pytest.raises(RuntimeError, match="root UI"):
        module["run"](
            RuntimeHandleStub(
                at_root=at_root,
                root_visible=root_visible,
            ),
            emit=lines.append,
        )

    assert lines == []
