"""Bounded proof that the acceptance plug-in fixture pack is slot-owned."""
import hashlib
import os

from calc.limits import MAX_PLUGIN_SOURCE_BYTES


_CHUNK_SIZE = 256
_RELEASE_ID_LENGTH = 64
_SHA256_BYTES = 32
_SLOT_BASE = "/sd/.slots"
_SLOT_NAMES = ("A", "B")
_FIXTURE_FILES = (
    "_acceptance_core.py",
    "_acceptance_dependent.py",
    "_acceptance_missing.py",
)
_FIXTURE_KEYS = (
    b"sd:functions/_acceptance_core",
    b"sd:functions/_acceptance_dependent",
    b"sd:functions/_acceptance_missing",
)
_FIXTURE_PATHS = (
    b"functions/_acceptance_core.py",
    b"functions/_acceptance_dependent.py",
    b"functions/_acceptance_missing.py",
)
_VALID_SELECTION = ("plugin:_acceptance_dependent",)
_MISSING_SELECTION = ("plugin:_acceptance_missing",)

# These bounds cover the current release manifest while putting a fixed limit
# on malformed SD input.  They are deliberately independent of directory
# discovery and never grow a JSON tree.
_MAX_MANIFEST_BYTES = 65536
_MAX_MANIFEST_READS = 320
_MAX_MANIFEST_RECORDS = 256
_MAX_MANIFEST_TOKENS = 4096
_MAX_MANIFEST_STRING_BYTES = 96
_MAX_ASSET_SIZE_DIGITS = 8
_MAX_FILE_READS = (MAX_PLUGIN_SOURCE_BYTES // _CHUNK_SIZE) + 2

# A schema-1 boot-log record contains a 30-byte header, one optional slot
# reference (name, byte length, release id and manifest digest), then its
# record digest.  The length byte caps the encoded release id at 255 bytes.
# Read one byte beyond that maximum so an oversized `/sys/boot.*` file is
# rejected before it can reach the bootlog codec.
_BOOT_RECORD_HEADER_BYTES = 30
_BOOT_RECORD_MAX_RELEASE_ID_BYTES = 255
_BOOT_RECORD_MAX_BYTES = (
    _BOOT_RECORD_HEADER_BYTES + 2 + _BOOT_RECORD_MAX_RELEASE_ID_BYTES
    + _SHA256_BYTES * 2)
_BOOT_RECORD_BUFFER_BYTES = _BOOT_RECORD_MAX_BYTES + 1

_STATE_IDENTITY = 0
_STATE_MANIFEST_OPEN = 1
_STATE_MANIFEST_STREAM = 2
_STATE_FILE_STAT = 3
_STATE_FILE_OPEN = 4
_STATE_FILE_HASH = 5
_STATE_READY = 6
_STATE_UNAVAILABLE = 7
_STATE_MEMORY_FAILED = 8
_STATE_CLOSED = 9

REASON_NONE = 0
REASON_SLOT = 1
REASON_MANIFEST = 2
REASON_RECORD = 3
REASON_FILE = 4
REASON_IO = 5

_STRING_MORE = 0
_STRING_DONE = 1
_STRING_BAD = 2
_RECORD_MORE = 0
_RECORD_DONE = 1
_RECORD_BAD = 2


def _lower_ascii(value):
    if 65 <= value <= 90:
        return value + 32
    return value


def _hex_value(value):
    if 48 <= value <= 57:
        return value - 48
    if 97 <= value <= 102:
        return value - 87
    return -1


def _same_bytes(left, right):
    if len(left) != len(right):
        return False
    for index in range(len(left)):
        if left[index] != right[index]:
            return False
    return True


def _is_lower_hex(value):
    if not isinstance(value, str) or len(value) != _RELEASE_ID_LENGTH:
        return False
    for character in value:
        if not ("0" <= character <= "9" or "a" <= character <= "f"):
            return False
    return True


def _close_bounded_boot_stream(stream):
    """Close a boot stream at most twice without retaining its fault."""
    try:
        stream.close()
        return True
    except MemoryError:
        # Preserve this exact cleanup OOM even if the bounded retry also
        # faults.  Callers that already have a primary OOM suppress it.
        try:
            stream.close()
        except Exception:
            pass
        raise
    except Exception:
        # A normal close fault gets one local retry only to release the stream.
        # Even a successful retry invalidates this decoded record: its close
        # acknowledgement was not trustworthy.  A retry OOM remains exact.
        try:
            stream.close()
        except MemoryError:
            raise
        except Exception:
            return False
        return False


def _read_bounded_boot_record(path, unpack_record):
    """Decode one fixed-size boot record with bounded readinto calls."""
    try:
        stream = open(path, "rb")
    except OSError:
        return None

    ordinary_failure = False
    try:
        buffer = bytearray(_BOOT_RECORD_BUFFER_BYTES)
        count = stream.readinto(buffer)
        if count is None:
            entry = None
        elif (isinstance(count, bool) or not isinstance(count, int)
              or count <= 0 or count >= _BOOT_RECORD_BUFFER_BYTES):
            entry = None
        else:
            # A normal boot record is a tiny regular file.  The EOF probe
            # rejects a short first read followed by replacement or extra data.
            view = memoryview(buffer)
            extra = stream.readinto(view[:1])
            if extra is None:
                extra = 0
            if (isinstance(extra, bool) or not isinstance(extra, int)
                    or extra < 0 or extra > 1 or extra != 0):
                entry = None
            else:
                entry = unpack_record(bytes(view[:count]))
    except MemoryError:
        # Do not let either close attempt hide a primary read/parse OOM.
        try:
            _close_bounded_boot_stream(stream)
        except Exception:
            pass
        raise
    except Exception:
        # Do not retain the ordinary exception.  Close after this handler so a
        # cleanup OOM also has no retained secondary exception context.
        ordinary_failure = True

    if ordinary_failure:
        # For an ordinary read/parse failure, a cleanup OOM is the exact
        # primary signal.  Ordinary cleanup faults are retried locally.
        _close_bounded_boot_stream(stream)
        return None

    if not _close_bounded_boot_stream(stream):
        return None
    return entry


def _bounded_bootlog_winner(paths, unpack_record):
    """Keep the newest valid dual-record boot evidence entry."""
    best = None
    for path in paths:
        entry = _read_bounded_boot_record(path, unpack_record)
        if entry is not None and (best is None
                                  or entry.generation > best.generation):
            best = entry
    return best


def _active_slot_evidence():
    """Read the boot-selected slot; never infer one from the selector."""
    from approot import app_root
    import bootenv
    import bootlog

    root = app_root()
    entry = _bounded_bootlog_winner(
        bootenv.BOOTLOG_PATHS, bootlog.unpack_record)
    selected = entry.selected if entry is not None else None
    return root, selected, bootenv.SLOT_BASE, bootenv.MANIFEST_NAME


class _BoundedJsonString:
    """Parse one JSON string without allocating its decoded value."""

    __slots__ = (
        "_limit", "_buffer", "_started", "_escaped", "_unicode",
        "_bytes", "_length", "_plain", "_capture")

    def __init__(self, limit, buffer):
        self._limit = limit
        self._buffer = buffer
        self.reset(False)

    def reset(self, capture):
        self._started = False
        self._escaped = False
        self._unicode = 0
        self._bytes = 0
        self._length = 0
        self._plain = True
        self._capture = capture

    def feed(self, value):
        if not self._started:
            if value != 34:
                return _STRING_BAD
            self._started = True
            return _STRING_MORE
        if self._unicode:
            if _hex_value(value) < 0:
                return _STRING_BAD
            self._bytes += 1
            if self._bytes > self._limit:
                return _STRING_BAD
            self._unicode -= 1
            if self._unicode == 0:
                self._escaped = False
            return _STRING_MORE
        if self._escaped:
            self._bytes += 1
            if self._bytes > self._limit:
                return _STRING_BAD
            if value == ord("u"):
                self._unicode = 4
                return _STRING_MORE
            if not (value == 34 or value == 47 or value == 92
                    or value == 98 or value == 102 or value == 110
                    or value == 114 or value == 116):
                return _STRING_BAD
            self._escaped = False
            return _STRING_MORE
        if value == 34:
            return _STRING_DONE
        if value < 32:
            return _STRING_BAD
        self._bytes += 1
        if self._bytes > self._limit:
            return _STRING_BAD
        if value == 92:
            self._plain = False
            self._escaped = True
            return _STRING_MORE
        if self._capture:
            if self._length >= len(self._buffer):
                return _STRING_BAD
            self._buffer[self._length] = value
        self._length += 1
        return _STRING_MORE

    def matches(self, expected):
        if (not self._capture or not self._plain
                or self._length != len(expected)):
            return False
        for index in range(self._length):
            if self._buffer[index] != expected[index]:
                return False
        return True

    def matches_text(self, expected):
        if (not self._capture or not self._plain
                or self._length != len(expected)):
            return False
        for index in range(self._length):
            if self._buffer[index] != ord(expected[index]):
                return False
        return True

    def folded_matches(self, expected):
        if (not self._capture or not self._plain
                or self._length != len(expected)):
            return False
        for index in range(self._length):
            if _lower_ascii(self._buffer[index]) != _lower_ascii(expected[index]):
                return False
        return True


class _AssetRecordParser:
    """Strict parser for one canonical release asset record."""

    __slots__ = (
        "_state", "_literal", "_literal_position", "_next_state",
        "_string", "_field", "_digest", "_digest_position",
        "_digest_high", "_size", "_size_digits", "_size_zero",
        "_fixture_index", "_allow_fixture", "_format_source",
        "_role_managed", "_zone_sd", "_complete_index", "_tokens",
        "_exact_index", "_folded_index")

    _START = 0
    _FORMAT = 1
    _KEY = 2
    _PATH = 3
    _ROLE = 4
    _DIGEST_OPEN = 5
    _DIGEST = 6
    _SIZE = 7
    _ZONE = 8
    _FINISH = 9
    _DONE = 10

    def __init__(self):
        self._field = bytearray(_MAX_MANIFEST_STRING_BYTES)
        self._string = _BoundedJsonString(
            _MAX_MANIFEST_STRING_BYTES, self._field)
        self._digest = bytearray(_SHA256_BYTES)
        self.reset(True)

    def reset(self, allow_fixture):
        self._state = self._START
        self._literal = None
        self._literal_position = 0
        self._next_state = self._START
        self._string.reset(False)
        self._digest_position = 0
        self._digest_high = -1
        self._size = 0
        self._size_digits = 0
        self._size_zero = False
        self._fixture_index = -1
        self._allow_fixture = allow_fixture
        self._format_source = False
        self._role_managed = False
        self._zone_sd = False
        self._complete_index = -1
        self._tokens = 0
        self._exact_index = -1
        self._folded_index = -1

    @property
    def fixture_index(self):
        return self._complete_index

    @property
    def size(self):
        return self._size

    @property
    def digest(self):
        return self._digest

    def take_tokens(self):
        tokens = self._tokens
        self._tokens = 0
        return tokens

    def _set_literal(self, literal, next_state):
        self._literal = literal
        self._literal_position = 0
        self._next_state = next_state

    def _feed_literal(self, value):
        literal = self._literal
        position = self._literal_position
        if value != literal[position]:
            return False
        position += 1
        if position != len(literal):
            self._literal_position = position
            return True
        self._literal = None
        self._literal_position = 0
        self._state = self._next_state
        self._tokens += 1
        if (self._state == self._FORMAT or self._state == self._KEY
                or self._state == self._PATH or self._state == self._ROLE
                or self._state == self._ZONE):
            self._string.reset(True)
        elif self._state == self._DIGEST_OPEN:
            self._digest_position = 0
            self._digest_high = -1
        elif self._state == self._SIZE:
            self._size = 0
            self._size_digits = 0
            self._size_zero = False
        return True

    def _fixture_match(self, candidates):
        self._exact_index = -1
        self._folded_index = -1
        for index in range(len(candidates)):
            candidate = candidates[index]
            if self._string.matches(candidate):
                self._exact_index = index
            if self._string.folded_matches(candidate):
                self._folded_index = index

    def _finish_fixture_key(self):
        self._fixture_match(_FIXTURE_KEYS)
        exact = self._exact_index
        folded = self._folded_index
        if folded >= 0 and exact != folded:
            return False
        if exact >= 0:
            if not self._allow_fixture:
                return False
            self._fixture_index = exact
        return True

    def _finish_fixture_path(self):
        self._fixture_match(_FIXTURE_PATHS)
        exact = self._exact_index
        folded = self._folded_index
        if folded >= 0 and exact != folded:
            return False
        if exact >= 0:
            if not self._allow_fixture or self._fixture_index != exact:
                return False
        elif self._fixture_index >= 0:
            return False
        return True

    def _finish_record(self):
        index = self._fixture_index
        if index >= 0:
            if (not self._format_source or not self._role_managed
                    or not self._zone_sd or self._size <= 0
                    or self._size > MAX_PLUGIN_SOURCE_BYTES):
                return False
            self._complete_index = index
        self._state = self._DONE
        self._tokens += 1
        return True

    def feed(self, value):
        literal = self._literal
        if literal is not None:
            if not self._feed_literal(value):
                return _RECORD_BAD
            if self._state == self._FINISH:
                return (_RECORD_DONE if self._finish_record()
                        else _RECORD_BAD)
            return _RECORD_MORE

        state = self._state
        if state == self._START:
            if value != 123:
                return _RECORD_BAD
            self._tokens += 1
            self._set_literal(b'"format":', self._FORMAT)
            return _RECORD_MORE
        if (state == self._FORMAT or state == self._KEY
                or state == self._PATH or state == self._ROLE
                or state == self._ZONE):
            result = self._string.feed(value)
            if result == _STRING_BAD:
                return _RECORD_BAD
            if result != _STRING_DONE:
                return _RECORD_MORE
            self._tokens += 1
            if state == self._FORMAT:
                self._format_source = self._string.matches(b"source")
                self._set_literal(b',"key":', self._KEY)
            elif state == self._KEY:
                if not self._finish_fixture_key():
                    return _RECORD_BAD
                self._set_literal(b',"path":', self._PATH)
            elif state == self._PATH:
                if not self._finish_fixture_path():
                    return _RECORD_BAD
                self._set_literal(b',"role":', self._ROLE)
            elif state == self._ROLE:
                self._role_managed = self._string.matches(b"managed_release")
                self._set_literal(b',"sha256":', self._DIGEST_OPEN)
            else:
                self._zone_sd = self._string.matches(b"sd")
                self._set_literal(b'}', self._FINISH)
            return _RECORD_MORE
        if state == self._DIGEST_OPEN:
            if value != 34:
                return _RECORD_BAD
            self._state = self._DIGEST
            return _RECORD_MORE
        if state == self._DIGEST:
            if self._digest_position < _SHA256_BYTES * 2:
                digit = _hex_value(value)
                if digit < 0:
                    return _RECORD_BAD
                if self._fixture_index >= 0:
                    if self._digest_position & 1:
                        self._digest[self._digest_position // 2] = (
                            (self._digest_high << 4) | digit)
                    else:
                        self._digest_high = digit
                self._digest_position += 1
                return _RECORD_MORE
            if value != 34:
                return _RECORD_BAD
            self._tokens += 1
            self._set_literal(b',"size":', self._SIZE)
            return _RECORD_MORE
        if state == self._SIZE:
            if 48 <= value <= 57:
                if self._size_digits >= _MAX_ASSET_SIZE_DIGITS:
                    return _RECORD_BAD
                if self._size_digits == 0:
                    self._size_zero = value == 48
                elif self._size_zero:
                    return _RECORD_BAD
                self._size = self._size * 10 + value - 48
                self._size_digits += 1
                return _RECORD_MORE
            if value != 44 or self._size_digits == 0:
                return _RECORD_BAD
            if (self._fixture_index >= 0
                    and (self._size <= 0
                         or self._size > MAX_PLUGIN_SOURCE_BYTES)):
                return _RECORD_BAD
            self._tokens += 1
            self._set_literal(b'"zone":', self._ZONE)
            return _RECORD_MORE
        return _RECORD_BAD


class _ManifestFixtureScanner:
    """Strict, fixed-state parser for the canonical release manifest."""

    __slots__ = (
        "_release_id", "_state", "_literal", "_literal_position",
        "_next_state", "_string", "_top_value", "_asset",
        "_record_count", "_token_count", "_fixture_count0",
        "_fixture_count1", "_fixture_count2", "_digest0", "_digest1",
        "_digest2", "_size0", "_size1", "_size2", "_invalid")

    _ABI = 0
    _APP_VERSION = 1
    _ASSETS_EXPECT = 2
    _ASSETS_RECORD = 3
    _ASSETS_DELIMITER = 4
    _MODE = 5
    _PRODUCT = 6
    _RELEASE_ID = 7
    _SCHEMA = 8
    _SEEDS_EXPECT = 9
    _SEEDS_RECORD = 10
    _SEEDS_DELIMITER = 11
    _DONE = 12

    def __init__(self, release_id):
        self._top_value = bytearray(_MAX_MANIFEST_STRING_BYTES)
        self._string = _BoundedJsonString(
            _MAX_MANIFEST_STRING_BYTES, self._top_value)
        self._asset = _AssetRecordParser()
        self._digest0 = bytearray(_SHA256_BYTES)
        self._digest1 = bytearray(_SHA256_BYTES)
        self._digest2 = bytearray(_SHA256_BYTES)
        self._release_id = release_id
        self._state = self._ABI
        self._literal = b'{"abi_tag":'
        self._literal_position = 0
        self._next_state = self._ABI
        self._record_count = 0
        self._token_count = 0
        self._fixture_count0 = 0
        self._fixture_count1 = 0
        self._fixture_count2 = 0
        self._size0 = 0
        self._size1 = 0
        self._size2 = 0
        self._invalid = False

    def _add_tokens(self, amount):
        self._token_count += amount
        if self._token_count > _MAX_MANIFEST_TOKENS:
            self._invalid = True

    def _set_literal(self, literal, next_state):
        self._literal = literal
        self._literal_position = 0
        self._next_state = next_state

    def _enter_state(self, state):
        self._state = state
        if state == self._ABI or state == self._APP_VERSION:
            self._string.reset(False)
        elif (state == self._MODE or state == self._PRODUCT
              or state == self._RELEASE_ID):
            self._string.reset(True)

    def _feed_literal(self, value):
        literal = self._literal
        position = self._literal_position
        if value != literal[position]:
            self._invalid = True
            return
        position += 1
        if position != len(literal):
            self._literal_position = position
            return
        self._literal = None
        self._literal_position = 0
        self._add_tokens(1)
        self._enter_state(self._next_state)

    def _store_fixture(self, index):
        source = self._asset.digest
        if index == 0:
            self._fixture_count0 += 1
            target = self._digest0
            self._size0 = self._asset.size
        elif index == 1:
            self._fixture_count1 += 1
            target = self._digest1
            self._size1 = self._asset.size
        else:
            self._fixture_count2 += 1
            target = self._digest2
            self._size2 = self._asset.size
        for offset in range(_SHA256_BYTES):
            target[offset] = source[offset]

    def _start_asset(self, allow_fixture):
        if self._record_count >= _MAX_MANIFEST_RECORDS:
            self._invalid = True
            return
        self._record_count += 1
        self._asset.reset(allow_fixture)
        result = self._asset.feed(123)
        self._add_tokens(self._asset.take_tokens())
        if result == _RECORD_BAD:
            self._invalid = True

    def _feed_asset(self, value, next_delimiter):
        result = self._asset.feed(value)
        self._add_tokens(self._asset.take_tokens())
        if result == _RECORD_BAD:
            self._invalid = True
            return
        if result != _RECORD_DONE:
            return
        index = self._asset.fixture_index
        if index >= 0:
            self._store_fixture(index)
        self._state = next_delimiter

    def _finish_top_string(self, state):
        if state == self._ABI:
            self._set_literal(b',"app_version":', self._APP_VERSION)
        elif state == self._APP_VERSION:
            self._set_literal(b',"assets":[', self._ASSETS_EXPECT)
        elif state == self._MODE:
            if not (self._string.matches(b"source")
                    or self._string.matches(b"mpy")):
                self._invalid = True
                return
            self._set_literal(b',"product":', self._PRODUCT)
        elif state == self._PRODUCT:
            if not self._string.matches(b"sci-calc"):
                self._invalid = True
                return
            self._set_literal(b',"release_id":', self._RELEASE_ID)
        else:
            if not self._string.matches_text(self._release_id):
                self._invalid = True
                return
            self._set_literal(b',"schema":', self._SCHEMA)

    def feed(self, view, count):
        for index in range(count):
            if self._invalid:
                return False
            value = view[index]
            literal = self._literal
            if literal is not None:
                self._feed_literal(value)
                continue
            state = self._state
            if (state == self._ABI or state == self._APP_VERSION
                    or state == self._MODE or state == self._PRODUCT
                    or state == self._RELEASE_ID):
                result = self._string.feed(value)
                if result == _STRING_BAD:
                    self._invalid = True
                elif result == _STRING_DONE:
                    self._add_tokens(1)
                    self._finish_top_string(state)
                continue
            if state == self._ASSETS_EXPECT:
                if value == 93:
                    self._set_literal(b',"mode":', self._MODE)
                elif value == 123:
                    self._start_asset(True)
                    self._state = self._ASSETS_RECORD
                else:
                    self._invalid = True
                continue
            if state == self._ASSETS_RECORD:
                self._feed_asset(value, self._ASSETS_DELIMITER)
                continue
            if state == self._ASSETS_DELIMITER:
                if value == 44:
                    self._state = self._ASSETS_EXPECT
                elif value == 93:
                    self._set_literal(b',"mode":', self._MODE)
                else:
                    self._invalid = True
                continue
            if state == self._SCHEMA:
                if value != 49:
                    self._invalid = True
                else:
                    self._add_tokens(1)
                    self._set_literal(b',"seeds":[', self._SEEDS_EXPECT)
                continue
            if state == self._SEEDS_EXPECT:
                if value == 93:
                    self._set_literal(b'}', self._DONE)
                elif value == 123:
                    self._start_asset(False)
                    self._state = self._SEEDS_RECORD
                else:
                    self._invalid = True
                continue
            if state == self._SEEDS_RECORD:
                self._feed_asset(value, self._SEEDS_DELIMITER)
                continue
            if state == self._SEEDS_DELIMITER:
                if value == 44:
                    self._state = self._SEEDS_EXPECT
                elif value == 93:
                    self._set_literal(b'}', self._DONE)
                else:
                    self._invalid = True
                continue
            self._invalid = True
        return not self._invalid

    def valid(self):
        return (
            not self._invalid
            and self._state == self._DONE
            and self._literal is None
            and self._fixture_count0 == 1
            and self._fixture_count1 == 1
            and self._fixture_count2 == 1)

    def fixture_digest(self, index):
        if index == 0:
            return bytes(self._digest0)
        if index == 1:
            return bytes(self._digest1)
        if index == 2:
            return bytes(self._digest2)
        raise ValueError("invalid fixture index")

    def fixture_size(self, index):
        if index == 0:
            return self._size0
        if index == 1:
            return self._size1
        if index == 2:
            return self._size2
        raise ValueError("invalid fixture index")


class PluginScenarioFixtureSnapshot:
    """Immutable slot and per-file evidence for a later bounded reverify."""

    __slots__ = (
        "root", "directory", "slot_name", "release_id", "manifest_sha256",
        "_digest0", "_digest1", "_digest2", "_size0", "_size1", "_size2",
        "_sealed")

    def __init__(self, root, directory, slot_name, release_id,
                 manifest_sha256, digest0, digest1, digest2,
                 size0, size1, size2):
        self._sealed = False
        self.root = root
        self.directory = directory
        self.slot_name = slot_name
        self.release_id = release_id
        self.manifest_sha256 = manifest_sha256
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
        """Return a bounded file-only proof bound to this exact boot slot."""
        return PluginScenarioFixtureCandidate(self)


class PluginScenarioFixtureCandidate:
    """Incrementally verify the release-owned fixture pack for one slot."""

    __slots__ = (
        "_state", "_reason", "_closed", "_root", "_directory",
        "_slot_name", "_release_id", "_manifest_sha256", "_manifest_name",
        "_stream", "_hash", "_chunk", "_view", "_scanner", "_file_index",
        "_expected_digest0", "_expected_digest1", "_expected_digest2",
        "_expected_size0", "_expected_size1", "_expected_size2", "_snapshot",
        "_source_snapshot", "_stream_reads", "_short_read", "_manifest_bytes",
        "_file_bytes")

    def __init__(self, snapshot=None):
        if (snapshot is not None
                and not isinstance(snapshot, PluginScenarioFixtureSnapshot)):
            raise ValueError("invalid fixture snapshot")
        self._state = _STATE_IDENTITY
        self._reason = REASON_NONE
        self._closed = False
        self._root = None
        self._directory = None
        self._slot_name = None
        self._release_id = None
        self._manifest_sha256 = None
        self._manifest_name = None
        self._stream = None
        self._hash = None
        self._chunk = None
        self._view = None
        self._scanner = None
        self._file_index = 0
        self._expected_digest0 = None
        self._expected_digest1 = None
        self._expected_digest2 = None
        self._expected_size0 = 0
        self._expected_size1 = 0
        self._expected_size2 = 0
        self._snapshot = None
        self._source_snapshot = snapshot
        self._stream_reads = 0
        self._short_read = False
        self._manifest_bytes = 0
        self._file_bytes = 0

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

    def _clear_scratch(self):
        self._hash = None
        self._chunk = None
        self._view = None
        self._scanner = None
        self._root = None
        self._directory = None
        self._slot_name = None
        self._release_id = None
        self._manifest_sha256 = None
        self._manifest_name = None
        self._expected_digest0 = None
        self._expected_digest1 = None
        self._expected_digest2 = None
        self._expected_size0 = 0
        self._expected_size1 = 0
        self._expected_size2 = 0
        self._source_snapshot = None
        self._stream_reads = 0
        self._short_read = False
        self._manifest_bytes = 0
        self._file_bytes = 0

    def _unavailable(self, reason):
        try:
            self._close_stream()
        except MemoryError:
            # The ordinary terminal reason remains observable, but the exact
            # cleanup OOM must reach the caller and leave the stream retryable.
            self._finish_unavailable(reason)
            raise
        except Exception:
            # Do not retain a secondary exception object.  The failed stream
            # stays owned by this candidate so close() can retry it later.
            return self._finish_unavailable(reason)
        return self._finish_unavailable(reason)

    def _finish_unavailable(self, reason):
        self._clear_scratch()
        self._reason = reason
        self._state = _STATE_UNAVAILABLE
        return True

    def _memory_failed(self):
        try:
            self._close_stream()
        except Exception:
            # A primary OOM is more important than an ordinary or OOM cleanup
            # fault.  _close_stream leaves the failed stream for close() retry.
            pass
        self._clear_scratch()
        self._reason = REASON_NONE
        self._state = _STATE_MEMORY_FAILED

    def _prepare_buffer(self):
        self._chunk = bytearray(_CHUNK_SIZE)
        self._view = memoryview(self._chunk)
        self._stream_reads = 0
        self._short_read = False

    def _copy_snapshot_evidence(self, snapshot):
        self._expected_digest0 = snapshot.expected_digest(0)
        self._expected_digest1 = snapshot.expected_digest(1)
        self._expected_digest2 = snapshot.expected_digest(2)
        self._expected_size0 = snapshot.expected_size(0)
        self._expected_size1 = snapshot.expected_size(1)
        self._expected_size2 = snapshot.expected_size(2)

    def _identity_step(self):
        root, selected, slot_base, manifest_name = _active_slot_evidence()
        if (not isinstance(root, str)
                or selected is None
                or slot_base != _SLOT_BASE
                or manifest_name != "release.manifest"):
            return self._unavailable(REASON_SLOT)
        name = getattr(selected, "name", None)
        release_id = getattr(selected, "release_id", None)
        manifest_sha256 = getattr(selected, "manifest_sha256", None)
        if (name not in _SLOT_NAMES
                or not _is_lower_hex(release_id)
                or not isinstance(manifest_sha256, (bytes, bytearray))
                or len(manifest_sha256) != _SHA256_BYTES):
            return self._unavailable(REASON_SLOT)
        expected_root = slot_base + "/" + name
        if root != expected_root:
            return self._unavailable(REASON_SLOT)
        source_snapshot = self._source_snapshot
        if source_snapshot is not None:
            if (root != source_snapshot.root
                    or name != source_snapshot.slot_name
                    or release_id != source_snapshot.release_id
                    or not _same_bytes(
                        manifest_sha256, source_snapshot.manifest_sha256)):
                return self._unavailable(REASON_SLOT)
            self._root = root
            self._directory = source_snapshot.directory
            self._slot_name = name
            self._release_id = release_id
            self._manifest_sha256 = source_snapshot.manifest_sha256
            self._manifest_name = manifest_name
            self._copy_snapshot_evidence(source_snapshot)
            self._prepare_buffer()
            self._file_index = 0
            self._state = _STATE_FILE_STAT
            return False
        self._root = root
        self._directory = root + "/functions"
        self._slot_name = name
        self._release_id = release_id
        self._manifest_sha256 = bytes(manifest_sha256)
        self._manifest_name = manifest_name
        self._prepare_buffer()
        self._state = _STATE_MANIFEST_OPEN
        return False

    def _open_manifest_step(self):
        self._stream = open(
            self._root + "/" + self._manifest_name, "rb")
        self._hash = hashlib.sha256()
        self._scanner = _ManifestFixtureScanner(self._release_id)
        self._stream_reads = 0
        self._short_read = False
        self._manifest_bytes = 0
        self._state = _STATE_MANIFEST_STREAM
        return False

    def _read_chunk(self, maximum_reads):
        count = self._stream.readinto(self._chunk)
        if count is None:
            return 0
        if (isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0 or count > _CHUNK_SIZE):
            raise ValueError("invalid fixture stream read")
        if count:
            self._stream_reads += 1
            if self._stream_reads > maximum_reads:
                raise ValueError("fixture stream step limit")
        return count

    def _hash_chunk(self, count):
        if self._short_read:
            raise ValueError("non-final short fixture stream read")
        if count == _CHUNK_SIZE:
            self._hash.update(self._view)
            return
        # Regular FAT files fill this reusable view until EOF.  A final short
        # chunk needs one bounded view object; another data chunk then fails.
        self._short_read = True
        self._hash.update(self._view[:count])

    def _manifest_stream_step(self):
        count = self._read_chunk(_MAX_MANIFEST_READS)
        if count:
            self._manifest_bytes += count
            if self._manifest_bytes > _MAX_MANIFEST_BYTES:
                return self._unavailable(REASON_MANIFEST)
            self._hash_chunk(count)
            scanner = self._scanner
            if scanner is None or not scanner.feed(self._view, count):
                return self._unavailable(REASON_RECORD)
            return False
        scanner = self._scanner
        if self._hash.digest() != self._manifest_sha256:
            return self._unavailable(REASON_MANIFEST)
        if scanner is None or not scanner.valid():
            return self._unavailable(REASON_RECORD)
        self._close_stream()
        self._expected_digest0 = scanner.fixture_digest(0)
        self._expected_digest1 = scanner.fixture_digest(1)
        self._expected_digest2 = scanner.fixture_digest(2)
        self._expected_size0 = scanner.fixture_size(0)
        self._expected_size1 = scanner.fixture_size(1)
        self._expected_size2 = scanner.fixture_size(2)
        self._scanner = None
        self._hash = None
        self._file_index = 0
        self._state = _STATE_FILE_STAT
        return False

    def _fixture_filename(self, index):
        if index == 0:
            return _FIXTURE_FILES[0]
        if index == 1:
            return _FIXTURE_FILES[1]
        if index == 2:
            return _FIXTURE_FILES[2]
        raise ValueError("invalid fixture index")

    def _expected_size(self, index):
        if index == 0:
            return self._expected_size0
        if index == 1:
            return self._expected_size1
        if index == 2:
            return self._expected_size2
        raise ValueError("invalid fixture index")

    def _expected_digest(self, index):
        if index == 0:
            return self._expected_digest0
        if index == 1:
            return self._expected_digest1
        if index == 2:
            return self._expected_digest2
        raise ValueError("invalid fixture index")

    def _fixture_path(self, index):
        return self._directory + "/" + self._fixture_filename(index)

    def _file_stat_step(self):
        stat = os.stat(self._fixture_path(self._file_index))
        size = getattr(stat, "st_size", None)
        if size is None:
            size = stat[6]
        if size != self._expected_size(self._file_index):
            return self._unavailable(REASON_FILE)
        self._state = _STATE_FILE_OPEN
        return False

    def _file_open_step(self):
        self._stream = open(self._fixture_path(self._file_index), "rb")
        self._hash = hashlib.sha256()
        self._stream_reads = 0
        self._short_read = False
        self._file_bytes = 0
        self._state = _STATE_FILE_HASH
        return False

    def _file_hash_step(self):
        count = self._read_chunk(_MAX_FILE_READS)
        if count:
            self._file_bytes += count
            if self._file_bytes > self._expected_size(self._file_index):
                return self._unavailable(REASON_FILE)
            self._hash_chunk(count)
            return False
        if (self._file_bytes != self._expected_size(self._file_index)
                or self._hash.digest()
                != self._expected_digest(self._file_index)):
            return self._unavailable(REASON_FILE)
        self._close_stream()
        self._hash = None
        self._file_index += 1
        if self._file_index < len(_FIXTURE_FILES):
            self._state = _STATE_FILE_STAT
            return False
        source_snapshot = self._source_snapshot
        if source_snapshot is None:
            self._snapshot = PluginScenarioFixtureSnapshot(
                self._root, self._directory, self._slot_name, self._release_id,
                self._manifest_sha256, self._expected_digest0,
                self._expected_digest1, self._expected_digest2,
                self._expected_size0, self._expected_size1,
                self._expected_size2)
        else:
            self._snapshot = source_snapshot
        self._clear_scratch()
        self._reason = REASON_NONE
        self._state = _STATE_READY
        return True

    def _ordinary_reason(self):
        state = self._state
        if state == _STATE_IDENTITY:
            return REASON_SLOT
        if state in (_STATE_MANIFEST_OPEN, _STATE_MANIFEST_STREAM):
            return REASON_MANIFEST
        if state in (_STATE_FILE_STAT, _STATE_FILE_OPEN, _STATE_FILE_HASH):
            return REASON_FILE
        return REASON_IO

    def step(self):
        """Advance exactly one bounded slot-proof unit and return terminal."""
        if self._closed:
            raise RuntimeError("Plugin fixture candidate is closed")
        if self.complete:
            return True
        try:
            state = self._state
            if state == _STATE_IDENTITY:
                return self._identity_step()
            if state == _STATE_MANIFEST_OPEN:
                return self._open_manifest_step()
            if state == _STATE_MANIFEST_STREAM:
                return self._manifest_stream_step()
            if state == _STATE_FILE_STAT:
                return self._file_stat_step()
            if state == _STATE_FILE_OPEN:
                return self._file_open_step()
            if state == _STATE_FILE_HASH:
                return self._file_hash_step()
            return self._unavailable(REASON_IO)
        except MemoryError:
            # _unavailable records an ordinary terminal reason before it
            # re-raises a cleanup OOM.  Do not turn that cleanup OOM into a
            # primary operation OOM or discard its retryable stream.
            if self._state == _STATE_UNAVAILABLE:
                raise
            self._memory_failed()
            raise
        except Exception:
            return self._unavailable(self._ordinary_reason())

    def close(self):
        """Release scratch ownership; a close fault remains retryable."""
        if self._closed:
            return True
        self._close_stream()
        self._clear_scratch()
        self._snapshot = None
        self._closed = True
        self._state = _STATE_CLOSED
        return True


__all__ = (
    "PluginScenarioFixtureCandidate",
    "PluginScenarioFixtureSnapshot",
    "REASON_NONE",
    "REASON_SLOT",
    "REASON_MANIFEST",
    "REASON_RECORD",
    "REASON_FILE",
    "REASON_IO",
)
