import builtins
from pathlib import Path

import pytest

from calc import plugin_fixture


DIRECTORY = "/sd/_sci_accept_support/functions"
PAYLOADS = {
    "_acceptance_core.py": b"def register(registry):\n    pass\n",
    "_acceptance_dependent.py": b"DEPENDENCIES = ('_acceptance_core',)\n",
    "_acceptance_missing.py": b"DEPENDENCIES = ('_acceptance_absent',)\n",
}


def _install_files(monkeypatch, tmp_path):
    for name, payload in PAYLOADS.items():
        (tmp_path / name).write_bytes(payload)
    real_open = builtins.open

    def mapped_open(path, mode="r"):
        assert str(path).startswith(DIRECTORY + "/")
        return real_open(tmp_path / Path(path).name, mode)

    monkeypatch.setattr(plugin_fixture, "open", mapped_open, raising=False)


def _finish(candidate):
    for _ in range(64):
        if candidate.step():
            return candidate
    raise AssertionError("fixture candidate did not finish")


def test_configures_only_the_fixed_transient_fixture(monkeypatch, tmp_path):
    _install_files(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="directory"):
        plugin_fixture.configure_transient_fixture("/sd/Add-ons")
    snapshot = plugin_fixture.configure_transient_fixture(DIRECTORY)

    assert snapshot.directory == DIRECTORY
    assert snapshot.files == tuple(PAYLOADS)
    for index, payload in enumerate(PAYLOADS.values()):
        assert snapshot.expected_size(index) == len(payload)
        assert len(snapshot.expected_digest(index)) == 32
    with pytest.raises(AttributeError, match="immutable"):
        snapshot.directory = "/sd/Add-ons"


def test_candidate_reverifies_all_three_files_incrementally(
        monkeypatch, tmp_path):
    _install_files(monkeypatch, tmp_path)
    snapshot = plugin_fixture.configure_transient_fixture(DIRECTORY)

    candidate = _finish(plugin_fixture.PluginScenarioFixtureCandidate())

    assert candidate.available is True
    assert candidate.snapshot is snapshot
    assert candidate.reason == plugin_fixture.REASON_NONE
    assert candidate.close() is True
    with pytest.raises(RuntimeError, match="closed"):
        candidate.step()


def test_candidate_rejects_a_changed_fixture_file(monkeypatch, tmp_path):
    _install_files(monkeypatch, tmp_path)
    snapshot = plugin_fixture.configure_transient_fixture(DIRECTORY)
    (tmp_path / "_acceptance_dependent.py").write_bytes(b"changed\n")

    candidate = _finish(snapshot.open_reverify())

    assert candidate.available is False
    assert candidate.reason == plugin_fixture.REASON_FILE


def test_candidate_preserves_memory_error(monkeypatch, tmp_path):
    _install_files(monkeypatch, tmp_path)
    snapshot = plugin_fixture.configure_transient_fixture(DIRECTORY)
    failure = MemoryError("injected fixture OOM")

    class FailingStream:
        def readinto(self, _buffer):
            raise failure

        def close(self):
            pass

    monkeypatch.setattr(
        plugin_fixture, "open", lambda _path, _mode: FailingStream(),
        raising=False)
    candidate = snapshot.open_reverify()
    assert candidate.step() is False

    with pytest.raises(MemoryError) as caught:
        candidate.step()

    assert caught.value is failure
    assert candidate.available is False
    assert candidate.complete is True


def test_candidate_turns_an_ordinary_io_failure_into_unavailable(
        monkeypatch, tmp_path):
    _install_files(monkeypatch, tmp_path)
    snapshot = plugin_fixture.configure_transient_fixture(DIRECTORY)
    monkeypatch.setattr(
        plugin_fixture, "open",
        lambda _path, _mode: (_ for _ in ()).throw(OSError("missing")),
        raising=False)

    candidate = snapshot.open_reverify()

    assert candidate.step() is True
    assert candidate.available is False
    assert candidate.reason == plugin_fixture.REASON_IO
