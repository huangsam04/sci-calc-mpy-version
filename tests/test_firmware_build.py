from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_frozen_manifest_selects_production_modules_only():
    source = (ROOT / "firmware" / "manifest.py").read_text()
    for module in (
        'freeze(_FIRMWARE, "main.py")',
        '"application.py"',
        '"boot.py"',
        '"calc/number.py"',
        '"display"',
        '"input"',
        '"screens/calculator.py"',
        '"screens/plot.py"',
        '"functions/basic.py"',
        '"functions/solve.py"',
        '"functions/trig.py"',
        '"recovery.py"',
        '"sdcard.py"',
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


def test_frozen_main_is_only_a_bootstrap_for_the_renamed_product_entry():
    source = (ROOT / "firmware" / "main.py").read_text(encoding="utf-8")

    assert 'sys.path = [".frozen", "/lib"]' in source
    assert "from application import main" in source
    assert "main()" in source
    assert ".slots" not in source
    assert "launch.py" not in source


def test_frozen_manifest_does_not_mix_directories_into_a_file_list():
    calls = []

    def freeze(path, selection=None):
        calls.append((path, selection))

    namespace = {"freeze": freeze, "include": lambda _path: None}
    manifest = ROOT / "firmware" / "manifest.py"
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
    assert "application.py" in manifest_paths
    for obsolete in (
        "approot.py", "bootenv.py", "bootlog.py", "bootsel.py",
        "bootsupervisor.py", "internal_main.py", "launch.py",
    ):
        assert obsolete not in manifest_paths


def test_firmware_builder_locks_stable_upstream_and_product_output():
    source = (ROOT / "tools" / "build_firmware.ps1").read_text()
    assert "v1.28.0" in source
    assert "tooling\\mpy-cross-v1.28\\mpy-cross.exe" in source
    assert 'MpyVersion -notmatch "MicroPython v1\\.28\\.0"' in source
    assert 'MpyVersion -notmatch "mpy v6\\.3"' in source
    assert "e0e9fbb17ed6fd06bb76e266ae554784c9c80804" in source
    assert "6c48c290ce7e85916892549933ffea4daaedd331" in source
    assert 'Join-Path $ProjectRoot "firmware\\manifest.py"' in source
    assert "MICROPY_MPYCROSS" in source
    assert "PYTHONUTF8=1" in source
    assert "PYTHONIOENCODING=utf-8" in source
    assert "[System.Security.Cryptography.SHA256]::Create()" in source
    assert "Get-FileHash" not in source
    assert '"frozen_content.c"' in source
    assert "LastWriteTimeUtc" in source
    assert '(Get-Item -LiteralPath $MpyCross).LastWriteTimeUtc' in source
    assert 'Join-Path $WorkRoot "firmware\\product"' in source
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


def test_firmware_flasher_writes_only_the_product_application_partition():
    source = (ROOT / "tools" / "flash_firmware.ps1").read_text()
    assert '[string]$Port' in source
    assert 'ValidateSet("frozen")' not in source
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
