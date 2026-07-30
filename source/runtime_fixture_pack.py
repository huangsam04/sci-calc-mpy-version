"""Fail-closed adapter for the release-owned plug-in fixture snapshot.

The slot manifest and file hashing proof belongs to ``calc.plugin_fixture``.
This module deliberately does not discover directories, open files, or create
another candidate implementation.  It only accepts the exact verified
snapshot produced by that proof and exposes the two fixed acceptance reloads.
"""

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

_SLOT_PREFIX = "/sd/.slots/"
_FIXTURE_DIRECTORY_SUFFIX = "/functions"
_TRANSIENT_FIXTURE_DIRECTORY = "/sd/_sci_accept_support/functions"
_SLOT_NAMES = ("A", "B")
_SHA256_BYTES = 32
_RELEASE_ID_LENGTH = 64


def _same_fixed_tuple(value, expected):
    if type(value) is not tuple or len(value) != len(expected):
        return False
    for index in range(len(expected)):
        if type(value[index]) is not str or value[index] != expected[index]:
            return False
    return True


def _is_lower_hex_release_id(value):
    if type(value) is not str or len(value) != _RELEASE_ID_LENGTH:
        return False
    for character in value:
        if not ("0" <= character <= "9" or "a" <= character <= "f"):
            return False
    return True


def _is_digest(value):
    return type(value) is bytes and len(value) == _SHA256_BYTES


def _is_positive_source_size(value):
    return (type(value) is int and 0 < value <= MAX_PLUGIN_SOURCE_BYTES)


def _is_managed_snapshot(snapshot):
    """Check the fixed evidence shape without reading a target filesystem."""
    if type(snapshot) is not PluginScenarioFixtureSnapshot:
        return False
    try:
        slot_name = snapshot.slot_name
        root = snapshot.root
        if (type(slot_name) is not str
                or slot_name not in _SLOT_NAMES
                or type(root) is not str
                or root != _SLOT_PREFIX + slot_name
                or snapshot.directory not in (
                    root + _FIXTURE_DIRECTORY_SUFFIX,
                    _TRANSIENT_FIXTURE_DIRECTORY)
                or not _is_lower_hex_release_id(snapshot.release_id)
                or not _is_digest(snapshot.manifest_sha256)
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
    return (type(entry) is tuple
            and len(entry) == 3
            and type(entry[0]) is str
            and entry[0] == name)


class ManagedPluginFixturePack:
    """One immutable, identity-bound view of a verified fixture snapshot.

    Construct this only through :func:`bind_verified_candidate` after the
    existing slot-owned candidate has completed successfully.  The object
    retains no user directory or selection and cannot publish a reload.
    """

    __slots__ = ("_snapshot", "_sealed")

    def __init__(self, candidate):
        if type(candidate) is not PluginScenarioFixtureCandidate:
            raise ValueError("invalid managed plugin fixture candidate")
        try:
            if candidate.available is not True:
                raise ValueError("managed plugin fixture candidate is unavailable")
            snapshot = candidate.snapshot
        except MemoryError:
            raise
        except ValueError:
            raise
        except Exception:
            raise ValueError("invalid managed plugin fixture candidate")
        if not _is_managed_snapshot(snapshot):
            raise ValueError("invalid managed plugin fixture snapshot")
        self._sealed = False
        self._snapshot = snapshot
        self._sealed = True

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Managed plugin fixture pack is immutable")
        object.__setattr__(self, name, value)

    @property
    def directory(self):
        """Return the exact selected-slot fixture directory, never Add-ons."""
        return self._snapshot.directory

    @property
    def files(self):
        """Return the fixed three-file fixture manifest selection."""
        return FIXTURE_FILES

    @property
    def valid_selection(self):
        """Return the fixed dependent-to-core acceptance selection."""
        return VALID_SELECTION

    @property
    def missing_selection(self):
        """Return the fixed missing-dependency rejection selection."""
        return MISSING_SELECTION

    @property
    def release_id(self):
        return self._snapshot.release_id

    @property
    def manifest_sha256(self):
        return self._snapshot.manifest_sha256

    def open_reverify(self):
        """Delegate a later file-only proof to the original snapshot."""
        candidate = self._snapshot.open_reverify()
        if type(candidate) is not PluginScenarioFixtureCandidate:
            raise RuntimeError("managed fixture reverify is unavailable")
        return candidate

    def accepts_reverified_candidate(self, candidate):
        """Accept only a successful reverify that returns this same snapshot."""
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
        """Prove exactly the fixed dependent/core success result in O(1)."""
        try:
            if transaction.complete is not True or transaction.succeeded is not True:
                return False
            report = transaction.report
            loaded = report.loaded
            errors = report.errors
            return (
                type(loaded) is list
                and len(loaded) == 2
                and _loaded_entry_is(loaded[0], "_acceptance_core")
                and _loaded_entry_is(loaded[1], "_acceptance_dependent")
                and type(errors) is list
                and len(errors) == 0)
        except MemoryError:
            raise
        except Exception:
            return False

    def missing_reload_result(self, transaction):
        """Prove exactly the fixed missing-dependency rejection in O(1)."""
        try:
            if transaction.complete is not True or transaction.succeeded is not False:
                return False
            report = transaction.report
            loaded = report.loaded
            errors = report.errors
            if (type(loaded) is not list or len(loaded) != 0
                    or type(errors) is not list or len(errors) != 1):
                return False
            error = errors[0]
            return (type(error) is tuple
                    and len(error) == 2
                    and type(error[0]) is str
                    and type(error[1]) is str
                    and error[0] == "_acceptance_missing"
                    and error[1] == "Dependency failed: _acceptance_absent")
        except MemoryError:
            raise
        except Exception:
            return False


def bind_verified_candidate(candidate):
    """Bind one pack only after the existing bounded candidate is ready."""
    return ManagedPluginFixturePack(candidate)


__all__ = (
    "FIXTURE_FILES",
    "VALID_SELECTION",
    "MISSING_SELECTION",
    "ManagedPluginFixturePack",
    "bind_verified_candidate",
)
