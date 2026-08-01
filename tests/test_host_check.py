import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest


PROJECT = Path(__file__).parents[1]
PYTEST_TEMP_ROOT = PROJECT / ".work" / "pytest"
PYTEST_SESSION_ROOT = PYTEST_TEMP_ROOT / "sessions"
TEMP_PROBE = PROJECT / "tests" / "_support" / "pytest_temp_probe.py"
CHECK_SCRIPT = PROJECT / "check.ps1"
CHECK_SUPPORT = PROJECT / "tools" / "host_check_support.ps1"
ENV_PROBE = PROJECT / "tests" / "_support" / "host_check_env_probe.ps1"
DEVICE_COMPILE_PROBE = (
    PROJECT / "tests" / "_support" / "device_compile_probe.ps1")


def _run_temp_probe(*, fail=False):
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    outside_temp = str(PROJECT.parent / ".pytest_tmp")
    environment["TEMP"] = outside_temp
    environment["TMP"] = outside_temp
    environment["TMPDIR"] = outside_temp
    environment.pop("PYTHONPYCACHEPREFIX", None)
    if fail:
        environment["SCI_CALC_PYTEST_PROBE_FAIL"] = "1"
    environment.pop("PYTEST_ADDOPTS", None)

    return subprocess.run(
        [sys.executable, "-m", "pytest", "-s", str(TEMP_PROBE)],
        cwd=PROJECT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _reported_session_temp(result):
    marker = "SCI_CALC_PYTEST_BASE="
    line = next(
        (item for item in result.stdout.splitlines() if item.startswith(marker)),
        None,
    )
    assert line is not None, result.stdout
    return Path(line[len(marker) :]).resolve()


def _reported_path(result, marker):
    line = next(
        (item for item in result.stdout.splitlines() if item.startswith(marker)),
        None,
    )
    assert line is not None, result.stdout
    return Path(line[len(marker) :]).resolve()


def _assert_project_temp_was_cleaned(session_temp):
    assert session_temp.parent == PYTEST_SESSION_ROOT.resolve()
    assert re.fullmatch(r"[0-9a-f]{32}", session_temp.name)
    assert not session_temp.exists()


def test_direct_pytest_uses_and_cleans_a_unique_project_temp_directory():
    result = _run_temp_probe()

    assert result.returncode == 0, result.stdout + result.stderr
    _assert_project_temp_was_cleaned(_reported_session_temp(result))


def test_direct_pytest_confines_process_temp_and_python_cache_to_work_root():
    result = _run_temp_probe()

    assert result.returncode == 0, result.stdout + result.stderr
    expected_temp = (PROJECT / ".work" / "temp").resolve()
    for marker in (
        "SCI_CALC_PROCESS_TEMP=",
        "SCI_CALC_PROCESS_TMP=",
        "SCI_CALC_PROCESS_TMPDIR=",
    ):
        assert _reported_path(result, marker) == expected_temp
    assert _reported_path(
        result, "SCI_CALC_PYTHON_CACHE=") == (
            PROJECT / ".work" / "pycache").resolve()


def test_failed_direct_pytest_still_cleans_its_project_temp_directory():
    result = _run_temp_probe(fail=True)

    assert result.returncode == 1, result.stdout + result.stderr
    _assert_project_temp_was_cleaned(_reported_session_temp(result))


@pytest.mark.parametrize("source", ("environment", "command_line"))
def test_direct_pytest_rejects_a_caller_supplied_basetemp_without_deleting_it(
        tmp_path, source):
    supplied = tmp_path / ("caller-basetemp-" + source)
    supplied.mkdir()
    sentinel = supplied / "must-survive.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("PYTEST_ADDOPTS", None)
    command = [sys.executable, "-m", "pytest", "-s", str(TEMP_PROBE)]
    supplied_option = supplied.as_posix()
    if source == "environment":
        environment["PYTEST_ADDOPTS"] = (
            "--basetemp=" + shlex.quote(supplied_option))
    else:
        command.append("--basetemp=" + supplied_option)

    result = subprocess.run(
        command,
        cwd=PROJECT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert "caller-supplied pytest basetemp is forbidden" in (
        result.stdout + result.stderr)


def test_host_check_reuses_the_direct_pytest_temp_policy():
    script = CHECK_SCRIPT.read_text(encoding="utf-8")

    assert "GetTempPath" not in script
    assert "$PytestTemp" not in script
    assert "--basetemp" not in script


def test_host_check_isolates_and_restores_ambient_pytest_addopts():
    powershell = shutil.which("pwsh")
    assert powershell is not None
    environment = os.environ.copy()
    environment["PYTEST_ADDOPTS"] = "--basetemp=caller-owned"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(ENV_PROBE),
            "-SupportScript",
            str(CHECK_SUPPORT),
        ],
        cwd=PROJECT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "PYTEST_ADDOPTS_INSIDE <unset>",
        "PYTEST_ADDOPTS_AFTER --basetemp=caller-owned",
    ]


def test_host_check_mpy_compiles_every_device_tool():
    script = CHECK_SCRIPT.read_text(encoding="utf-8")
    support = CHECK_SUPPORT.read_text(encoding="utf-8")

    assert "tooling\\mpy-cross-v1.28\\mpy-cross.exe" in script
    assert 'MpyVersion -notmatch "MicroPython v1\\.28\\.0"' in script
    assert 'MpyVersion -notmatch "mpy v6\\.3"' in script
    assert "Invoke-DeviceToolCompilation" in script
    assert 'Join-Path $ProjectRoot ".work"' in script
    assert 'Join-Path $WorkRoot "mpy"' in script
    assert 'Join-Path $ProjectRoot "tools"' in script
    assert 'Join-Path $BuildRoot "device-tools"' in script
    assert "-X no-source-lines" not in script
    assert "-X no-source-lines" not in support
    assert "-s $Relative" in script
    assert '-s $EmbeddedSource' in support


def test_generated_build_and_test_paths_share_one_work_root():
    expected = {
        "check.ps1": (
            'Join-Path $ProjectRoot ".work"',
            '[Environment]::GetEnvironmentVariable',
            '[Environment]::SetEnvironmentVariable',
        ),
        "tools/build_firmware.ps1": (
            'Join-Path $ProjectRoot ".work"',
            'Join-Path $WorkRoot "firmware\\product"',
            'set "PYTHONPYCACHEPREFIX=',
        ),
        "tools/flash_firmware.ps1": (
            'Join-Path $ProjectRoot ".work"',
            'Join-Path $WorkRoot "pycache"',
            '[Environment]::GetEnvironmentVariable',
            '[Environment]::SetEnvironmentVariable',
        ),
        "tools/run_device_acceptance.ps1": (
            'Join-Path $ProjectRoot ".work"',
            '.work/mpy/device-tools',
            '[Environment]::GetEnvironmentVariable',
            '[Environment]::SetEnvironmentVariable',
        ),
        "tools/release_deploy.py": (
            'WORK_ROOT = PROJECT_ROOT / ".work"',
            'sys.pycache_prefix = str(PYTHON_CACHE_ROOT)',
        ),
        "tests/conftest.py": (
            'WORK_ROOT = PROJECT / ".work"',
            'PYTEST_TEMP_ROOT = WORK_ROOT / "pytest"',
        ),
    }
    for relative, markers in expected.items():
        source = (PROJECT / relative).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source, relative


def test_git_does_not_hide_generated_paths_outside_work_root():
    ignored = (PROJECT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".work/" in ignored
    assert ".pytest_cache/" not in ignored
    assert not any("__pycache__" in line for line in ignored)


def test_direct_release_cli_keeps_local_import_cache_under_work_root():
    environment = os.environ.copy()
    environment.pop("PYTHONPYCACHEPREFIX", None)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT / "tools" / "release_deploy.py"),
            "--help",
        ],
        cwd=PROJECT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Deploy SCI-CALC" in result.stdout
    assert not (PROJECT / "tools" / "__pycache__").exists()


@pytest.mark.parametrize(
    (
        "with_source",
        "create_output",
        "extra_arguments",
        "expected_error",
    ),
    (
        (False, False, (), "No device tools matched"),
        (True, False, (), "did not create its output"),
        (
            True,
            False,
            ("-CompilerExitCode", "7"),
            "mpy-cross failed",
        ),
        (
            True,
            False,
            ("-CreateEmptyOutput",),
            "empty output",
        ),
    ),
)
def test_device_tool_compile_gate_rejects_missing_inputs_or_outputs(
        tmp_path, with_source, create_output, extra_arguments,
        expected_error):
    powershell = shutil.which("pwsh")
    assert powershell is not None
    tools = tmp_path / "tools"
    output = tmp_path / "build"
    tools.mkdir()
    if with_source:
        (tools / "device_probe.py").write_text("pass\n", encoding="utf-8")

    command = [
        powershell,
        "-NoProfile",
        "-File",
        str(DEVICE_COMPILE_PROBE),
        "-SupportScript",
        str(CHECK_SUPPORT),
        "-ToolsRoot",
        str(tools),
        "-OutputRoot",
        str(output),
    ]
    if create_output:
        command.append("-CreateOutput")
    command.extend(extra_arguments)
    result = subprocess.run(
        command,
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert expected_error in result.stdout
