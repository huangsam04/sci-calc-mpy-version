"""Bounded verification for the transient acceptance add-on fixture."""
import hashlib

from calc.limits import MAX_PLUGIN_SOURCE_BYTES


_CHUNK_SIZE = 256
_FIXTURE_FILES = (
    "_acceptance_core.py",
    "_acceptance_dependent.py",
    "_acceptance_missing.py",
)
_VALID_SELECTION = ("plugin:_acceptance_dependent",)
_MISSING_SELECTION = ("plugin:_acceptance_missing",)
_TRANSIENT_FIXTURE_DIRECTORY = "/sd/_sci_accept_support/functions"

_STATE_OPEN = 0
_STATE_READ = 1
_STATE_READY = 2
_STATE_UNAVAILABLE = 3
_STATE_MEMORY_FAILED = 4
_STATE_CLOSED = 5

REASON_NONE = 0
REASON_FILE = 1
REASON_IO = 2

_transient_snapshot = None


def _path(directory, filename):
    return directory + "/" + filename


def _hash_file(path):
    chunk = bytearray(_CHUNK_SIZE)
    view = memoryview(chunk)
    digest = hashlib.sha256()
    total = 0
    stream = None
    try:
        stream = open(path, "rb")
        while True:
            count = stream.readinto(chunk)
            if not count:
                break
            total += count
            if total > MAX_PLUGIN_SOURCE_BYTES:
                raise ValueError("transient fixture file exceeds limit")
            digest.update(chunk if count == _CHUNK_SIZE else view[:count])
    finally:
        if stream is not None:
            stream.close()
    if total <= 0:
        raise ValueError("transient fixture file is empty")
    return digest.digest(), total


class PluginScenarioFixtureSnapshot:
    """Immutable per-file evidence for the uploaded acceptance fixture."""

    __slots__ = (
        "directory", "_digest0", "_digest1", "_digest2",
        "_size0", "_size1", "_size2", "_sealed")

    def __init__(self, directory, digest0, digest1, digest2,
                 size0, size1, size2):
        self._sealed = False
        self.directory = directory
        self._digest0 = digest0
        self._digest1 = digest1
        self._digest2 = digest2
        self._size0 = size0
        self._size1 = size1
        self._size2 = size2
        self._sealed = True

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Plugin fixture snapshot is immutable")
        object.__setattr__(self, name, value)

    @property
    def files(self):
        return _FIXTURE_FILES

    @property
    def valid_selection(self):
        return _VALID_SELECTION

    @property
    def missing_selection(self):
        return _MISSING_SELECTION

    def expected_digest(self, index):
        if index == 0:
            return self._digest0
        if index == 1:
            return self._digest1
        if index == 2:
            return self._digest2
        raise ValueError("invalid fixture index")

    def expected_size(self, index):
        if index == 0:
            return self._size0
        if index == 1:
            return self._size1
        if index == 2:
            return self._size2
        raise ValueError("invalid fixture index")

    def open_reverify(self):
        return PluginScenarioFixtureCandidate(self)


def configure_transient_fixture(directory):
    """Hash the three files uploaded by the existing acceptance runner."""
    global _transient_snapshot
    _transient_snapshot = None
    if directory != _TRANSIENT_FIXTURE_DIRECTORY:
        raise ValueError("invalid transient fixture directory")
    digest0, size0 = _hash_file(_path(directory, _FIXTURE_FILES[0]))
    digest1, size1 = _hash_file(_path(directory, _FIXTURE_FILES[1]))
    digest2, size2 = _hash_file(_path(directory, _FIXTURE_FILES[2]))
    _transient_snapshot = PluginScenarioFixtureSnapshot(
        directory, digest0, digest1, digest2, size0, size1, size2)
    return _transient_snapshot


class PluginScenarioFixtureCandidate:
    """Incrementally re-hash the fixed transient fixture without discovery."""

    __slots__ = (
        "_state", "_reason", "_closed", "_source_snapshot", "_snapshot",
        "_file_index", "_stream", "_hash", "_chunk", "_view", "_size")

    def __init__(self, snapshot=None):
        if snapshot is None:
            snapshot = _transient_snapshot
        if not isinstance(snapshot, PluginScenarioFixtureSnapshot):
            raise ValueError("transient fixture is not configured")
        self._state = _STATE_OPEN
        self._reason = REASON_NONE
        self._closed = False
        self._source_snapshot = snapshot
        self._snapshot = None
        self._file_index = 0
        self._stream = None
        self._hash = None
        self._chunk = None
        self._view = None
        self._size = 0

    @property
    def available(self):
        return self._state == _STATE_READY and self._snapshot is not None

    @property
    def snapshot(self):
        return self._snapshot if self.available else None

    @property
    def reason(self):
        return self._reason

    @property
    def complete(self):
        return self._state >= _STATE_READY

    def _close_stream(self):
        stream = self._stream
        if stream is not None:
            stream.close()
            self._stream = None

    def _clear_file(self):
        self._hash = None
        self._chunk = None
        self._view = None
        self._size = 0

    def _finish_unavailable(self, reason):
        try:
            self._close_stream()
        finally:
            self._clear_file()
            self._snapshot = None
            self._reason = reason
            self._state = _STATE_UNAVAILABLE
        return True

    def _memory_failed(self):
        try:
            self._close_stream()
        except Exception:
            pass
        self._clear_file()
        self._snapshot = None
        self._reason = REASON_NONE
        self._state = _STATE_MEMORY_FAILED

    def _open_file(self):
        snapshot = self._source_snapshot
        self._stream = open(
            _path(snapshot.directory, _FIXTURE_FILES[self._file_index]), "rb")
        self._hash = hashlib.sha256()
        self._chunk = bytearray(_CHUNK_SIZE)
        self._view = memoryview(self._chunk)
        self._size = 0
        self._state = _STATE_READ
        return False

    def _read_file(self):
        count = self._stream.readinto(self._chunk)
        if count:
            self._size += count
            if self._size > MAX_PLUGIN_SOURCE_BYTES:
                return self._finish_unavailable(REASON_FILE)
            self._hash.update(
                self._chunk if count == _CHUNK_SIZE else self._view[:count])
            return False

        self._close_stream()
        index = self._file_index
        snapshot = self._source_snapshot
        if (self._size != snapshot.expected_size(index)
                or self._hash.digest() != snapshot.expected_digest(index)):
            return self._finish_unavailable(REASON_FILE)
        self._clear_file()
        index += 1
        self._file_index = index
        if index < len(_FIXTURE_FILES):
            self._state = _STATE_OPEN
            return False
        self._snapshot = snapshot
        self._state = _STATE_READY
        return True

    def step(self):
        if self._closed:
            raise RuntimeError("Plugin fixture candidate is closed")
        if self.complete:
            return True
        try:
            if self._state == _STATE_OPEN:
                return self._open_file()
            return self._read_file()
        except MemoryError:
            self._memory_failed()
            raise
        except Exception:
            return self._finish_unavailable(REASON_IO)

    def close(self):
        if self._closed:
            return True
        self._close_stream()
        self._clear_file()
        self._source_snapshot = None
        self._snapshot = None
        self._closed = True
        self._state = _STATE_CLOSED
        return True


__all__ = (
    "PluginScenarioFixtureCandidate",
    "PluginScenarioFixtureSnapshot",
    "REASON_NONE",
    "REASON_FILE",
    "REASON_IO",
    "configure_transient_fixture",
)
