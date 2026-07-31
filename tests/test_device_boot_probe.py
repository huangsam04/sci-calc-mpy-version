import pathlib
import runpy
import sys
from types import SimpleNamespace

import pytest

from version import VERSION


TOOLS = pathlib.Path(__file__).parents[1] / "tools"


class BindingStub:
    def __init__(self, buffer_size=8192, at_root=True, root_visible=True,
                 workspace_size=104):
        root = object()
        display = type("Display", (), {"gs4_buf": bytearray(buffer_size)})()
        renderer = type("Renderer", (), {})()
        renderer.display = display
        renderer._visible_screen = root if root_visible else object()
        nav = type("Nav", (), {})()
        nav.current = root if at_root else object()
        nav.renderer = renderer
        nav.memory = type(
            "Memory", (), {
                "_plot_curve": bytearray(workspace_size)
                if workspace_size else None})()
        self._binding_state = (nav, root, object(), {}, object())



def test_release_version_is_1_5_0():
    assert VERSION == "1.5.0"


def test_boot_probe_reports_version_root_and_buffer_contract():
    module = runpy.run_path(str(TOOLS / "device_boot_probe.py"))
    lines = []

    assert module["run"](BindingStub(), emit=lines.append) is None

    assert lines[:3] == [
        "BOOT_VERSION " + VERSION,
        "BOOT_RUNTIME_READY True",
        "BOOT_ROOT_VISIBLE True",
    ]
    assert lines[3].startswith("BOOT_BUFFERS main:8192:")
    assert lines[4].startswith("BOOT_WORKSPACE plot:104:")
    assert lines[5:7] == ["BOOT_MODE source", "BOOT_ABI_VIPER ok"]
    assert [line.split(":", 1)[0] for line in lines[7:]] == [
        "BOOT_MODULE main",
        "BOOT_MODULE performance",
        "BOOT_MODULE runtime_handle",
        "BOOT_MODULE version",
        "BOOT_MODULE approot",
    ]


def test_boot_probe_reports_mpy_mode_from_dynamic_main_when_version_is_frozen(
        monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "main",
        SimpleNamespace(__file__="/sd/.slots/B/main.mpy"),
    )
    monkeypatch.setitem(
        sys.modules,
        "version",
        SimpleNamespace(VERSION=VERSION, __file__="version.py"),
    )
    module = runpy.run_path(str(TOOLS / "device_boot_probe.py"))
    lines = []

    module["run"](BindingStub(), emit=lines.append)

    assert "BOOT_MODE mpy" in lines


def test_boot_probe_reads_the_published_binding_without_materializing_runtime():
    source = (TOOLS / "device_boot_probe.py").read_text(encoding="utf-8")

    assert "from runtime_handle import get_resident_runtime" in source
    assert "runtime_materialize" not in source
    assert "runtime_acceptance" not in source
    assert "report =" not in source


@pytest.mark.parametrize(
    ("buffer_size", "workspace_size"),
    ((4096, 104), (8192, 103)),
)
def test_boot_probe_rejects_an_invalid_framebuffer_contract(
        buffer_size, workspace_size):
    module = runpy.run_path(str(TOOLS / "device_boot_probe.py"))

    with pytest.raises(RuntimeError, match="framebuffer contract"):
        module["run"](BindingStub(buffer_size, workspace_size=workspace_size))


@pytest.mark.parametrize(
    ("at_root", "root_visible"),
    ((False, True), (True, False), (False, False)),
)
def test_boot_probe_rejects_a_runtime_without_a_visible_root(
        at_root, root_visible):
    module = runpy.run_path(str(TOOLS / "device_boot_probe.py"))

    with pytest.raises(RuntimeError, match="root UI"):
        module["run"](
            BindingStub(at_root=at_root, root_visible=root_visible))


def test_boot_probe_rejects_a_nonbinding_runtime():
    module = runpy.run_path(str(TOOLS / "device_boot_probe.py"))

    with pytest.raises(RuntimeError, match="resident runtime"):
        module["run"](object())
