# Host behaviour tests for the boot evidence record codec.
# The module (source/bootlog.py) must also compile for MicroPython.
import pytest

import bootlog
import bootsel


def _ref(name, release_id, sha_byte):
    return bootsel.SlotEntry(name, release_id, bytes([sha_byte]) * 32)


def _entry(generation=0, selector_generation=1, selection_generation=None,
           selected=None):
    return bootlog.BootEntry(
        generation,
        selector_generation,
        selection_generation,
        selected,
    )


def _store(tmp_path):
    return bootlog.BootLogStore(
        str(tmp_path / "boot.0"), str(tmp_path / "boot.1"))


def test_record_round_trip_preserves_every_field():
    entries = (
        _entry(7, 10, 9, _ref("B", "app:1.4.0:source", 0x33)),
        _entry(3, 2, None, _ref("A", "app:1.3.0:mpy", 0x44)),
        _entry(1, 0, None, None),
    )
    for entry in entries:
        assert bootlog.unpack_record(bootlog.pack_record(entry)) == entry


def test_unpack_rejects_truncation_and_bit_flips():
    packed = bootlog.pack_record(_entry(7, 10, 9, _ref("B", "a:1.4.0", 0x33)))
    for offset in range(len(packed)):
        assert bootlog.unpack_record(packed[:offset]) is None
        corrupted = bytearray(packed)
        corrupted[offset] ^= 0x01
        assert bootlog.unpack_record(bytes(corrupted)) is None


@pytest.mark.parametrize("entry", (
    _entry(1, 0, 5, _ref("B", "app:1.4.0:source", 0x33)),
    _entry(1, 3, 3, _ref("B", "app:1.4.0:source", 0x33)),
    _entry(1, 0, None, _ref("C", "app:1.4.0:source", 0x33)),
    _entry(1, 0, 5, None),
    _entry(1, 0, None, _ref("B", "", 0x33)),
    _entry(1, -1, None, None),
))
def test_pack_rejects_structurally_invalid_entries(entry):
    with pytest.raises(ValueError):
        bootlog.pack_record(entry)


def test_selection_generation_requires_a_trial_selection():
    packed = bootlog.pack_record(_entry(7, 9, None, None))
    assert bootlog.unpack_record(packed) == _entry(7, 9, None, None)


def test_store_assigns_monotonic_boot_ids_and_alternates(tmp_path):
    store = _store(tmp_path)
    assert store.read() is None

    first = store.write(_entry(selector_generation=1,
                               selected=_ref("A", "app:1.3.0:source", 0x11)))
    second = store.write(_entry(selector_generation=4,
                                selection_generation=3,
                                selected=_ref("B", "app:1.4.0:source", 0x22)))

    assert first.generation == 1
    assert second.generation == 2
    assert store.read() == second
    assert (tmp_path / "boot.0").exists()
    assert (tmp_path / "boot.1").exists()


def test_torn_newer_record_falls_back_to_the_previous_boot(tmp_path):
    store = _store(tmp_path)
    first = store.write(_entry(selector_generation=1,
                               selected=_ref("A", "app:1.3.0:source", 0x11)))
    store.write(_entry(selector_generation=4, selection_generation=3,
                       selected=_ref("B", "app:1.4.0:source", 0x22)))

    (tmp_path / "boot.1").write_bytes(b"torn")

    assert store.read() == first


def test_both_records_corrupt_reads_none(tmp_path):
    store = _store(tmp_path)
    store.write(_entry(selector_generation=1,
                       selected=_ref("A", "app:1.3.0:source", 0x11)))
    store.write(_entry(selector_generation=2,
                       selected=_ref("A", "app:1.3.0:source", 0x11)))

    (tmp_path / "boot.0").write_bytes(b"")
    (tmp_path / "boot.1").write_bytes(b"\xff" * 40)

    assert store.read() is None


def test_write_rejects_a_forged_boot_id(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.write(_entry(generation=4, selector_generation=1))


class _TamperingStore(bootlog.BootLogStore):
    def _write_bytes(self, path, data):
        super()._write_bytes(path, data[::-1])


def test_write_readback_mismatch_raises(tmp_path):
    store = _TamperingStore(
        str(tmp_path / "boot.0"), str(tmp_path / "boot.1"))
    with pytest.raises(OSError):
        store.write(_entry(selector_generation=1))
