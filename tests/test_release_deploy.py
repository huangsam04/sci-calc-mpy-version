from dataclasses import replace

import bootenv
import bootsel
import pytest

from tools import release_deploy
from test_release_device_mpremote import _DeviceTwin, _smoke_lines


def _project(tmp_path):
    source = tmp_path / "source"
    (source / "display").mkdir(parents=True)
    (source / "fonts").mkdir()
    files = {
        "boot.py": b"# current boot chain\n",
        "internal_main.py": b"# current supervisor shim\n",
        "sdcard.py": b"# current sdcard driver\n",
        "bootsel.py": b"# selector codec\n",
        "bootlog.py": b"# boot log\n",
        "bootsupervisor.py": b"# supervisor\n",
        "bootenv.py": b"# environment\n",
        "recovery.py": b"# recovery\n",
        "display/mono_palette.py": b"# palette\n",
        "display/ssd1322.py": b"# display driver\n",
        "main.py": b"# app main\n",
        "version.py": b'VERSION = "1.4.0"\n',
        "settings.json": b"{}\n",
        "vars.json": b"{}\n",
    }
    for path, payload in files.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    from test_release_build import _fake_font_c
    for name, width, height in (
            ("Bally7x9", 7, 9), ("Neato5x7", 5, 7),
            ("FixedFont5x8", 5, 8)):
        (source / "fonts" / (name + ".c")).write_bytes(
            _fake_font_c(width, height))
    return tmp_path, files


def _compiler(calls):
    def compile_module(source_path, output_path):
        from pathlib import Path
        calls.append(Path(source_path).name)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"MPY" + Path(source_path).name.encode())
    return compile_module


def test_release_compiler_strips_unused_source_line_tables(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(
        release_deploy.subprocess, "run",
        lambda command, capture_output: calls.append(command) or Result())

    release_deploy._mpy_cross_compiler("mpy-cross")("input.py", "output.mpy")

    assert calls == [(
        "mpy-cross", "-march=xtensawin", "-X", "no-source-lines",
        "-s", "input.py", "-o", "output.mpy", "input.py",
    )]


def test_run_uses_fast_release_by_default(monkeypatch, tmp_path):
    project_root, _files = _project(tmp_path / "project")
    applied = []
    lines = []

    def fast_apply(plan, adapter):
        applied.append((plan, adapter))
        return plan.release_id

    monkeypatch.setattr(release_deploy, "apply_fast_release", fast_apply)
    monkeypatch.setattr(
        release_deploy, "apply_release",
        lambda plan, adapter: pytest.fail("transactional release was used"))

    release_id = release_deploy.run(
        "TWIN",
        "source",
        _compiler([]),
        lambda: pytest.fail("unexpected direct device contact"),
        project_root=project_root,
        adapter_factory=lambda factory: ("adapter", factory),
        boot_wait_s=0,
        emit=lines.append,
    )

    assert release_id == applied[0][0].release_id
    assert applied[0][1][0] == "adapter"
    assert lines == [
        "RELEASE_ADMISSION_PLAN " + release_id,
        "RELEASE_ADMISSION_MANIFEST_SHA256 "
        + applied[0][0].manifest_sha256,
        "RELEASE_APPLIED " + release_id,
    ]


def test_run_uses_transactional_release_only_when_requested(
        monkeypatch, tmp_path):
    project_root, _files = _project(tmp_path / "project")
    applied = []

    def transactional_apply(plan, adapter):
        applied.append((plan, adapter))
        return plan.release_id

    monkeypatch.setattr(release_deploy, "apply_release", transactional_apply)
    monkeypatch.setattr(
        release_deploy, "apply_fast_release",
        lambda plan, adapter: pytest.fail("fast release was used"))

    release_id = release_deploy.run(
        "TWIN",
        "source",
        _compiler([]),
        lambda: pytest.fail("unexpected direct device contact"),
        project_root=project_root,
        adapter_factory=lambda factory: ("adapter", factory),
        boot_wait_s=0,
        emit=lambda _line: None,
        transactional=True,
    )

    assert release_id == applied[0][0].release_id
    assert applied[0][1][0] == "adapter"


def test_run_forwards_boot_wait_to_the_default_release_adapter(
        monkeypatch, tmp_path):
    project_root, _files = _project(tmp_path / "project")
    adapters = []

    def make_adapter(factory, boot_wait_s):
        adapter = (factory, boot_wait_s)
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(
        release_deploy.release_device_mpremote,
        "MpremoteReleaseAdapter", make_adapter)
    monkeypatch.setattr(
        release_deploy, "apply_fast_release",
        lambda plan, adapter: plan.release_id)

    release_deploy.run(
        "TWIN",
        "source",
        _compiler([]),
        lambda: None,
        project_root=project_root,
        boot_wait_s=3,
        emit=lambda _line: None,
    )

    assert len(adapters) == 1
    assert adapters[0][1] == 3


def test_invalid_plan_fails_before_device_contact(monkeypatch, tmp_path):
    project_root, _files = _project(tmp_path / "project")
    plans = release_deploy.release_build.prepare_release_plans(
        project_root, _compiler([]))
    invalid_plan = replace(plans.source, manifest_sha256="0" * 64)
    monkeypatch.setattr(
        release_deploy.release_build,
        "prepare_release_plans",
        lambda *_args, **_kwargs: replace(plans, source=invalid_plan),
    )
    contacts = []

    def device_factory():
        contacts.append(True)
        raise AssertionError("invalid plan must not contact the device")

    with pytest.raises(ValueError, match="manifest"):
        release_deploy.run(
            "TWIN",
            "source",
            _compiler([]),
            device_factory,
            project_root=project_root,
            boot_wait_s=0,
            emit=lambda _line: None,
        )

    assert contacts == []


def test_transactional_mode_first_provisions_current_firmware_and_keeps_user_data(
        tmp_path):
    project_root, files = _project(tmp_path / "project")
    twin_root = tmp_path / "device"
    twin_root.mkdir()
    twin = _DeviceTwin(twin_root, lambda boot: _smoke_lines("1.4.0"))
    probe_source = "probe-source"
    twin._probe_source_text = probe_source
    bootstrap_paths = {
        "boot.py": "/boot.py",
        "internal_main.py": "/main.py",
        "sdcard.py": "/sdcard.py",
        "bootsel.py": "/bootsel.py",
        "bootlog.py": "/bootlog.py",
        "bootsupervisor.py": "/bootsupervisor.py",
        "bootenv.py": "/bootenv.py",
        "recovery.py": "/recovery.py",
        "display/mono_palette.py": "/display/mono_palette.py",
        "display/ssd1322.py": "/display/ssd1322.py",
    }
    for source_path, device_path in bootstrap_paths.items():
        twin.write_file(device_path, files[source_path])
    sentinels = {
        "/sd/settings.json": b'{"brightness":73}\n',
        "/sd/vars.json": b'{"answer":42}\n',
        "/sd/Add-ons/user_pack.py": b"# user add-on\n",
    }
    for path, payload in sentinels.items():
        twin.write_file(path, payload)

    from tools.release_device_mpremote import MpremoteReleaseAdapter
    release_id = release_deploy.run(
        "TWIN",
        "source",
        _compiler([]),
        lambda: twin,
        project_root=project_root,
        adapter_factory=lambda factory: MpremoteReleaseAdapter(
            factory, probe_source=probe_source, boot_wait_s=0),
        boot_wait_s=0,
        emit=lambda _line: None,
        transactional=True,
    )

    store = bootsel.SelectorStore(
        str(twin_root / "sys" / "sel.0"), str(twin_root / "sys" / "sel.1"))
    selector = store.read()
    assert selector.confirmed.release_id == release_id
    assert selector.confirmed.name == "A"
    assert selector.trial is None
    assert selector.retired == ()
    assert (twin_root / "sd" / ".slots" / "A"
            / bootenv.MANIFEST_NAME).is_file()
    for path, payload in sentinels.items():
        assert twin.read_file(path) == payload
