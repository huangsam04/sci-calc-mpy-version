import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import device_application_acceptance


class _Display:
    def __init__(self):
        self.gs4_buf = bytearray(8192)
        self.sleep_count = 0

    def sleep(self):
        self.sleep_count += 1


class _Memory:
    def __init__(self):
        self._plot_curve = bytearray(104)

    def get_plot_workspace(self):
        return self._plot_curve


class _Binding:
    def __init__(self):
        self.display = _Display()
        nav = type("Nav", (), {})()
        nav.renderer = type("Renderer", (), {"display": self.display})()
        nav.memory = _Memory()
        self._binding_state = (
            tuple(object() for _ in range(10)),
            object(), {}, object(), nav,
        )


@pytest.fixture
def device_heap(monkeypatch):
    monkeypatch.setattr(
        device_application_acceptance.gc, "mem_free", lambda: 20000,
        raising=False)


def test_device_application_acceptance_runs_exactly_five_resident_rounds(
        monkeypatch, device_heap):
    binding = _Binding()
    rounds = []
    lines = []

    def exercise(state):
        assert state is binding._binding_state
        rounds.append(len(rounds) + 1)
        return 18000

    monkeypatch.setattr(
        device_application_acceptance, "_exercise_round", exercise)

    report = device_application_acceptance.run(binding, emit=lines.append)

    assert rounds == [1, 2, 3, 4, 5]
    assert report == (20000, 0, 0, 0)
    assert lines[-1] == "APPLICATION_RESULT PASS memory_errors=0 errors=0"
    assert "framebuffer_bytes=8192" in lines[-2]
    assert binding.display.sleep_count == 2


def test_device_application_acceptance_enforces_operation_reserve(
        monkeypatch, device_heap):
    binding = _Binding()
    lines = []
    monkeypatch.setattr(
        device_application_acceptance, "_exercise_round", lambda _state: 3500)
    monkeypatch.setattr(
        device_application_acceptance, "_sample", lambda: 3500)

    with pytest.raises(RuntimeError, match="operation reserve"):
        device_application_acceptance.run(binding, emit=lines.append)

    assert lines[-1] == "APPLICATION_RESULT FAIL memory_errors=0 errors=1"
    assert binding.display.sleep_count == 2


def test_device_application_acceptance_preserves_memory_error_and_sleeps_oled(
        monkeypatch, device_heap):
    binding = _Binding()
    failure = MemoryError("measured")
    lines = []

    def fail(_state):
        raise failure

    monkeypatch.setattr(
        device_application_acceptance, "_exercise_round", fail)

    with pytest.raises(MemoryError) as caught:
        device_application_acceptance.run(binding, emit=lines.append)

    assert caught.value is failure
    assert lines[-1] == "APPLICATION_RESULT FAIL memory_errors=1 errors=0"
    assert binding.display.sleep_count == 2


def test_device_application_entry_has_no_legacy_matrix_import_graph():
    source = (TOOLS / "device_application_acceptance.py").read_text(
        encoding="utf-8")

    assert "runtime_materialize" not in source
    assert "runtime_scenarios" not in source
    assert "runtime_acceptance" not in source
    assert "runtime_application_controller" not in source
    assert "controller=" not in source
    assert "history.clear()" not in source
    assert "laps.clear()" not in source
