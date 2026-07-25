import os
from pathlib import Path
import shutil
import subprocess


PROJECT = Path(__file__).parents[1]
ORCHESTRATOR = PROJECT / "tools" / "run_device_acceptance.ps1"
NATIVE_PROBE = PROJECT / "tests" / "_support" / "orchestrator_native_probe.ps1"


def _run_native_orchestrator(
        tmp_path, *, orchestrator=ORCHESTRATOR, fail_stage_script="",
        fail_reset=False, noisy_stage=False):
    powershell = shutil.which("pwsh")
    assert powershell is not None
    log = tmp_path / "fake-mpremote.log"
    environment = os.environ.copy()
    environment["SCI_CALC_FAKE_MPREMOTE_LOG"] = str(log)
    environment["SCI_CALC_FAKE_FAIL_STAGE_SCRIPT"] = fail_stage_script
    environment["SCI_CALC_FAKE_FAIL_RESET"] = "1" if fail_reset else "0"
    environment["SCI_CALC_FAKE_NOISY_STAGE"] = "1" if noisy_stage else "0"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(NATIVE_PROBE),
            "-Orchestrator",
            str(orchestrator),
            "-Port",
            "TEST_PORT",
        ],
        cwd=PROJECT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert log.exists(), result.stdout + result.stderr
    commands = [
        tuple(line.split("\t"))
        for line in log.read_text(encoding="utf-8").splitlines()
    ]
    return result, commands


def _command_events(commands):
    events = []
    for command in commands:
        if command[-1] == "reset":
            events.append(("reset", command[1]))
        else:
            events.append(("run", Path(command[-1]).name))
    return events


def test_device_acceptance_dry_run_orders_every_stage_with_a_finally_reset():
    powershell = shutil.which("pwsh")
    assert powershell is not None

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(ORCHESTRATOR),
            "-Port",
            "TEST_PORT",
            "-DryRun",
        ],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    events = [
        line for line in result.stdout.splitlines()
        if line.startswith("ACCEPTANCE_")
    ]
    assert events == [
        "ACCEPTANCE_STAGE boot_probe",
        "ACCEPTANCE_COMMAND TEST_PORT tools/device_boot_probe.py",
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE runtime_target_tracer",
        "ACCEPTANCE_COMMAND TEST_PORT tools/device_runtime_monitor.py",
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE interaction_screen_tracer",
        "ACCEPTANCE_COMMAND TEST_PORT tools/device_interaction_acceptance.py",
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE application_matrix",
        "ACCEPTANCE_COMMAND TEST_PORT tools/device_application_acceptance.py",
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_COMPLETE TEST_PORT",
    ]


def test_device_acceptance_tracer_only_uses_a_narrow_completion_label():
    powershell = shutil.which("pwsh")
    assert powershell is not None

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(ORCHESTRATOR),
            "-Port",
            "TEST_PORT",
            "-DryRun",
            "-TracerOnly",
        ],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    events = [
        line for line in result.stdout.splitlines()
        if line.startswith("ACCEPTANCE_")
    ]
    assert events == [
        "ACCEPTANCE_STAGE boot_probe",
        "ACCEPTANCE_COMMAND TEST_PORT tools/device_boot_probe.py",
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE runtime_target_tracer",
        "ACCEPTANCE_COMMAND TEST_PORT tools/device_runtime_monitor.py",
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE interaction_screen_tracer",
        "ACCEPTANCE_COMMAND TEST_PORT tools/device_interaction_acceptance.py",
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_TRACERS_COMPLETE TEST_PORT",
    ]


def test_device_acceptance_failure_still_resets_before_stopping():
    powershell = shutil.which("pwsh")
    assert powershell is not None

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(ORCHESTRATOR),
            "-Port",
            "TEST_PORT",
            "-DryRun",
            "-DryRunFailureStage",
            "runtime_target_tracer",
        ],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    events = [
        line for line in result.stdout.splitlines()
        if line.startswith("ACCEPTANCE_")
    ]
    assert events == [
        "ACCEPTANCE_STAGE boot_probe",
        "ACCEPTANCE_COMMAND TEST_PORT tools/device_boot_probe.py",
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE runtime_target_tracer",
        "ACCEPTANCE_COMMAND TEST_PORT tools/device_runtime_monitor.py",
        "ACCEPTANCE_RESET TEST_PORT",
    ]


def test_device_acceptance_missing_stage_script_still_resets(tmp_path):
    powershell = shutil.which("pwsh")
    assert powershell is not None
    project_copy = tmp_path / "workspace" / "mp_version"
    tools_copy = project_copy / "tools"
    tools_copy.mkdir(parents=True)
    orchestrator_copy = tools_copy / ORCHESTRATOR.name
    shutil.copyfile(ORCHESTRATOR, orchestrator_copy)

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(orchestrator_copy),
            "-Port",
            "TEST_PORT",
            "-DryRun",
            "-BootWaitMs",
            "0",
        ],
        cwd=project_copy,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    events = [
        line for line in result.stdout.splitlines()
        if line.startswith("ACCEPTANCE_")
    ]
    assert events == [
        "ACCEPTANCE_STAGE boot_probe",
        "ACCEPTANCE_RESET TEST_PORT",
    ]


def test_stage_and_reset_failure_preserves_stage_error_and_reports_reset(
        tmp_path):
    result, commands = _run_native_orchestrator(
        tmp_path,
        fail_stage_script="device_boot_probe.py",
        fail_reset=True,
    )

    assert result.returncode != 0
    assert [command[-1] for command in commands] == [
        str(PROJECT / "tools" / "device_boot_probe.py"),
        "reset",
    ]
    assert "PROBE_CAUGHT Acceptance stage failed: boot_probe" in result.stdout
    assert (
        "Acceptance reset also failed: "
        "Unable to reset TEST_PORT after acceptance stage"
    ) in result.stderr


def test_native_stage_failure_resets_then_stops(tmp_path):
    result, commands = _run_native_orchestrator(
        tmp_path,
        fail_stage_script="device_runtime_monitor.py",
    )

    assert result.returncode != 0
    assert _command_events(commands) == [
        ("run", "device_boot_probe.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_runtime_monitor.py"),
        ("reset", "TEST_PORT"),
    ]
    assert (
        "PROBE_CAUGHT Acceptance stage failed: runtime_target_tracer"
    ) in (
        result.stdout)


def test_application_matrix_failure_resets_and_never_reports_completion(
        tmp_path):
    result, commands = _run_native_orchestrator(
        tmp_path,
        fail_stage_script="device_application_acceptance.py",
    )

    assert result.returncode != 0
    assert _command_events(commands) == [
        ("run", "device_boot_probe.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_runtime_monitor.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_interaction_acceptance.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_application_acceptance.py"),
        ("reset", "TEST_PORT"),
    ]
    assert (
        "PROBE_CAUGHT Acceptance stage failed: application_matrix"
    ) in result.stdout
    assert "ACCEPTANCE_COMPLETE TEST_PORT" not in result.stdout


def test_native_reset_failure_stops_before_the_next_stage(tmp_path):
    result, commands = _run_native_orchestrator(
        tmp_path,
        fail_reset=True,
    )

    assert result.returncode != 0
    assert _command_events(commands) == [
        ("run", "device_boot_probe.py"),
        ("reset", "TEST_PORT"),
    ]
    assert (
        "PROBE_CAUGHT "
        "Unable to reset TEST_PORT after acceptance stage"
    ) in result.stdout


def test_injected_adapter_does_not_require_a_workspace_python(tmp_path):
    project_copy = tmp_path / "workspace" / "mp_version"
    tools_copy = project_copy / "tools"
    tools_copy.mkdir(parents=True)
    orchestrator_copy = tools_copy / ORCHESTRATOR.name
    shutil.copyfile(ORCHESTRATOR, orchestrator_copy)
    for script_name in (
            "device_boot_probe.py",
            "device_runtime_monitor.py",
            "device_interaction_acceptance.py",
            "device_application_acceptance.py"):
        (tools_copy / script_name).write_text("", encoding="utf-8")

    result, commands = _run_native_orchestrator(
        tmp_path,
        orchestrator=orchestrator_copy,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _command_events(commands) == [
        ("run", "device_boot_probe.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_runtime_monitor.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_interaction_acceptance.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_application_acceptance.py"),
        ("reset", "TEST_PORT"),
    ]
    assert "ACCEPTANCE_COMPLETE TEST_PORT" in result.stdout


def test_injected_adapter_rejects_ambiguous_pipeline_output(tmp_path):
    result, commands = _run_native_orchestrator(
        tmp_path,
        noisy_stage=True,
    )

    assert result.returncode != 0
    assert _command_events(commands) == [
        ("run", "device_boot_probe.py"),
        ("reset", "TEST_PORT"),
    ]
    assert (
        "PROBE_CAUGHT "
        "MpremoteAdapter must return exactly one integer exit code"
    ) in result.stdout
