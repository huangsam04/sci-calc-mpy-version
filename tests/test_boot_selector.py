# Host behaviour tests for the dual-record boot selector codec.
# The codec module (source/bootsel.py) must also compile for MicroPython;
# these tests exercise it on CPython through its public interface only.

import pytest

import bootsel


def _ref(name, release_id, sha_byte):
    return bootsel.SlotEntry(name, release_id, bytes([sha_byte]) * 32)


def _selector(generation=0, confirmed=None, trial=None, trial_generation=0,
              trial_consumed=False, retired=(), confirmation_pending=False):
    return bootsel.SelectorData(
        generation,
        confirmed,
        trial,
        trial_generation,
        trial_consumed,
        tuple(retired),
        confirmation_pending,
    )


def _full_selector(generation):
    return _selector(
        generation=generation,
        confirmed=_ref("A", "app:1.4.0:source", 0x11),
        retired=(_ref("B", "app:1.3.0:source", 0x22),),
        confirmation_pending=True,
    )


def _trial_selector(generation):
    return _selector(
        generation=generation,
        confirmed=_ref("A", "app:1.3.0:source", 0x33),
        trial=_ref("B", "app:1.4.0:mpy", 0x44),
        trial_generation=generation,
        trial_consumed=True,
    )


def _store(tmp_path):
    return bootsel.SelectorStore(
        str(tmp_path / "sel.0"), str(tmp_path / "sel.1"))


def test_record_round_trip_preserves_every_field():
    for selector in (_full_selector(9), _trial_selector(4), _selector(1)):
        packed = bootsel.pack_record(selector)
        assert bootsel.unpack_record(packed) == selector


def test_unpack_rejects_truncation_at_every_offset():
    packed = bootsel.pack_record(_full_selector(9))
    for offset in range(len(packed)):
        assert bootsel.unpack_record(packed[:offset]) is None


def test_unpack_rejects_a_bit_flip_at_every_offset():
    packed = bootsel.pack_record(_trial_selector(4))
    for offset in range(len(packed)):
        corrupted = bytearray(packed)
        corrupted[offset] ^= 0x01
        assert bootsel.unpack_record(bytes(corrupted)) is None


@pytest.mark.parametrize("corruptor", (
    lambda data: b"",
    lambda data: data + b"\x00",
    lambda data: b"XCSL" + data[4:],
    lambda data: data[:4] + b"\x02" + data[5:],
))
def test_unpack_rejects_structural_garbage(corruptor):
    packed = bootsel.pack_record(_full_selector(9))
    assert bootsel.unpack_record(corruptor(packed)) is None


@pytest.mark.parametrize("selector", (
    _selector(1, confirmed=_ref("C", "app:1.4.0:source", 0x11)),
    _selector(1, confirmed=_ref("A", "", 0x11)),
    _selector(
        1,
        confirmed=_ref("A", "app:1.4.0:source", 0x11),
        trial=_ref("A", "app:1.5.0:source", 0x22),
        trial_generation=1),
    _selector(1, trial=_ref("B", "app:1.4.0:source", 0x11)),
    _selector(1, trial_consumed=True),
    _selector(1, retired=(
        _ref("A", "app:1.3.0:source", 0x11),
        _ref("B", "app:1.2.0:source", 0x22))),
))
def test_pack_rejects_structurally_invalid_selectors(selector):
    with pytest.raises(ValueError):
        bootsel.pack_record(selector)


def test_pack_rejects_a_wrong_length_manifest_digest():
    bad = bootsel.SlotEntry("A", "app:1.4.0:source", b"\x01" * 31)
    with pytest.raises(ValueError):
        bootsel.pack_record(_selector(1, confirmed=bad))


def test_store_first_write_uses_record_zero_and_reads_back(tmp_path):
    store = _store(tmp_path)
    assert store.read() is None

    stored = store.write(_full_selector(0))

    assert stored.generation == 1
    assert (tmp_path / "sel.0").exists()
    assert not (tmp_path / "sel.1").exists()
    assert store.read() == stored


def test_store_alternates_records_and_prefers_the_newest(tmp_path):
    store = _store(tmp_path)
    first = store.write(_full_selector(0))
    second = store.write(_trial_selector(0))

    assert second.generation == 2
    assert store.read() == second
    assert store.read() != first


def test_torn_newer_record_falls_back_to_the_previous_one(tmp_path):
    store = _store(tmp_path)
    first = store.write(_full_selector(0))
    store.write(_trial_selector(0))

    (tmp_path / "sel.1").write_bytes(b"\xff" * 17)

    assert store.read() == first


def test_torn_older_record_keeps_the_newer_one(tmp_path):
    store = _store(tmp_path)
    store.write(_full_selector(0))
    second = store.write(_trial_selector(0))

    (tmp_path / "sel.0").write_bytes(b"")

    assert store.read() == second


def test_both_records_corrupt_fails_closed(tmp_path):
    store = _store(tmp_path)
    store.write(_full_selector(0))
    store.write(_trial_selector(0))

    (tmp_path / "sel.0").write_bytes(b"garbage")
    (tmp_path / "sel.1").write_bytes(b"")

    assert store.read() is None


def test_write_after_a_tear_restores_redundancy(tmp_path):
    store = _store(tmp_path)
    store.write(_full_selector(0))
    store.write(_trial_selector(0))
    (tmp_path / "sel.1").write_bytes(b"\x00" * 5)

    third = store.write(_full_selector(0))

    assert third.generation == 2
    assert store.read() == third
    assert bootsel.unpack_record(
        (tmp_path / "sel.0").read_bytes()) is not None
    assert bootsel.unpack_record(
        (tmp_path / "sel.1").read_bytes()) is not None


def test_read_prefers_higher_generation_regardless_of_file_order(tmp_path):
    older = _selector(2, confirmed=_ref("A", "app:1.3.0:source", 0x33))
    newer = _selector(3, confirmed=_ref("B", "app:1.4.0:source", 0x44))
    (tmp_path / "sel.0").write_bytes(bootsel.pack_record(newer))
    (tmp_path / "sel.1").write_bytes(bootsel.pack_record(older))

    assert _store(tmp_path).read() == newer


def test_write_assigns_the_next_generation_from_the_valid_winner(tmp_path):
    store = _store(tmp_path)
    first = store.write(_full_selector(0))
    assert first.generation == 1
    second = store.write(_trial_selector(0))
    assert second.generation == 2
    (tmp_path / "sel.1").write_bytes(b"")

    third = store.write(_full_selector(0))

    assert third.generation == 2
    assert store.read() == third


def test_write_rejects_a_forged_generation(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.write(_full_selector(7))


class _TamperingStore(bootsel.SelectorStore):
    def _write_bytes(self, path, data):
        super()._write_bytes(path, bytes(reversed(data)))


def test_write_readback_mismatch_raises_and_keeps_the_old_state(tmp_path):
    store = _store(tmp_path)
    first = store.write(_full_selector(0))

    tampering = _TamperingStore(
        str(tmp_path / "sel.0"), str(tmp_path / "sel.1"))
    with pytest.raises(OSError):
        tampering.write(_trial_selector(0))

    assert store.read() == first


def test_exhaustive_torn_writes_never_yield_garbage(tmp_path):
    store = _store(tmp_path)
    paths = (tmp_path / "sel.0", tmp_path / "sel.1")
    survivors = []

    states = (
        _full_selector(0),
        _trial_selector(0),
        _selector(0, confirmed=_ref("B", "app:1.5.0:mpy", 0x55)),
    )
    for state in states:
        stored = store.write(state)
        survivors.append(stored)
        if len(survivors) > 2:
            survivors.pop(0)
        for path in paths:
            if not path.exists():
                continue
            original = path.read_bytes()
            corruptions = (
                b"",
                original[: len(original) // 2],
                b"\xff" * len(original),
                bytes(reversed(original)),
            )
            for corrupted in corruptions:
                path.write_bytes(corrupted)
                data = store.read()
                assert data is None or data in survivors
            path.write_bytes(original)
        assert store.read() == stored
