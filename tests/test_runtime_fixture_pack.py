import pytest

from calc import plugin_fixture
from runtime_fixture_pack import (
    FIXTURE_FILES,
    MISSING_SELECTION,
    VALID_SELECTION,
    ManagedPluginFixturePack,
    bind_verified_candidate,
)


_RELEASE_ID = "a" * 64
_DIGEST = b"d" * 32


class _Report:
    def __init__(self, loaded, errors):
        self.loaded = loaded
        self.errors = errors


class _Transaction:
    def __init__(self, complete, succeeded, report):
        self.complete = complete
        self.succeeded = succeeded
        self.report = report


def _snapshot(**changes):
    values = {
        "root": "/sd/.slots/A",
        "directory": "/sd/.slots/A/functions",
        "slot_name": "A",
        "release_id": _RELEASE_ID,
        "manifest_sha256": _DIGEST,
        "digest0": _DIGEST,
        "digest1": _DIGEST,
        "digest2": _DIGEST,
        "size0": 1,
        "size1": 2,
        "size2": 3,
    }
    values.update(changes)
    return plugin_fixture.PluginScenarioFixtureSnapshot(**values)


def _ready_candidate(snapshot):
    candidate = plugin_fixture.PluginScenarioFixtureCandidate()
    candidate._state = plugin_fixture._STATE_READY
    candidate._snapshot = snapshot
    return candidate


def _pack():
    return bind_verified_candidate(_ready_candidate(_snapshot()))


def test_binds_only_the_verified_fixed_slot_fixture_snapshot():
    snapshot = _snapshot()
    pack = bind_verified_candidate(_ready_candidate(snapshot))

    assert pack.directory == "/sd/.slots/A/functions"
    assert pack.files is FIXTURE_FILES
    assert pack.valid_selection is VALID_SELECTION
    assert pack.missing_selection is MISSING_SELECTION
    assert pack.release_id == _RELEASE_ID
    assert pack.manifest_sha256 is _DIGEST
    with pytest.raises(AttributeError, match="immutable"):
        pack._snapshot = snapshot


@pytest.mark.parametrize("changes", (
    {"root": "/sd/Add-ons"},
    {"directory": "/sd/.slots/A/Add-ons"},
    {"slot_name": "C"},
    {"release_id": "A" * 64},
    {"manifest_sha256": bytearray(_DIGEST)},
    {"digest1": b"x"},
    {"size2": 0},
))
def test_rejects_any_noncanonical_or_mutable_fixture_snapshot(changes):
    with pytest.raises(ValueError, match="snapshot"):
        bind_verified_candidate(_ready_candidate(_snapshot(**changes)))


def test_requires_a_completed_exact_fixture_candidate():
    snapshot = _snapshot()
    candidate = plugin_fixture.PluginScenarioFixtureCandidate()

    with pytest.raises(ValueError, match="unavailable"):
        bind_verified_candidate(candidate)
    with pytest.raises(ValueError, match="candidate"):
        bind_verified_candidate(object())
    with pytest.raises(ValueError, match="candidate"):
        ManagedPluginFixturePack(snapshot)

    candidate._state = plugin_fixture._STATE_READY
    candidate._snapshot = snapshot
    assert bind_verified_candidate(candidate).directory == snapshot.directory


def test_reverify_must_return_the_bound_snapshot_identity():
    pack = _pack()
    snapshot = _snapshot()
    candidate = _ready_candidate(snapshot)

    assert pack.accepts_reverified_candidate(candidate) is False

    bound = _ready_candidate(pack._snapshot)
    assert pack.accepts_reverified_candidate(bound) is True

    reverify = pack.open_reverify()
    assert type(reverify) is plugin_fixture.PluginScenarioFixtureCandidate
    assert reverify._source_snapshot is pack._snapshot


def test_valid_chain_result_requires_only_the_two_fixed_loaded_records():
    pack = _pack()
    valid = _Transaction(
        True,
        True,
        _Report(
            [
                ("_acceptance_core", 1, ""),
                ("_acceptance_dependent", 1, ""),
            ],
            [],
        ),
    )

    assert pack.valid_reload_result(valid) is True
    assert pack.missing_reload_result(valid) is False

    valid.report.loaded.append(("unmanaged", 1, ""))
    assert pack.valid_reload_result(valid) is False


def test_missing_dependency_result_requires_the_fixed_single_error():
    pack = _pack()
    missing = _Transaction(
        True,
        False,
        _Report(
            [],
            [("_acceptance_missing", "Dependency failed: _acceptance_absent")],
        ),
    )

    assert pack.missing_reload_result(missing) is True
    assert pack.valid_reload_result(missing) is False

    missing.report.errors[0] = ("_acceptance_missing", "unexpected")
    assert pack.missing_reload_result(missing) is False


def test_malformed_result_or_memory_error_never_proves_a_fixture_reload():
    pack = _pack()
    malformed = _Transaction(True, True, _Report((), []))

    assert pack.valid_reload_result(malformed) is False

    class _MemoryReport:
        @property
        def loaded(self):
            raise MemoryError("injected report OOM")

        @property
        def errors(self):
            return []

    with pytest.raises(MemoryError, match="report OOM"):
        pack.valid_reload_result(_Transaction(True, True, _MemoryReport()))
