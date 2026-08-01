"""Fail-closed adapter for the transient acceptance add-on fixture."""
from calc.limits import MAX_PLUGIN_SOURCE_BYTES
from calc.plugin_fixture import (
    PluginScenarioFixtureCandidate,
    PluginScenarioFixtureSnapshot,
)


FIXTURE_FILES = (
    "_acceptance_core.py",
    "_acceptance_dependent.py",
    "_acceptance_missing.py",
)
VALID_SELECTION = ("plugin:_acceptance_dependent",)
MISSING_SELECTION = ("plugin:_acceptance_missing",)
_FIXTURE_DIRECTORY = "/sd/_sci_accept_support/functions"


def _same_fixed_tuple(value, expected):
    if type(value) is not tuple or len(value) != len(expected):
        return False
    for index in range(len(expected)):
        if type(value[index]) is not str or value[index] != expected[index]:
            return False
    return True


def _is_digest(value):
    return type(value) is bytes and len(value) == 32


def _is_positive_source_size(value):
    return type(value) is int and 0 < value <= MAX_PLUGIN_SOURCE_BYTES


def _is_fixture_snapshot(snapshot):
    if type(snapshot) is not PluginScenarioFixtureSnapshot:
        return False
    try:
        if (snapshot.directory != _FIXTURE_DIRECTORY
                or not _same_fixed_tuple(snapshot.files, FIXTURE_FILES)
                or not _same_fixed_tuple(
                    snapshot.valid_selection, VALID_SELECTION)
                or not _same_fixed_tuple(
                    snapshot.missing_selection, MISSING_SELECTION)):
            return False
        for index in range(len(FIXTURE_FILES)):
            if (not _is_digest(snapshot.expected_digest(index))
                    or not _is_positive_source_size(
                        snapshot.expected_size(index))):
                return False
    except MemoryError:
        raise
    except Exception:
        return False
    return True


def _loaded_entry_is(entry, name):
    return (type(entry) is tuple and len(entry) == 3
            and type(entry[0]) is str and entry[0] == name)


class ManagedPluginFixturePack:
    """Immutable view of one verified, temporary fixture snapshot."""

    __slots__ = ("_snapshot", "_sealed")

    def __init__(self, candidate):
        if type(candidate) is not PluginScenarioFixtureCandidate:
            raise ValueError("invalid plugin fixture candidate")
        try:
            if candidate.available is not True:
                raise ValueError("plugin fixture candidate is unavailable")
            snapshot = candidate.snapshot
        except MemoryError:
            raise
        except ValueError:
            raise
        except Exception:
            raise ValueError("invalid plugin fixture candidate")
        if not _is_fixture_snapshot(snapshot):
            raise ValueError("invalid plugin fixture snapshot")
        self._sealed = False
        self._snapshot = snapshot
        self._sealed = True

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Managed plugin fixture pack is immutable")
        object.__setattr__(self, name, value)

    @property
    def directory(self):
        return self._snapshot.directory

    @property
    def files(self):
        return FIXTURE_FILES

    @property
    def valid_selection(self):
        return VALID_SELECTION

    @property
    def missing_selection(self):
        return MISSING_SELECTION

    def open_reverify(self):
        candidate = self._snapshot.open_reverify()
        if type(candidate) is not PluginScenarioFixtureCandidate:
            raise RuntimeError("plugin fixture reverify is unavailable")
        return candidate

    def accepts_reverified_candidate(self, candidate):
        if type(candidate) is not PluginScenarioFixtureCandidate:
            return False
        try:
            return (candidate.available is True
                    and candidate.snapshot is self._snapshot)
        except MemoryError:
            raise
        except Exception:
            return False

    def valid_reload_result(self, transaction):
        try:
            if transaction.complete is not True or transaction.succeeded is not True:
                return False
            loaded = transaction.report.loaded
            errors = transaction.report.errors
            return (
                type(loaded) is list and len(loaded) == 2
                and _loaded_entry_is(loaded[0], "_acceptance_core")
                and _loaded_entry_is(loaded[1], "_acceptance_dependent")
                and type(errors) is list and len(errors) == 0)
        except MemoryError:
            raise
        except Exception:
            return False

    def missing_reload_result(self, transaction):
        try:
            if transaction.complete is not True or transaction.succeeded is not False:
                return False
            loaded = transaction.report.loaded
            errors = transaction.report.errors
            if (type(loaded) is not list or len(loaded) != 0
                    or type(errors) is not list or len(errors) != 1):
                return False
            error = errors[0]
            return (type(error) is tuple and len(error) == 2
                    and error[0] == "_acceptance_missing"
                    and error[1]
                    == "Dependency failed: _acceptance_absent")
        except MemoryError:
            raise
        except Exception:
            return False


def bind_verified_candidate(candidate):
    return ManagedPluginFixturePack(candidate)


__all__ = (
    "FIXTURE_FILES",
    "VALID_SELECTION",
    "MISSING_SELECTION",
    "ManagedPluginFixturePack",
    "bind_verified_candidate",
)
