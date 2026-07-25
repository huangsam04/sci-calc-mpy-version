# Dual fixed-record boot selector codec.
# Runs on both MicroPython (device boot chain) and CPython (host release
# adapter), so it stays free of dataclasses, typing and f-strings.
#
# Record layout: magic + schema + generation + flags + trial generation +
# slot entries + SHA-256 digest. Two record files alternate; every write
# lands on the record that is NOT the current valid winner. A torn write
# fails the digest and is ignored, so a read always yields the previous
# valid state or the next valid state, never garbage.

import hashlib

MAGIC = b"SCSL"
SCHEMA = 1

_DIGEST_SIZE = 32
_GENERATION_SIZE = 8
_HEADER_SIZE = 4 + 1 + _GENERATION_SIZE + 1 + _GENERATION_SIZE
_FLAG_CONFIRMED = 1
_FLAG_TRIAL = 2
_FLAG_TRIAL_CONSUMED = 4
_FLAG_RETIRED = 8
_FLAG_CONFIRMATION_PENDING = 16
_FLAG_MASK = 31
_MAX_GENERATION = 1 << (_GENERATION_SIZE * 8)
_SLOT_NAMES = ("A", "B")


class SlotEntry:
    def __init__(self, name, release_id, manifest_sha256):
        self.name = name
        self.release_id = release_id
        self.manifest_sha256 = manifest_sha256

    def __eq__(self, other):
        return (
            isinstance(other, SlotEntry)
            and self.name == other.name
            and self.release_id == other.release_id
            and self.manifest_sha256 == other.manifest_sha256)

    def __ne__(self, other):
        return not self.__eq__(other)


class SelectorData:
    def __init__(self, generation, confirmed, trial, trial_generation,
                 trial_consumed, retired, confirmation_pending):
        self.generation = generation
        self.confirmed = confirmed
        self.trial = trial
        self.trial_generation = trial_generation
        self.trial_consumed = trial_consumed
        self.retired = tuple(retired)
        self.confirmation_pending = confirmation_pending

    def __eq__(self, other):
        return (
            isinstance(other, SelectorData)
            and self.generation == other.generation
            and self.confirmed == other.confirmed
            and self.trial == other.trial
            and self.trial_generation == other.trial_generation
            and self.trial_consumed == other.trial_consumed
            and self.retired == other.retired
            and self.confirmation_pending == other.confirmation_pending)

    def __ne__(self, other):
        return not self.__eq__(other)


def _validate_ref(ref):
    if not isinstance(ref, SlotEntry):
        raise ValueError("invalid slot entry")
    if ref.name not in _SLOT_NAMES:
        raise ValueError("invalid slot name")
    if (not isinstance(ref.release_id, str)
            or not 0 < len(ref.release_id) <= 255):
        raise ValueError("invalid slot release identity")
    digest = ref.manifest_sha256
    if (not isinstance(digest, (bytes, bytearray))
            or len(digest) != _DIGEST_SIZE):
        raise ValueError("invalid slot manifest digest")


def _roles_overlap(confirmed, trial, retired):
    names = []
    for ref in (confirmed, trial):
        if ref is not None:
            names.append(ref.name)
    for ref in retired:
        names.append(ref.name)
    return len(names) != len(set(names))


def _validate_selector(selector):
    if not isinstance(selector, SelectorData):
        raise ValueError("invalid selector record")
    if (type(selector.generation) is not int
            or not 0 <= selector.generation < _MAX_GENERATION):
        raise ValueError("invalid selector generation")
    if type(selector.trial_consumed) is not bool:
        raise ValueError("invalid selector trial state")
    if type(selector.confirmation_pending) is not bool:
        raise ValueError("invalid selector confirmation state")
    if type(selector.retired) is not tuple or len(selector.retired) > 1:
        raise ValueError("invalid selector retired slots")
    for ref in (selector.confirmed, selector.trial):
        if ref is not None:
            _validate_ref(ref)
    for ref in selector.retired:
        _validate_ref(ref)
    if _roles_overlap(selector.confirmed, selector.trial, selector.retired):
        raise ValueError("selector slot roles overlap")
    if selector.trial is None:
        if selector.trial_generation != 0 or selector.trial_consumed:
            raise ValueError("selector has trial metadata without a trial")
    elif (type(selector.trial_generation) is not int
            or not 0 < selector.trial_generation < _MAX_GENERATION):
        raise ValueError("invalid selector trial generation")


def _pack_ref(ref):
    encoded = ref.release_id.encode("utf-8")
    return (bytes((ord(ref.name), len(encoded)))
            + encoded
            + bytes(ref.manifest_sha256))


def pack_record(selector):
    _validate_selector(selector)
    flags = 0
    if selector.confirmed is not None:
        flags |= _FLAG_CONFIRMED
    if selector.trial is not None:
        flags |= _FLAG_TRIAL
    if selector.trial_consumed:
        flags |= _FLAG_TRIAL_CONSUMED
    if selector.retired:
        flags |= _FLAG_RETIRED
    if selector.confirmation_pending:
        flags |= _FLAG_CONFIRMATION_PENDING
    body = (
        MAGIC
        + bytes((SCHEMA,))
        + selector.generation.to_bytes(_GENERATION_SIZE, "big")
        + bytes((flags,))
        + selector.trial_generation.to_bytes(_GENERATION_SIZE, "big"))
    if selector.confirmed is not None:
        body += _pack_ref(selector.confirmed)
    if selector.trial is not None:
        body += _pack_ref(selector.trial)
    for ref in selector.retired:
        body += _pack_ref(ref)
    digest = hashlib.sha256()
    digest.update(body)
    return body + digest.digest()


def _unpack_ref(data, offset):
    if offset + 2 > len(data):
        return None, offset
    name = chr(data[offset])
    if name not in _SLOT_NAMES:
        return None, offset
    size = data[offset + 1]
    start = offset + 2
    end = start + size
    if size == 0 or end + _DIGEST_SIZE > len(data):
        return None, offset
    release_id = data[start:end].decode("utf-8")
    digest = data[end:end + _DIGEST_SIZE]
    return SlotEntry(name, release_id, digest), end + _DIGEST_SIZE


def _unpack(data):
    if not isinstance(data, (bytes, bytearray)):
        return None
    data = bytes(data)
    if len(data) < _HEADER_SIZE + _DIGEST_SIZE:
        return None
    if data[:4] != MAGIC:
        return None
    if data[4] != SCHEMA:
        return None
    digest = hashlib.sha256()
    digest.update(data[:-_DIGEST_SIZE])
    if digest.digest() != data[-_DIGEST_SIZE:]:
        return None
    generation = int.from_bytes(data[5:13], "big")
    flags = data[13]
    if flags & ~_FLAG_MASK:
        return None
    trial_generation = int.from_bytes(data[14:22], "big")
    offset = _HEADER_SIZE
    confirmed = None
    trial = None
    retired = ()
    if flags & _FLAG_CONFIRMED:
        confirmed, offset = _unpack_ref(data, offset)
        if confirmed is None:
            return None
    if flags & _FLAG_TRIAL:
        trial, offset = _unpack_ref(data, offset)
        if trial is None:
            return None
    if flags & _FLAG_RETIRED:
        ref, offset = _unpack_ref(data, offset)
        if ref is None:
            return None
        retired = (ref,)
    if offset != len(data) - _DIGEST_SIZE:
        return None
    if trial is None:
        if trial_generation != 0 or flags & _FLAG_TRIAL_CONSUMED:
            return None
    elif trial_generation == 0:
        return None
    if _roles_overlap(confirmed, trial, retired):
        return None
    return SelectorData(
        generation,
        confirmed,
        trial,
        trial_generation,
        bool(flags & _FLAG_TRIAL_CONSUMED),
        retired,
        bool(flags & _FLAG_CONFIRMATION_PENDING))


def unpack_record(data):
    try:
        return _unpack(data)
    except (ValueError, TypeError, IndexError, UnicodeError):
        return None


class SelectorStore:
    def __init__(self, path0, path1):
        if path0 == path1:
            raise ValueError("selector records must use distinct paths")
        self._paths = (path0, path1)

    def _read_bytes(self, path):
        try:
            with open(path, "rb") as stream:
                return stream.read()
        except OSError:
            return None

    def _write_bytes(self, path, data):
        stream = open(path, "wb")
        try:
            stream.write(data)
            stream.flush()
        finally:
            stream.close()

    def _winner(self):
        best = None
        best_index = -1
        for index, path in enumerate(self._paths):
            raw = self._read_bytes(path)
            if raw is None:
                continue
            record = unpack_record(raw)
            if record is None:
                continue
            if best is None or record.generation > best.generation:
                best = record
                best_index = index
        return best, best_index

    def read(self):
        record, _index = self._winner()
        return record

    def write(self, selector):
        if not isinstance(selector, SelectorData):
            raise ValueError("invalid selector record")
        if selector.generation != 0:
            raise ValueError("selector generation is assigned by the store")
        winner, winner_index = self._winner()
        generation = 1 if winner is None else winner.generation + 1
        trial_generation = selector.trial_generation
        if selector.trial is not None and trial_generation == 0:
            # Arming a trial binds its selection generation to the record
            # being written, which only the store knows beforehand.
            trial_generation = generation
        stored = SelectorData(
            generation,
            selector.confirmed,
            selector.trial,
            trial_generation,
            selector.trial_consumed,
            selector.retired,
            selector.confirmation_pending)
        # pack_record validates the stored record before any flash write.
        packed = pack_record(stored)
        target = self._paths[1] if winner_index == 0 else self._paths[0]
        self._write_bytes(target, packed)
        if self._read_bytes(target) != packed:
            raise OSError("selector record read-back mismatch")
        return stored
