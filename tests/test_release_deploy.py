from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import release_deploy


def _project(tmp_path):
    project = tmp_path / "mp_version"
    (project / "tools").mkdir(parents=True)
    for name in ("build_firmware.ps1", "flash_firmware.ps1"):
        (project / "tools" / name).write_text("", encoding="ascii")
    return project


def test_builds_then_flashes_only_the_product_firmware(tmp_path):
    project = _project(tmp_path)
    calls = []

    def runner(command, cwd, check):
        calls.append((command, cwd, check))
        if command[5].endswith("build_firmware.ps1"):
            image = project / ".work/firmware/product/micropython.bin"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"firmware")
        return SimpleNamespace(returncode=0)

    lines = []
    image = release_deploy.run(
        "COM5", project_root=project, runner=runner,
        powershell="pwsh", emit=lines.append)

    assert image == project / ".work/firmware/product/micropython.bin"
    assert [Path(call[0][5]).name for call in calls] == [
        "build_firmware.ps1", "flash_firmware.ps1"]
    assert calls[1][0][-4:] == ["-Port", "COM5", "-Baud", "921600"]
    assert all(call[1] == project and call[2] is False for call in calls)
    assert lines[-1].startswith("RELEASE_APPLIED port=COM5 image=")


def test_skip_build_flashes_an_existing_image(tmp_path):
    project = _project(tmp_path)
    image = project / ".work/firmware/product/micropython.bin"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"firmware")
    calls = []

    def runner(command, cwd, check):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    release_deploy.run(
        "COM7", build=False, baud=460800, project_root=project,
        runner=runner, powershell="pwsh", emit=lambda _line: None)

    assert len(calls) == 1
    assert Path(calls[0][5]).name == "flash_firmware.ps1"
    assert calls[0][-4:] == ["-Port", "COM7", "-Baud", "460800"]


def test_missing_product_image_stops_before_flash(tmp_path):
    project = _project(tmp_path)
    calls = []

    def runner(command, cwd, check):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    with pytest.raises(RuntimeError, match="Missing SCI-CALC product firmware"):
        release_deploy.run(
            "COM5", build=False, project_root=project, runner=runner,
            powershell="pwsh", emit=lambda _line: None)

    assert calls == []


def test_failed_build_does_not_flash(tmp_path):
    project = _project(tmp_path)
    calls = []

    def runner(command, cwd, check):
        calls.append(command)
        return SimpleNamespace(returncode=12)

    with pytest.raises(RuntimeError, match="build_firmware.ps1 failed"):
        release_deploy.run(
            "COM5", project_root=project, runner=runner,
            powershell="pwsh", emit=lambda _line: None)

    assert len(calls) == 1


def test_release_entry_has_no_sd_slot_or_transactional_mode():
    source = (release_deploy.PROJECT_ROOT / "tools/release_deploy.py").read_text(
        encoding="utf-8")

    for obsolete in (
        "--mode", "--transactional", ".slots", "release_plan",
        "release_apply", "release_device", "mpremote",
    ):
        assert obsolete not in source
    assert "--skip-build" in source
    assert "flash_firmware.ps1" in source
