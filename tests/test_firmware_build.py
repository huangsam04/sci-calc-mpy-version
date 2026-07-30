from pathlib import Path

from tools.release_plan import is_frozen_module


ROOT = Path(__file__).parents[1]


def test_base_manifest_contains_no_application_modules():
    source = (ROOT / "firmware" / "manifest-base.py").read_text()
    assert 'freeze("$(PORT_DIR)/modules")' in source
    assert "mp_version/source" not in source


def test_frozen_manifest_selects_production_modules_only():
    source = (ROOT / "firmware" / "manifest-frozen.py").read_text()
    for module in (
        '"calc/number.py"',
        '"display"',
        '"input"',
        '"screens/calculator.py"',
        '"screens/plot.py"',
        '"functions/basic.py"',
        '"functions/solve.py"',
        '"functions/trig.py"',
        '"ui"',
        '"utils"',
    ):
        assert module in source
    assert "plugin_fixture.py" not in source
    assert "scenario_variables.py" not in source
    assert 'freeze(_SOURCE, "functions")' not in source
    assert "_acceptance_" not in source
    assert "_scenario.py" not in source
    assert "device_" not in source


def test_frozen_manifest_does_not_mix_directories_into_a_file_list():
    calls = []

    def freeze(path, selection=None):
        calls.append((path, selection))

    namespace = {"freeze": freeze, "include": lambda _path: None}
    manifest = ROOT / "firmware" / "manifest-frozen.py"
    exec(compile(manifest.read_text(), str(manifest), "exec"), namespace)
    assert (manifest.parent / namespace["_SOURCE"]).resolve() == ROOT / "source"

    application_calls = [selection for path, selection in calls
                         if path == namespace["_SOURCE"]]
    manifest_paths = set()
    for selection in application_calls:
        if isinstance(selection, tuple):
            assert all(item.endswith(".py") for item in selection)
            manifest_paths.update(selection)
        else:
            package_root = ROOT / "source" / selection
            manifest_paths.update(
                path.relative_to(ROOT / "source").as_posix()
                for path in package_root.rglob("*.py")
            )
    for package in ("display", "input", "ui", "utils"):
        assert package in application_calls
    release_paths = {
        path.relative_to(ROOT / "source").as_posix()
        for path in (ROOT / "source").rglob("*.py")
        if is_frozen_module(path.relative_to(ROOT / "source").as_posix())
    }
    assert release_paths == manifest_paths


def test_firmware_builder_only_builds_and_checks_the_factory_application():
    source = (ROOT / "tools" / "build_firmware.ps1").read_text()
    assert 'ValidateSet("base", "frozen")' in source
    assert "MICROPY_MPYCROSS" in source
    assert "PYTHONUTF8=1" in source
    assert "PYTHONIOENCODING=utf-8" in source
    assert "[System.Security.Cryptography.SHA256]::Create()" in source
    assert "Get-FileHash" not in source
    assert '"frozen_content.c"' in source
    assert "LastWriteTimeUtc" in source
    assert 'Join-Path $WorkRoot ("firmware\\" + $Profile)' in source
    assert "COMPONENTS=main" in source
    assert "firmware_qstr_wrapper.py" in source
    assert "Set-QstrCommandAdapter" in source
    assert "makeqstrdefs.py pp" in source
    assert '"usr\\bin"' in source
    assert '"--pipeline"' in source
    assert "qstrdefs.preprocessed.h" in source
    for command in ("touch.exe", "sed.exe", "cat.exe"):
        assert command in source
    assert "0x1F0000" in source
    assert "micropython.bin" in source
    assert "erase-flash" not in source
    assert "write-flash" not in source
    assert "firmware.bin" not in source


def test_firmware_flasher_writes_only_the_factory_application_partition():
    source = (ROOT / "tools" / "flash_firmware.ps1").read_text()
    assert '[string]$Port' in source
    assert 'ValidateSet("frozen")' in source
    assert "micropython.bin" in source
    assert '"0x10000"' in source
    assert "write_flash" in source
    for forbidden in (
        "erase-flash",
        "erase_flash",
        "erase-region",
        "erase_region",
        "partition-table.bin",
        "bootloader.bin",
        '"0x1000"',
        '"0x8000"',
        "format",
    ):
        assert forbidden not in source.lower()
