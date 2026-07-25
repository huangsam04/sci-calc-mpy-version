# Integration test for the deployment sequence over the device twin.
import hashlib

import bootsel
from tools import release_deploy
from tools.release_plan import ReleaseTreeSnapshot, plan_release

from test_release_device_mpremote import _DeviceTwin, _smoke_lines


BASELINE_130 = {
    "boot.py": b"# 1.3.0 boot\n",
    "main.py": b"# 1.3.0 internal main\n",
    "sdcard.py": b"# 1.3.0 sdcard driver\n",
    "recovery.py": b"# 1.3.0 recovery\n",
    "display/mono_palette.py": b"# 1.3.0 palette\n",
    "display/ssd1322.py": b"# 1.3.0 display driver\n",
}


def _project(tmp_path):
    source = tmp_path / "source"
    (source / "display").mkdir(parents=True)
    (source / "fonts").mkdir()
    files = {
        "boot.py": b"# new boot chain\n",
        "internal_main.py": b"# new supervisor shim\n",
        "sdcard.py": BASELINE_130["sdcard.py"],
        "bootsel.py": b"# codec\n",
        "bootlog.py": b"# boot log\n",
        "bootsupervisor.py": b"# supervisor\n",
        "bootenv.py": b"# environment\n",
        "recovery.py": BASELINE_130["recovery.py"],
        "display/mono_palette.py": BASELINE_130["display/mono_palette.py"],
        "display/ssd1322.py": BASELINE_130["display/ssd1322.py"],
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
            ("Bally7x9", 7, 9), ("Neato5x7", 5, 7), ("FixedFont5x8", 5, 8)):
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


def _baseline_hashes():
    return {
        path: hashlib.sha256(payload).hexdigest()
        for path, payload in BASELINE_130.items()
    }


def test_deploy_sequence_adopts_then_applies_end_to_end(tmp_path):
    project_root, files = _project(tmp_path / "project")
    twin_root = tmp_path / "device"
    twin_root.mkdir()
    twin = _DeviceTwin(twin_root, lambda boot: _smoke_lines("1.4.0"))
    for path, payload in BASELINE_130.items():
        twin.write_file("/" + path, payload)
    probe_source = "probe-source"
    twin._probe_source_text = probe_source

    lines = []
    from tools.release_device_mpremote import MpremoteReleaseAdapter
    release_id = release_deploy.run(
        "TWIN",
        "source",
        _compiler([]),
        lambda: twin,
        project_root=project_root,
        baseline_hashes=_baseline_hashes(),
        adapter_factory=lambda factory: MpremoteReleaseAdapter(
            factory, probe_source=probe_source, boot_wait_s=0),
        boot_wait_s=0,
        emit=lines.append,
    )

    assert "RELEASE_ADOPTION applied" in lines
    assert any(line.startswith("RELEASE_APPLIED ") for line in lines)
    assert twin.read_file("/boot.py") == files["boot.py"]
    assert twin.read_file("/main.py") == files["internal_main.py"]

    store = bootsel.SelectorStore(
        str(twin_root / "sys" / "sel.0"), str(twin_root / "sys" / "sel.1"))
    selector = store.read()
    assert selector.confirmed.release_id == release_id
    assert selector.retired == ()
    assert selector.confirmation_pending is False
    manifest_path = (
        twin_root / "sd" / ".slots" / "A" / "release.manifest")
    assert manifest_path.is_file()
    assert twin.resets >= 4
    assert twin.closes >= 4
