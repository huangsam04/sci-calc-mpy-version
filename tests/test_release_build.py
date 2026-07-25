# Host behaviour tests for the fresh release build Module.
from pathlib import Path

import pytest

from tools.build_fonts import bytes_per_letter
from tools.release_build import prepare_release_plans
from tools.release_plan import is_compiled_in_mpy


@pytest.mark.parametrize(("path", "expected"), (
    ("main.py", True),
    ("approot.py", True),
    ("calc/parser.py", True),
    ("display/ssd1322.py", True),
    ("boot.py", False),
    ("bootenv.py", False),
    ("bootlog.py", False),
    ("bootsel.py", False),
    ("bootsupervisor.py", False),
    ("internal_main.py", False),
    ("recovery.py", False),
    ("sdcard.py", False),
    ("launch.py", False),
    ("functions/basic.py", False),
    ("runtime_scenarios_host.py", False),
    ("settings.json", False),
    ("fonts/Bally7x9.c", False),
))
def test_compiled_module_predicate_mirrors_the_plan_rules(path, expected):
    assert is_compiled_in_mpy(path) is expected


def _fake_font_c(width, height):
    size = bytes_per_letter(width, height)
    line = ",".join("0x%02X" % value for value in range(size)) + ",\n"
    return (line * 96).encode("ascii")


def _project(tmp_path):
    source = tmp_path / "source"
    (source / "calc").mkdir(parents=True)
    (source / "functions").mkdir()
    (source / "fonts").mkdir()
    (source / "main.py").write_bytes(b"# main\n")
    (source / "version.py").write_bytes(b'VERSION = "1.4.0"\n')
    (source / "calc" / "parser.py").write_bytes(b"# parser\n")
    (source / "functions" / "basic.py").write_bytes(b"# pack\n")
    (source / "settings.json").write_bytes(b"{}\n")
    (source / "vars.json").write_bytes(b"{}\n")
    (source / "fonts" / "Bally7x9.c").write_bytes(_fake_font_c(7, 9))
    (source / "fonts" / "Neato5x7.c").write_bytes(_fake_font_c(5, 7))
    (source / "fonts" / "FixedFont5x8.c").write_bytes(_fake_font_c(5, 8))
    return tmp_path


def _recording_compiler(calls, skip=(), fail_at=None):
    def compile_module(source_path, output_path):
        calls.append(source_path)
        if fail_at is not None and Path(source_path) == Path(fail_at):
            raise RuntimeError("compiler exploded")
        if any(Path(source_path) == Path(item) for item in skip):
            return
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(
            b"MPY" + Path(source_path).name.encode())
    return compile_module


def test_prepare_builds_fonts_compiles_exactly_the_mpy_set(tmp_path):
    project = _project(tmp_path)
    calls = []

    plans = prepare_release_plans(project, _recording_compiler(calls))

    compiled_names = sorted(
        str(path).replace("\\", "/").split("/")[-1] for path in calls)
    assert compiled_names == ["main.py", "parser.py", "version.py"]
    source_plan = plans.source
    mpy_plan = plans.mpy
    assert source_plan.mode == "source"
    assert mpy_plan.mode == "mpy"
    mpy_main = next(
        asset for asset in mpy_plan.assets if asset.key == "sd:main")
    assert mpy_main.kind == "mpy"
    assert mpy_main.payload.startswith(b"MPY")
    source_main = next(
        asset for asset in source_plan.assets if asset.key == "sd:main")
    assert source_main.kind == "source"
    for key in ("sd:fonts/Bally7x9", "sd:fonts/Neato5x7",
                "sd:fonts/FixedFont5x8"):
        font = next(asset for asset in mpy_plan.assets if asset.key == key)
        assert font.kind == "font"
        assert font.payload.startswith(b"XGF1")


def test_prepare_is_deterministic_across_repeated_builds(tmp_path):
    project = _project(tmp_path)

    first = prepare_release_plans(project, _recording_compiler([]))
    second = prepare_release_plans(project, _recording_compiler([]))

    assert first.source.manifest_sha256 == second.source.manifest_sha256
    assert first.mpy.manifest_sha256 == second.mpy.manifest_sha256


def test_prepare_propagates_a_compiler_failure(tmp_path):
    project = _project(tmp_path)
    main_py = str(project / "source" / "main.py")

    with pytest.raises(RuntimeError, match="compiler exploded"):
        prepare_release_plans(
            project, _recording_compiler([], fail_at=main_py))


def test_prepare_fails_closed_when_the_compiler_skips_an_output(tmp_path):
    project = _project(tmp_path)
    skip = (str(project / "source" / "main.py"),)

    with pytest.raises(ValueError, match="compiled"):
        prepare_release_plans(project, _recording_compiler([], skip=skip))
