import pytest

from calc import plugin_fixture
from runtime_fixture_pack import (
    FIXTURE_FILES,
    MISSING_SELECTION,
    VALID_SELECTION,
    ManagedPluginFixturePack,
    bind_verified_candidate,
)


DIRECTORY = "/sd/_sci_accept_support/functions"
DIGEST = b"d" * 32


class _Report:
    def __init__(self, loaded, errors):
        self.loaded = loaded
        self.errors = errors


class _Transaction:
    def __init__(self, complete, succeeded, report):
        self.complete = complete
        self.succeeded = succeeded
        self.report = report


def _snapshot(directory=DIRECTORY, digest=DIGEST, size=1):
    return plugin_fixture.PluginScenarioFixtureSnapshot(
        directory, digest, digest, digest, size, size, size)


def _ready_candidate(snapshot=None):
    snapshot = snapshot or _snapshot()
    candidate = plugin_fixture.PluginScenarioFixtureCandidate(snapshot)
    candidate._state = plugin_fixture._STATE_READY
    candidate._snapshot = snapshot
    return candidate


def _pack():
    return bind_verified_candidate(_ready_candidate())


def test_binds_only_the_verified_transient_fixture_snapshot():
    snapshot = _snapshot()
    pack = bind_verified_candidate(_ready_candidate(snapshot))

    assert pack.directory == DIRECTORY
    assert pack.files is FIXTURE_FILES
    assert pack.valid_selection is VALID_SELECTION
    assert pack.missing_selection is MISSING_SELECTION
    with pytest.raises(AttributeError, match="immutable"):
        pack._snapshot = snapshot


@pytest.mark.parametrize("snapshot", (
    _snapshot(directory="/sd/Add-ons"),
    _snapshot(digest=b"short"),
    _snapshot(size=0),
))
def test_rejects_noncanonical_or_unbounded_snapshots(snapshot):
    with pytest.raises(ValueError, match="snapshot"):
        bind_verified_candidate(_ready_candidate(snapshot))


def test_requires_a_completed_exact_fixture_candidate():
    candidate = plugin_fixture.PluginScenarioFixtureCandidate(_snapshot())

    with pytest.raises(ValueError, match="unavailable"):
        bind_verified_candidate(candidate)
    with pytest.raises(ValueError, match="candidate"):
        bind_verified_candidate(object())
    with pytest.raises(ValueError, match="candidate"):
        ManagedPluginFixturePack(_snapshot())


def test_reverify_must_return_the_bound_snapshot_identity():
    pack = _pack()

    assert pack.accepts_reverified_candidate(
        _ready_candidate(_snapshot())) is False
    assert pack.accepts_reverified_candidate(
        _ready_candidate(pack._snapshot)) is True
    assert type(pack.open_reverify()) is (
        plugin_fixture.PluginScenarioFixtureCandidate)


def test_valid_chain_result_requires_only_two_fixed_loaded_records():
    pack = _pack()
    transaction = _Transaction(True, True, _Report([
        ("_acceptance_core", 1, ""),
        ("_acceptance_dependent", 1, ""),
    ], []))

    assert pack.valid_reload_result(transaction) is True
    transaction.report.loaded.append(("unmanaged", 1, ""))
    assert pack.valid_reload_result(transaction) is False


def test_missing_dependency_result_requires_the_fixed_error():
    pack = _pack()
    transaction = _Transaction(
        True, False, _Report([], [
            ("_acceptance_missing",
             "Dependency failed: _acceptance_absent")]))

    assert pack.missing_reload_result(transaction) is True
    transaction.report.errors[0] = ("_acceptance_missing", "unexpected")
    assert pack.missing_reload_result(transaction) is False


def test_result_memory_error_is_not_hidden():
    pack = _pack()

    class MemoryReport:
        @property
        def loaded(self):
            raise MemoryError("injected report OOM")

        errors = []

    with pytest.raises(MemoryError, match="report OOM"):
        pack.valid_reload_result(_Transaction(True, True, MemoryReport()))
