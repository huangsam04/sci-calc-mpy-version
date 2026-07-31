import os
from pathlib import Path
import shutil
import subprocess


PROJECT = Path(__file__).parents[1]
ORCHESTRATOR = PROJECT / "tools" / "run_device_acceptance.ps1"
NATIVE_PROBE = PROJECT / "tests" / "_support" / "orchestrator_native_probe.ps1"
_STAGE_SCRIPTS = (
    "device_boot_probe.py",
    "device_application_acceptance.py",
    "device_runtime_monitor.py",
    "device_interaction_acceptance.py",
    "device_frame_allocation_probe.py",
)
_SUPPORT_MODULE_PATHS = (
    "benchmarks.mpy",
    "nav_scenario.mpy",
    "runtime_acceptance.mpy",
    "runtime_acceptance_bounded.mpy",
    "runtime_application_controller.mpy",
    "runtime_fixture_pack.mpy",
    "runtime_materialize.mpy",
    "runtime_scenarios.mpy",
    "runtime_trusted_construction.mpy",
    "calc/plugin_fixture.mpy",
    "calc/scenario_variables.mpy",
    "screens/about_scenario.mpy",
    "screens/calculator_scenario.mpy",
    "screens/function_panel_scenario.mpy",
    "screens/function_picker_scenario.mpy",
    "screens/letter_panel_scenario.mpy",
    "screens/plot_scenario.mpy",
    "screens/settings_scenario.mpy",
    "screens/stopwatch_scenario.mpy",
    "screens/variable_panel_scenario.mpy",
)
_SUPPORT_SOURCE_PATHS = (
    "functions/_acceptance_core.py",
    "functions/_acceptance_dependent.py",
    "functions/_acceptance_missing.py",
)
_SUPPORT_REMOTE_PATHS = tuple(
    "/sd/_sci_accept_support/" + path
    for path in _SUPPORT_MODULE_PATHS + _SUPPORT_SOURCE_PATHS
)


def _copy_orchestrator(tmp_path, missing_stage=""):
    project_copy = tmp_path / "workspace" / "mp_version"
    tools_copy = project_copy / "tools"
    tools_copy.mkdir(parents=True)
    orchestrator_copy = tools_copy / ORCHESTRATOR.name
    shutil.copyfile(ORCHESTRATOR, orchestrator_copy)
    for script_name in _STAGE_SCRIPTS:
        if script_name != missing_stage:
            (tools_copy / script_name).write_text("", encoding="utf-8")
    return orchestrator_copy


def _run_native_orchestrator(
        tmp_path, *, orchestrator=ORCHESTRATOR, fail_stage_script="",
        fail_reset=False, noisy_stage=False, expect_log=True):
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
    if expect_log:
        assert log.exists(), result.stdout + result.stderr
        commands = [
            tuple(line.split("\t"))
            for line in log.read_text(encoding="utf-8").splitlines()
        ]
    else:
        assert not log.exists(), result.stdout + result.stderr
        commands = []
    return result, commands


def _command_events(commands):
    events = []
    for command in commands:
        if command[-1] == "reset":
            events.append(("reset", command[1]))
        else:
            events.append(("run", Path(command[-1]).name))
    return events


def _compiled_protocol_events(commands):
    events = []
    for command in commands:
        if command[-1] == "reset":
            events.append(("reset", command[1]))
        elif "cp" in command:
            remote = command[-1]
            if remote.startswith(":/sd/_sci_accept_support/"):
                events.append(("support_upload", remote[1:]))
            else:
                events.append(("stage_upload", Path(command[-2]).name))
        elif "exec" in command:
            code = command[-1]
            if "ACCEPTANCE_SUPPORT_READY" in code:
                events.append(("support_prepare", "fixed"))
            elif "ACCEPTANCE_SUPPORT_REMOVED" in code:
                events.append(("support_remove", "fixed"))
            elif "_sci_accept_stage.run()" in code:
                mode = (
                    "support"
                    if "/sd/_sci_accept_support" in code
                    else "direct"
                )
                events.append(("stage_execute", mode))
            elif "gc.collect()" in code:
                events.append(("prepare", "resident"))
            else:
                events.append(("execute", code))
        elif "rm" in command:
            events.append(("cleanup", Path(command[-1]).name))
        else:
            events.append(("unexpected", command[-1]))
    return events


def _expected_compiled_protocol_events(stage_count=5):
    events = [
        ("reset", "TEST_PORT"),
        ("support_prepare", "fixed"),
    ]
    events.extend(
        ("support_upload", remote) for remote in _SUPPORT_REMOTE_PATHS)
    artifacts = (
        "device_boot_probe.mpy",
        "device_application_acceptance.mpy",
        "device_runtime_monitor.mpy",
        "device_interaction_acceptance.mpy",
        "device_frame_allocation_probe.mpy",
    )
    for index, artifact in enumerate(artifacts[:stage_count]):
        events.extend((
            ("prepare", "resident"),
            ("stage_upload", artifact),
            ("stage_execute", "direct" if index == 0 else "support"),
            ("reset", "TEST_PORT"),
        ))
    events.append(("support_remove", "fixed"))
    return events


def _expected_stage_events():
    return [
        ("run", "device_boot_probe.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_application_acceptance.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_runtime_monitor.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_interaction_acceptance.py"),
        ("reset", "TEST_PORT"),
        ("run", "device_frame_allocation_probe.py"),
        ("reset", "TEST_PORT"),
    ]


def test_device_acceptance_dry_run_orders_five_existing_tracers():
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
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_SUPPORT_INSTALL files=23",
        "ACCEPTANCE_STAGE boot_probe",
        ("ACCEPTANCE_COMMAND TEST_PORT "
         ".work/mpy/device-tools/device_boot_probe.mpy"),
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE application_matrix",
        ("ACCEPTANCE_COMMAND TEST_PORT "
         ".work/mpy/device-tools/device_application_acceptance.mpy"),
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE runtime_target_tracer",
        ("ACCEPTANCE_COMMAND TEST_PORT "
         ".work/mpy/device-tools/device_runtime_monitor.mpy"),
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE interaction_screen_tracer",
        ("ACCEPTANCE_COMMAND TEST_PORT "
         ".work/mpy/device-tools/device_interaction_acceptance.mpy"),
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE frame_allocation_probe",
        ("ACCEPTANCE_COMMAND TEST_PORT "
         ".work/mpy/device-tools/device_frame_allocation_probe.mpy"),
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_SUPPORT_REMOVE files=23",
        "ACCEPTANCE_DRY_RUN_COMPLETE TEST_PORT stages=5",
    ]
    assert "application_matrix" in result.stdout


def test_device_acceptance_sleeps_through_the_nav_owner_interface():
    source = ORCHESTRATOR.read_text(encoding="utf-8")

    assert source.count("r._nav.renderer.display.sleep()") == 3
    assert "_binding_state[" not in source


def test_device_acceptance_failure_still_resets_before_stopping(tmp_path):
    powershell = shutil.which("pwsh")
    assert powershell is not None
    orchestrator = _copy_orchestrator(tmp_path)

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(orchestrator),
            "-Port",
            "TEST_PORT",
            "-DryRun",
            "-DryRunFailureStage",
            "runtime_target_tracer",
        ],
        cwd=orchestrator.parents[1],
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
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_SUPPORT_INSTALL files=23",
        "ACCEPTANCE_STAGE boot_probe",
        ("ACCEPTANCE_COMMAND TEST_PORT "
         ".work/mpy/device-tools/device_boot_probe.mpy"),
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE application_matrix",
        ("ACCEPTANCE_COMMAND TEST_PORT "
         ".work/mpy/device-tools/device_application_acceptance.mpy"),
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_STAGE runtime_target_tracer",
        ("ACCEPTANCE_COMMAND TEST_PORT "
         ".work/mpy/device-tools/device_runtime_monitor.mpy"),
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_SUPPORT_REMOVE files=23",
    ]


def test_device_acceptance_missing_stage_script_still_resets(tmp_path):
    powershell = shutil.which("pwsh")
    assert powershell is not None
    orchestrator = _copy_orchestrator(
        tmp_path, missing_stage="device_boot_probe.py")

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(orchestrator),
            "-Port",
            "TEST_PORT",
            "-DryRun",
            "-BootWaitMs",
            "0",
        ],
        cwd=orchestrator.parents[1],
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
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_SUPPORT_INSTALL files=23",
        "ACCEPTANCE_STAGE boot_probe",
        "ACCEPTANCE_RESET TEST_PORT",
        "ACCEPTANCE_SUPPORT_REMOVE files=23",
    ]


def test_stage_and_reset_failure_preserves_stage_error_and_reports_reset(
        tmp_path):
    result, commands = _run_native_orchestrator(
        tmp_path,
        fail_stage_script="device_boot_probe.py",
        fail_reset=True,
    )

    assert result.returncode != 0
    assert _compiled_protocol_events(commands) == (
        _expected_compiled_protocol_events(stage_count=1)
    )
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
    assert _compiled_protocol_events(commands) == (
        _expected_compiled_protocol_events(stage_count=3)
    )
    assert (
        "PROBE_CAUGHT Acceptance stage failed: runtime_target_tracer"
        in result.stdout
    )


def test_failure_cleanup_removes_stage_and_fixed_support_payloads(tmp_path):
    result, commands = _run_native_orchestrator(
        tmp_path,
        fail_stage_script="device_runtime_monitor.py",
    )

    assert result.returncode != 0
    cleanup = next(
        command[-1] for command in commands
        if "exec" in command
        and "ACCEPTANCE_SUPPORT_REMOVED" in command[-1]
    )
    assert "/sd/_sci_accept_stage.mpy" in cleanup
    for remote in _SUPPORT_REMOTE_PATHS:
        assert remote in cleanup


def test_native_reset_failure_stops_before_the_next_stage(tmp_path):
    result, commands = _run_native_orchestrator(
        tmp_path,
        fail_reset=True,
    )

    assert result.returncode != 0
    assert _compiled_protocol_events(commands) == (
        _expected_compiled_protocol_events()[:1]
    )
    assert (
        "PROBE_CAUGHT "
        "Unable to reset TEST_PORT after acceptance stage"
    ) in result.stdout


def test_injected_adapter_runs_all_five_stages_without_workspace_python(
        tmp_path):
    orchestrator = _copy_orchestrator(tmp_path)

    result, commands = _run_native_orchestrator(
        tmp_path,
        orchestrator=orchestrator,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _compiled_protocol_events(commands) == (
        _expected_compiled_protocol_events()
    )
    assert (
        "ACCEPTANCE_COMPLETE TEST_PORT stages=5 "
        "animation=disabled_heap_below_12k"
    ) in result.stdout


def test_compiled_stage_removes_its_artifact_before_running(tmp_path):
    result, commands = _run_native_orchestrator(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    executions = [
        command[-1] for command in commands
        if "exec" in command and "_sci_accept_stage.run()" in command[-1]
    ]
    assert len(executions) == 5
    for index, code in enumerate(executions):
        assert "sys.path.append('/sd')" in code
        assert code.index(
            "os.remove('/sd/_sci_accept_stage.mpy')") < code.index(
                "_sci_accept_stage.run()")
        if index == 0:
            assert "import calc.plugin_fixture" not in code
            assert "screens.about_scenario" not in code
            continue
        assert "support='/sd/_sci_accept_support'" in code
        assert "sys.path.insert(0,support)" in code
        assert "calc.__path__=support+'/calc'" in code
        assert "screens.__path__=support+'/screens'" in code
        assert "functions.__path__=support+'/functions'" in code
        assert "plugin_fixture" not in code
        assert "_scenario" not in code
        assert "calc_path=" not in code


def test_injected_adapter_rejects_ambiguous_pipeline_output(tmp_path):
    result, commands = _run_native_orchestrator(
        tmp_path,
        noisy_stage=True,
    )

    assert result.returncode != 0
    assert _compiled_protocol_events(commands) == [
        ("reset", "TEST_PORT"),
        ("support_prepare", "fixed"),
        ("support_remove", "fixed"),
    ]
    assert (
        "PROBE_CAUGHT "
        "MpremoteAdapter must return exactly one integer exit code"
    ) in result.stdout
