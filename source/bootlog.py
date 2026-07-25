# Dual fixed-record boot evidence log.
# Every boot writes one record BEFORE the slot application runs, so the
# release adapter can later prove which selector state actually booted.
# The record generation doubles as the monotonic boot id. Same frame
# pattern as bootsel: magic + schema + generation + payload + SHA-256,
# alternating writes, torn writes ignored on read.
import hashlib

import bootsel

MAGIC = b"SCBL"
SCHEMA = 1

_DIGEST_SIZE = 32
_GENERATION_SIZE = 8
_HEADER_SIZE = 4 + 1 + _GENERATION_SIZE + 1 + _GENERATION_SIZE * 2
_FLAG_SELECTED = 1
_FLAG_SELECTION = 2
_FLAG_MASK = 3
_MAX_GENERATION = 1 << (_GENERATION_SIZE * 8)


class BootEntry:
    def __init__(self, generation, selector_generation,
                 selection_generation, selected):
        self.generation = generation
        self.selector_generation = selector_generation
        self.selection_generation = selection_generation
        self.selected = selected

    def __eq__(self, other):
        return (
            isinstance(other, BootEntry)
            and self.generation == other.generation
            and self.selector_generation == other.selector_generation
            and self.selection_generation == other.selection_generation
            and self.selected == other.selected)

    def __ne__(self, other):
        return not self.__eq__(other)


def _validate_generation(value, label):
    if type(value) is not int or not 0 <= value < _MAX_GENERATION:
        raise ValueError("invalid boot " + label)


def _validate_entry(entry):
    if not isinstance(entry, BootEntry):
        raise ValueError("invalid boot entry")
    _validate_generation(entry.generation, "generation")
    _validate_generation(entry.selector_generation, "selector generation")
    if entry.selected is None:
        if entry.selection_generation is not None:
            raise ValueError("boot selection without a selected slot")
    else:
        bootsel.validate_ref(entry.selected)
        if (type(entry.selection_generation) is not int
                and entry.selection_generation is not None):
            raise ValueError("invalid boot selection generation")
        if entry.selection_generation is not None and (
                entry.selection_generation <= 0
                or entry.selection_generation
                >= entry.selector_generation):
            raise ValueError("invalid boot selection generation")


def pack_record(entry):
    _validate_entry(entry)
    flags = 0
    if entry.selected is not None:
        flags |= _FLAG_SELECTED
    if entry.selection_generation is not None:
        flags |= _FLAG_SELECTION
    body = (
        MAGIC
        + bytes((SCHEMA,))
        + entry.generation.to_bytes(_GENERATION_SIZE, "big")
        + bytes((flags,))
        + entry.selector_generation.to_bytes(_GENERATION_SIZE, "big")
        + (entry.selection_generation or 0).to_bytes(
            _GENERATION_SIZE, "big"))
    if entry.selected is not None:
        encoded = entry.selected.release_id.encode("utf-8")
        body += (bytes((ord(entry.selected.name), len(encoded)))
                 + encoded
                 + bytes(entry.selected.manifest_sha256))
    digest = hashlib.sha256()
    digest.update(body)
    return body + digest.digest()


def _unpack(data):
    if not isinstance(data, (bytes, bytearray)):
        return None
    data = bytes(data)
    if len(data) < _HEADER_SIZE + _DIGEST_SIZE:
        return None
    if data[:4] != MAGIC or data[4] != SCHEMA:
        return None
    digest = hashlib.sha256()
    digest.update(data[:-_DIGEST_SIZE])
    if digest.digest() != data[-_DIGEST_SIZE:]:
        return None
    generation = int.from_bytes(data[5:13], "big")
    flags = data[13]
    if flags & ~_FLAG_MASK:
        return None
    selector_generation = int.from_bytes(data[14:22], "big")
    selection_generation = int.from_bytes(data[22:30], "big")
    offset = _HEADER_SIZE
    selected = None
    if flags & _FLAG_SELECTED:
        if offset + 2 > len(data):
            return None
        name = chr(data[offset])
        size = data[offset + 1]
        start = offset + 2
        end = start + size
        if size == 0 or end + _DIGEST_SIZE > len(data):
            return None
        release_id = data[start:end].decode("utf-8")
        selected = bootsel.SlotEntry(
            name, release_id, data[end:end + _DIGEST_SIZE])
        offset = end + _DIGEST_SIZE
    if offset != len(data) - _DIGEST_SIZE:
        return None
    if not flags & _FLAG_SELECTED and flags & _FLAG_SELECTION:
        return None
    entry = BootEntry(
        generation,
        selector_generation,
        selection_generation if flags & _FLAG_SELECTION else None,
        selected)
    try:
        _validate_entry(entry)
    except ValueError:
        return None
    return entry


def unpack_record(data):
    try:
        return _unpack(data)
    except (ValueError, TypeError, IndexError, UnicodeError):
        return None


class BootLogStore:
    def __init__(self, path0, path1):
        if path0 == path1:
            raise ValueError("boot records must use distinct paths")
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
            entry = unpack_record(raw)
            if entry is None:
                continue
            if best is None or entry.generation > best.generation:
                best = entry
                best_index = index
        return best, best_index

    def read(self):
        entry, _index = self._winner()
        return entry

    def write(self, entry):
        if not isinstance(entry, BootEntry):
            raise ValueError("invalid boot entry")
        if entry.generation != 0:
            raise ValueError("boot id is assigned by the store")
        winner, winner_index = self._winner()
        generation = 1 if winner is None else winner.generation + 1
        stored = BootEntry(
            generation,
            entry.selector_generation,
            entry.selection_generation,
            entry.selected)
        packed = pack_record(stored)
        target = self._paths[1] if winner_index == 0 else self._paths[0]
        self._write_bytes(target, packed)
        if self._read_bytes(target) != packed:
            raise OSError("boot record read-back mismatch")
        return stored
