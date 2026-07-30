import sys
from pathlib import Path

import pytest

from calc import plugin_fixture


TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import device_application_acceptance


class _Display:
    def __init__(self):
        self.sleep_count = 0

    def sleep(self):
        self.sleep_count += 1


class _Runtime:
    mode = "resident"

    def __init__(self):
        self.display = _Display()
        renderer = type("Renderer", (), {"display": self.display})()
        self.nav = type("Nav", (), {"renderer": renderer})()


class _Report:
    def __init__(self, *, accepted=True, memory_errors=0, errors=0,
                 primary_error=None, failure_mask=0):
        self.accepted = accepted
        self.memory_errors = memory_errors
        self.errors = errors
        self.primary_error = primary_error
        self.failure_mask = failure_mask
        self.rounds_completed = 5 if accepted else 0
        self.scenarios_completed = 35 if accepted else 0
        self.runtime_steps = 400
        self.heap_min = 20000
        self.heap_after = 21000
        self.heap_delta = -160
        self.blocking_max_us = 24000
        self.buffer_peak_bytes = 8296
        self.step_name = "calculator_history"
        self.phase = 1
        self.bounded_close_attempts = 1
        self.bounded_session_restored = True
        self.blocking_round = 0
        self.blocking_step = 1


def test_device_application_acceptance_runs_the_shared_five_round_matrix(
        monkeypatch):
    runtime = _Runtime()
    lines = []
    calls = []
    report = _Report()

    def run_matrix(candidate):
        calls.append(candidate)
        return report

    monkeypatch.setattr(
        device_application_acceptance, "_run_matrix", run_matrix)

    result = device_application_acceptance.run(runtime, emit=lines.append)

    assert calls == [runtime]
    assert result == (20000, -160, 0, 0)
    assert lines[-1] == (
        "APPLICATION_RESULT PASS memory_errors=0 errors=0 failure_mask=0")
    assert "framebuffer_bytes=8192" in lines[-2]
    assert runtime.display.sleep_count == 2


def test_device_application_acceptance_binds_transient_fixture_before_runtime(
        monkeypatch):
    runtime = _Runtime()
    report = _Report()
    calls = []

    monkeypatch.setattr(
        plugin_fixture,
        "configure_transient_fixture",
        lambda directory: calls.append(("fixture", directory)),
    )
    monkeypatch.setattr(
        device_application_acceptance,
        "_resident_runtime",
        lambda: calls.append(("runtime", None)) or runtime,
    )
    monkeypatch.setattr(
        device_application_acceptance,
        "_run_matrix",
        lambda candidate: calls.append(("matrix", candidate)) or report,
    )

    device_application_acceptance.run(emit=lambda _line: None)

    assert calls == [
        ("fixture", "/sd/_sci_accept_support/functions"),
        ("runtime", None),
        ("matrix", runtime),
    ]


def test_device_application_acceptance_rejects_a_failed_shared_report(
        monkeypatch):
    runtime = _Runtime()
    lines = []
    monkeypatch.setattr(
        device_application_acceptance,
        "_run_matrix",
        lambda _runtime: _Report(
            accepted=False, errors=1, failure_mask=8),
    )

    with pytest.raises(RuntimeError, match="application matrix"):
        device_application_acceptance.run(runtime, emit=lines.append)

    assert lines[-1] == (
        "APPLICATION_RESULT FAIL memory_errors=0 errors=1 failure_mask=8")
    assert runtime.display.sleep_count == 2


def test_device_application_acceptance_preserves_memory_error_and_sleeps_oled(
        monkeypatch):
    runtime = _Runtime()
    failure = MemoryError("measured")
    monkeypatch.setattr(
        device_application_acceptance,
        "_run_matrix",
        lambda _runtime: _Report(
            accepted=False, memory_errors=1,
            primary_error=failure, failure_mask=1),
    )

    with pytest.raises(MemoryError) as caught:
        device_application_acceptance.run(runtime, emit=lambda _line: None)

    assert caught.value is failure
    assert runtime.display.sleep_count == 2


def test_device_application_entry_has_no_resident_screen_tuple_path():
    source = (TOOLS / "device_application_acceptance.py").read_text(
        encoding="utf-8")

    assert "runtime_materialize" in source
    assert "runtime_acceptance" in source
    assert "application_scenarios" in source
    assert "_SCENARIO_MODULES" in source
    assert "_drop_modules" in source
    assert "_binding_state" not in source
    assert ".screens" not in source


def test_variable_scenario_preloads_its_calculator_lease_dependency():
    assert device_application_acceptance._SCENARIO_MODULES[2] == (
        "calc.scenario_variables",
        "screens.calculator_scenario",
    )
