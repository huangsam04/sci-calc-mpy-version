import hashlib
import json
import sys
import types

import pytest

from calc import plugin_fixture


SLOT_BASE = "/sd/.slots"
RELEASE_ID = "a" * 64
FIXTURE_FILES = (
    "_acceptance_core.py",
    "_acceptance_dependent.py",
    "_acceptance_missing.py",
)


class _SelectedSlot:
    def __init__(self, name, release_id, manifest_sha256):
        self.name = name
        self.release_id = release_id
        self.manifest_sha256 = manifest_sha256


class _Stat:
    def __init__(self, size):
        self.st_size = size


class _Stream:
    def __init__(self, disk, path, payload):
        self._disk = disk
        self._path = path
        self._payload = payload
        self._offset = 0
        self.closed = False
        self.close_calls = 0

    def readinto(self, buffer):
        error = self._disk.read_error
        if error is not None:
            self._disk.read_error = None
            raise error
        if self._offset >= len(self._payload):
            if self._disk.never_ending_path == self._path:
                for index in range(len(buffer)):
                    buffer[index] = ord("x")
                return len(buffer)
            return 0
        count = min(len(buffer), len(self._payload) - self._offset)
        buffer[:count] = self._payload[self._offset:self._offset + count]
        self._offset += count
        return count

    def close(self):
        self.close_calls += 1
        errors = self._disk.close_errors.get(self._path)
        if errors:
            raise errors.pop(0)
        failures = self._disk.close_failures.get(self._path, 0)
        if failures:
            self._disk.close_failures[self._path] = failures - 1
            raise OSError("injected close failure")
        self.closed = True


class _Disk:
    def __init__(self, root, manifest, payloads):
        self.root = root
        self.files = {root + "/release.manifest": manifest}
        for filename, payload in payloads.items():
            self.files[root + "/functions/" + filename] = bytes(payload)
        self.manifest_sha256 = hashlib.sha256(manifest).digest()
        self.opened = []
        self.statted = []
        self.streams = []
        self.read_error = None
        self.close_errors = {}
        self.close_failures = {}
        self.never_ending_path = None
        self.replace_after_manifest_open = None

    def open(self, path, mode):
        assert mode == "rb"
        assert path.startswith(self.root + "/")
        assert "Add-ons" not in path
        self.opened.append(path)
        try:
            stream = _Stream(self, path, self.files[path])
        except KeyError as error:
            raise OSError("unexpected fixture path: " + path) from error
        self.streams.append(stream)
        if (path == self.root + "/release.manifest"
                and self.replace_after_manifest_open is not None):
            manifest, payloads = self.replace_after_manifest_open
            self.replace_after_manifest_open = None
            self.files[path] = manifest
            for filename, payload in payloads.items():
                self.files[self.root + "/functions/" + filename] = bytes(payload)
        return stream

    def stat(self, path):
        assert path.startswith(self.root + "/functions/")
        assert "Add-ons" not in path
        self.statted.append(path)
        try:
            return _Stat(len(self.files[path]))
        except KeyError as error:
            raise OSError("unexpected fixture stat: " + path) from error


def _payloads():
    return {
        "_acceptance_core.py": b"CORE = 1\n",
        "_acceptance_dependent.py": (
            b"DEPENDENCIES = ('_acceptance_core',)\n"),
        "_acceptance_missing.py": (
            b"DEPENDENCIES = ('_acceptance_absent',)\n"),
    }


def _record(filename, payload):
    return {
        "format": "source",
        "key": "sd:functions/" + filename[:-3],
        "path": "functions/" + filename,
        "role": "managed_release",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "zone": "sd",
    }


def _manifest_bytes(release_id, records, seeds=()):
    return json.dumps(
        {
            "abi_tag": "host-fixture",
            "app_version": "fixture",
            "assets": records,
            "mode": "source",
            "product": "sci-calc",
            "release_id": release_id,
            "schema": 1,
            "seeds": list(seeds),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _disk(root="/sd/.slots/A", release_id=RELEASE_ID, records=None,
          payloads=None):
    payloads = _payloads() if payloads is None else payloads
    if records is None:
        records = [_record(filename, payloads[filename])
                   for filename in FIXTURE_FILES]
    return _Disk(root, _manifest_bytes(release_id, records), payloads)


def _install(monkeypatch, disk, root=None, name="A", release_id=RELEASE_ID,
             manifest_sha256=None, slot_base=SLOT_BASE):
    root = disk.root if root is None else root
    digest = disk.manifest_sha256 if manifest_sha256 is None else manifest_sha256
    selected = _SelectedSlot(name, release_id, digest)
    monkeypatch.setattr(
        plugin_fixture,
        "_active_slot_evidence",
        lambda: (root, selected, slot_base, "release.manifest"),
    )
    monkeypatch.setattr(plugin_fixture, "open", disk.open, raising=False)
    monkeypatch.setattr(plugin_fixture.os, "stat", disk.stat)
    return selected


def _finish(candidate, limit=200):
    for _ in range(limit):
        if candidate.step():
            return candidate
    raise AssertionError("fixture candidate did not finish")


def _assert_unavailable(candidate, reason):
    assert _finish(candidate) is candidate
    assert candidate.complete is True
    assert candidate.available is False
    assert candidate.snapshot is None
    assert candidate.reason == reason


@pytest.mark.parametrize("name, root", (
    ("A", "/sd/.slots/A"),
    ("B", "/sd/.slots/B"),
))
def test_accepts_only_a_boot_selected_slot_fixture_pack(monkeypatch, name, root):
    disk = _disk(root)
    _install(monkeypatch, disk, name=name)

    candidate = _finish(plugin_fixture.PluginScenarioFixtureCandidate())

    assert candidate.available is True
    assert candidate.reason == plugin_fixture.REASON_NONE
    snapshot = candidate.snapshot
    assert snapshot.directory == root + "/functions"
    assert snapshot.root == root
    assert snapshot.slot_name == name
    assert snapshot.release_id == RELEASE_ID
    assert snapshot.manifest_sha256 == disk.manifest_sha256
    assert snapshot.files == FIXTURE_FILES
    assert snapshot.valid_selection == ("plugin:_acceptance_dependent",)
    assert snapshot.missing_selection == ("plugin:_acceptance_missing",)
    with pytest.raises(AttributeError):
        snapshot.directory = "/sd"
    assert disk.opened.count(root + "/release.manifest") == 1
    assert tuple(disk.statted) == tuple(
        root + "/functions/" + filename for filename in FIXTURE_FILES)


@pytest.mark.parametrize("root, name, slot_base", (
    ("/sd", "A", SLOT_BASE),
    ("/sd/.slots/B", "A", SLOT_BASE),
    ("/sd/.slots/A/", "A", SLOT_BASE),
    ("/sd/.slots/A/foreign", "A", SLOT_BASE),
    ("/foreign/.slots/A", "A", SLOT_BASE),
    ("/private/.slots/A", "A", "/private/.slots"),
))
def test_rejects_fallback_wrong_trailing_and_foreign_slot_roots(
        monkeypatch, root, name, slot_base):
    disk = _disk()
    _install(monkeypatch, disk, root=root, name=name, slot_base=slot_base)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_SLOT,
    )
    assert disk.opened == []
    assert disk.statted == []


def test_rejects_manifest_sha_mismatch_before_fixture_access(monkeypatch):
    disk = _disk()
    wrong_digest = bytes([disk.manifest_sha256[0] ^ 1]) + (
        disk.manifest_sha256[1:])
    _install(monkeypatch, disk, manifest_sha256=wrong_digest)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_MANIFEST,
    )
    assert disk.statted == []


def test_rejects_manifest_release_id_mismatch(monkeypatch):
    disk = _disk(release_id="b" * 64)
    _install(monkeypatch, disk, release_id=RELEASE_ID)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_RECORD,
    )
    assert disk.statted == []


@pytest.mark.parametrize("case_variant", (False, True))
def test_rejects_duplicate_fixture_record_or_case_variant(
        monkeypatch, case_variant):
    payloads = _payloads()
    records = [_record(filename, payloads[filename]) for filename in FIXTURE_FILES]
    duplicate = dict(records[0])
    if case_variant:
        duplicate["key"] = "sd:functions/_ACCEPTANCE_CORE"
        duplicate["path"] = "functions/_ACCEPTANCE_CORE.PY"
    records.append(duplicate)
    disk = _disk(records=records, payloads=payloads)
    _install(monkeypatch, disk)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_RECORD,
    )
    assert disk.statted == []


@pytest.mark.parametrize("field, value", (
    ("format", "mpy"),
    ("role", "seed_if_absent"),
    ("zone", "internal"),
))
def test_rejects_fixture_record_with_wrong_release_ownership(
        monkeypatch, field, value):
    payloads = _payloads()
    records = [_record(filename, payloads[filename]) for filename in FIXTURE_FILES]
    records[0][field] = value
    disk = _disk(records=records, payloads=payloads)
    _install(monkeypatch, disk)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_RECORD,
    )
    assert disk.statted == []


def test_rejects_fixture_decoy_outside_canonical_assets(monkeypatch):
    payloads = _payloads()
    records = [_record(filename, payloads[filename]) for filename in FIXTURE_FILES]
    decoy = [_record("_acceptance_core.py", payloads["_acceptance_core.py"])]
    manifest = _manifest_bytes(RELEASE_ID, records, seeds=decoy)
    disk = _Disk("/sd/.slots/A", manifest, payloads)
    _install(monkeypatch, disk)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_RECORD,
    )
    assert disk.statted == []


@pytest.mark.parametrize("mutation", ("size", "hash"))
def test_rejects_mutated_fixture_size_or_hash(monkeypatch, mutation):
    disk = _disk()
    path = disk.root + "/functions/_acceptance_core.py"
    original = disk.files[path]
    if mutation == "size":
        disk.files[path] = original + b"x"
    else:
        disk.files[path] = b"x" * len(original)
    _install(monkeypatch, disk)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_FILE,
    )


def test_never_discovers_user_addons_or_directory_entries(monkeypatch):
    disk = _disk()
    _install(monkeypatch, disk)

    def forbidden_listdir(path):
        raise AssertionError("fixture candidate must not discover directories")

    monkeypatch.setattr(plugin_fixture.os, "listdir", forbidden_listdir)
    candidate = _finish(plugin_fixture.PluginScenarioFixtureCandidate())

    assert candidate.available is True
    assert all(path.startswith(disk.root + "/") for path in disk.opened)
    assert all("Add-ons" not in path for path in disk.opened)


def test_manifest_replacement_after_open_cannot_change_verified_records(
        monkeypatch):
    disk = _disk()
    replacement_payloads = {
        filename: b"replaced " + filename.encode("ascii")
        for filename in FIXTURE_FILES
    }
    replacement_records = [
        _record(filename, replacement_payloads[filename])
        for filename in FIXTURE_FILES
    ]
    disk.replace_after_manifest_open = (
        _manifest_bytes(RELEASE_ID, replacement_records), replacement_payloads)
    _install(monkeypatch, disk)

    candidate = plugin_fixture.PluginScenarioFixtureCandidate()
    _assert_unavailable(candidate, plugin_fixture.REASON_FILE)

    assert disk.opened.count(disk.root + "/release.manifest") == 1
    assert candidate._snapshot is None


def test_manifest_byte_and_stream_step_limits_fail_closed(monkeypatch):
    disk = _disk()
    _install(monkeypatch, disk)
    monkeypatch.setattr(plugin_fixture, "_MAX_MANIFEST_BYTES", 256)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_MANIFEST,
    )

    never_ending = _disk()
    never_ending.never_ending_path = never_ending.root + "/release.manifest"
    _install(monkeypatch, never_ending)
    monkeypatch.setattr(plugin_fixture, "_MAX_MANIFEST_BYTES", 65536)
    monkeypatch.setattr(plugin_fixture, "_MAX_MANIFEST_READS", 1)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_MANIFEST,
    )


def test_manifest_record_and_token_limits_fail_closed(monkeypatch):
    record_limited = _disk()
    _install(monkeypatch, record_limited)
    monkeypatch.setattr(plugin_fixture, "_MAX_MANIFEST_RECORDS", 2)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_RECORD,
    )

    token_limited = _disk()
    _install(monkeypatch, token_limited)
    monkeypatch.setattr(plugin_fixture, "_MAX_MANIFEST_RECORDS", 256)
    monkeypatch.setattr(plugin_fixture, "_MAX_MANIFEST_TOKENS", 1)

    _assert_unavailable(
        plugin_fixture.PluginScenarioFixtureCandidate(),
        plugin_fixture.REASON_RECORD,
    )


def test_snapshot_retains_fixed_file_evidence_for_bounded_reverify(monkeypatch):
    disk = _disk()
    _install(monkeypatch, disk)
    candidate = _finish(plugin_fixture.PluginScenarioFixtureCandidate())
    snapshot = candidate.snapshot

    for index, filename in enumerate(FIXTURE_FILES):
        payload = disk.files[disk.root + "/functions/" + filename]
        assert snapshot.expected_digest(index) == hashlib.sha256(payload).digest()
        assert snapshot.expected_size(index) == len(payload)

    reverify = _finish(snapshot.open_reverify())
    assert reverify.available is True
    assert reverify.snapshot is snapshot
    assert disk.opened.count(disk.root + "/release.manifest") == 1

    disk.files[disk.root + "/functions/_acceptance_core.py"] = b"changed\n"
    rejected = snapshot.open_reverify()
    _assert_unavailable(rejected, plugin_fixture.REASON_FILE)
    assert rejected._snapshot is None


def test_file_failure_never_publishes_partial_snapshot(monkeypatch):
    disk = _disk()
    path = disk.root + "/functions/_acceptance_missing.py"
    disk.files[path] = disk.files[path] + b"x"
    _install(monkeypatch, disk)

    candidate = plugin_fixture.PluginScenarioFixtureCandidate()
    _assert_unavailable(candidate, plugin_fixture.REASON_FILE)

    assert candidate._snapshot is None
    assert candidate._expected_digest0 is None
    assert candidate._expected_digest1 is None
    assert candidate._expected_digest2 is None


class _BootRecordStream:
    def __init__(self, payload, read_error=None, close_errors=()):
        self._payload = payload
        self._offset = 0
        self._read_error = read_error
        self._close_errors = list(close_errors)
        self.readinto_sizes = []
        self.read_called = False
        self.closed = False
        self.close_calls = 0

    def readinto(self, buffer):
        self.readinto_sizes.append(len(buffer))
        error = self._read_error
        if error is not None:
            self._read_error = None
            raise error
        count = min(len(buffer), len(self._payload) - self._offset)
        buffer[:count] = self._payload[self._offset:self._offset + count]
        self._offset += count
        return count

    def read(self):
        self.read_called = True
        raise AssertionError("boot evidence must use readinto")

    def close(self):
        self.close_calls += 1
        if self._close_errors:
            raise self._close_errors.pop(0)
        self.closed = True


def test_active_slot_evidence_rejects_oversized_bootlog_records(monkeypatch):
    paths = ("/sys/boot.0", "/sys/boot.1")
    payload = b"x" * plugin_fixture._BOOT_RECORD_BUFFER_BYTES
    streams = []
    decoded = []

    approot = types.ModuleType("approot")
    approot.app_root = lambda: "/sd/.slots/A"
    bootenv = types.ModuleType("bootenv")
    bootenv.BOOTLOG_PATHS = paths
    bootenv.SLOT_BASE = SLOT_BASE
    bootenv.MANIFEST_NAME = "release.manifest"
    bootlog = types.ModuleType("bootlog")

    def unpack_record(raw):
        decoded.append(raw)
        return None

    bootlog.unpack_record = unpack_record

    def bounded_open(path, mode):
        assert path in paths
        assert mode == "rb"
        stream = _BootRecordStream(payload)
        streams.append(stream)
        return stream

    def forbidden_stat(_path):
        raise AssertionError("boot evidence must not trust stat alone")

    monkeypatch.setitem(sys.modules, "approot", approot)
    monkeypatch.setitem(sys.modules, "bootenv", bootenv)
    monkeypatch.setitem(sys.modules, "bootlog", bootlog)
    monkeypatch.setattr(plugin_fixture, "open", bounded_open, raising=False)
    monkeypatch.setattr(plugin_fixture.os, "stat", forbidden_stat)

    root, selected, slot_base, manifest_name = (
        plugin_fixture._active_slot_evidence())

    assert root == "/sd/.slots/A"
    assert selected is None
    assert slot_base == SLOT_BASE
    assert manifest_name == "release.manifest"
    assert decoded == []
    assert len(streams) == 2
    assert all(stream.closed for stream in streams)
    assert all(stream.read_called is False for stream in streams)
    assert all(stream.readinto_sizes == [plugin_fixture._BOOT_RECORD_BUFFER_BYTES]
               for stream in streams)


def test_boot_record_close_fault_rejects_decoded_selected_entry(monkeypatch):
    paths = ("/sys/boot.0", "/sys/boot.1")
    selected_entry = _SelectedSlot("A", RELEASE_ID, b"x" * 32)
    decoded_entry = types.SimpleNamespace(
        generation=1, selected=selected_entry)
    streams = {}

    approot = types.ModuleType("approot")
    approot.app_root = lambda: "/sd/.slots/A"
    bootenv = types.ModuleType("bootenv")
    bootenv.BOOTLOG_PATHS = paths
    bootenv.SLOT_BASE = SLOT_BASE
    bootenv.MANIFEST_NAME = "release.manifest"
    bootlog = types.ModuleType("bootlog")

    def unpack_record(raw):
        assert raw == b"x"
        return decoded_entry

    bootlog.unpack_record = unpack_record

    def bounded_open(path, mode):
        assert mode == "rb"
        if path == paths[0]:
            stream = _BootRecordStream(
                b"x", close_errors=(OSError("injected boot close failure"),))
        else:
            assert path == paths[1]
            stream = _BootRecordStream(b"")
        streams[path] = stream
        return stream

    monkeypatch.setitem(sys.modules, "approot", approot)
    monkeypatch.setitem(sys.modules, "bootenv", bootenv)
    monkeypatch.setitem(sys.modules, "bootlog", bootlog)
    monkeypatch.setattr(plugin_fixture, "open", bounded_open, raising=False)

    _root, selected, _slot_base, _manifest_name = (
        plugin_fixture._active_slot_evidence())

    assert selected is None
    assert streams[paths[0]].close_calls == 2
    assert streams[paths[0]].closed is True


@pytest.mark.parametrize("cleanup_type", (OSError, MemoryError))
def test_primary_boot_read_oom_survives_local_close_retry(
        monkeypatch, cleanup_type):
    paths = ("/sys/boot.0", "/sys/boot.1")
    primary = MemoryError("injected boot record read OOM")
    cleanup = cleanup_type("injected boot record close fault")
    streams = []

    approot = types.ModuleType("approot")
    approot.app_root = lambda: "/sd/.slots/A"
    bootenv = types.ModuleType("bootenv")
    bootenv.BOOTLOG_PATHS = paths
    bootenv.SLOT_BASE = SLOT_BASE
    bootenv.MANIFEST_NAME = "release.manifest"
    bootlog = types.ModuleType("bootlog")
    bootlog.unpack_record = lambda _raw: None

    def bounded_open(path, mode):
        assert path == paths[0]
        assert mode == "rb"
        stream = _BootRecordStream(
            b"", read_error=primary, close_errors=(cleanup,))
        streams.append(stream)
        return stream

    monkeypatch.setitem(sys.modules, "approot", approot)
    monkeypatch.setitem(sys.modules, "bootenv", bootenv)
    monkeypatch.setitem(sys.modules, "bootlog", bootlog)
    monkeypatch.setattr(plugin_fixture, "open", bounded_open, raising=False)

    candidate = plugin_fixture.PluginScenarioFixtureCandidate()
    with pytest.raises(MemoryError) as caught:
        candidate.step()

    assert caught.value is primary
    assert candidate.complete is True
    assert candidate.available is False
    assert candidate.reason == plugin_fixture.REASON_NONE
    assert candidate._stream is None
    assert len(streams) == 1
    assert streams[0].close_calls == 2
    assert streams[0].closed is True


@pytest.mark.parametrize("cleanup_type", (OSError, MemoryError))
def test_primary_boot_decode_oom_survives_local_close_retry(
        monkeypatch, cleanup_type):
    paths = ("/sys/boot.0", "/sys/boot.1")
    primary = MemoryError("injected boot record decode OOM")
    cleanup = cleanup_type("injected boot record decode close fault")
    streams = []

    approot = types.ModuleType("approot")
    approot.app_root = lambda: "/sd/.slots/A"
    bootenv = types.ModuleType("bootenv")
    bootenv.BOOTLOG_PATHS = paths
    bootenv.SLOT_BASE = SLOT_BASE
    bootenv.MANIFEST_NAME = "release.manifest"
    bootlog = types.ModuleType("bootlog")

    def unpack_record(_raw):
        raise primary

    bootlog.unpack_record = unpack_record

    def bounded_open(path, mode):
        assert path == paths[0]
        assert mode == "rb"
        stream = _BootRecordStream(
            b"x", close_errors=(cleanup,))
        streams.append(stream)
        return stream

    monkeypatch.setitem(sys.modules, "approot", approot)
    monkeypatch.setitem(sys.modules, "bootenv", bootenv)
    monkeypatch.setitem(sys.modules, "bootlog", bootlog)
    monkeypatch.setattr(plugin_fixture, "open", bounded_open, raising=False)

    candidate = plugin_fixture.PluginScenarioFixtureCandidate()
    with pytest.raises(MemoryError) as caught:
        candidate.step()

    assert caught.value is primary
    assert candidate.complete is True
    assert candidate.available is False
    assert candidate.reason == plugin_fixture.REASON_NONE
    assert candidate._stream is None
    assert candidate._source_snapshot is None
    assert len(streams) == 1
    assert streams[0].close_calls == 2
    assert streams[0].closed is True


def test_ordinary_boot_read_error_promotes_cleanup_oom_after_local_retry(
        monkeypatch):
    paths = ("/sys/boot.0", "/sys/boot.1")
    primary = RuntimeError("injected boot record read failure")
    cleanup = MemoryError("injected boot record cleanup OOM")
    streams = []

    approot = types.ModuleType("approot")
    approot.app_root = lambda: "/sd/.slots/A"
    bootenv = types.ModuleType("bootenv")
    bootenv.BOOTLOG_PATHS = paths
    bootenv.SLOT_BASE = SLOT_BASE
    bootenv.MANIFEST_NAME = "release.manifest"
    bootlog = types.ModuleType("bootlog")
    bootlog.unpack_record = lambda _raw: None

    def bounded_open(path, mode):
        assert path == paths[0]
        assert mode == "rb"
        stream = _BootRecordStream(
            b"", read_error=primary, close_errors=(cleanup,))
        streams.append(stream)
        return stream

    monkeypatch.setitem(sys.modules, "approot", approot)
    monkeypatch.setitem(sys.modules, "bootenv", bootenv)
    monkeypatch.setitem(sys.modules, "bootlog", bootlog)
    monkeypatch.setattr(plugin_fixture, "open", bounded_open, raising=False)

    candidate = plugin_fixture.PluginScenarioFixtureCandidate()
    with pytest.raises(MemoryError) as caught:
        candidate.step()

    assert caught.value is cleanup
    assert candidate.complete is True
    assert candidate.available is False
    assert candidate.reason == plugin_fixture.REASON_NONE
    assert candidate._stream is None
    assert candidate._source_snapshot is None
    assert len(streams) == 1
    assert streams[0].close_calls == 2
    assert streams[0].closed is True


def test_ordinary_boot_decode_error_promotes_cleanup_oom_after_local_retry(
        monkeypatch):
    paths = ("/sys/boot.0", "/sys/boot.1")
    primary = RuntimeError("injected boot record decode failure")
    cleanup = MemoryError("injected boot record decode cleanup OOM")
    streams = []

    approot = types.ModuleType("approot")
    approot.app_root = lambda: "/sd/.slots/A"
    bootenv = types.ModuleType("bootenv")
    bootenv.BOOTLOG_PATHS = paths
    bootenv.SLOT_BASE = SLOT_BASE
    bootenv.MANIFEST_NAME = "release.manifest"
    bootlog = types.ModuleType("bootlog")

    def unpack_record(_raw):
        raise primary

    bootlog.unpack_record = unpack_record

    def bounded_open(path, mode):
        assert path == paths[0]
        assert mode == "rb"
        stream = _BootRecordStream(
            b"x", close_errors=(cleanup,))
        streams.append(stream)
        return stream

    monkeypatch.setitem(sys.modules, "approot", approot)
    monkeypatch.setitem(sys.modules, "bootenv", bootenv)
    monkeypatch.setitem(sys.modules, "bootlog", bootlog)
    monkeypatch.setattr(plugin_fixture, "open", bounded_open, raising=False)

    candidate = plugin_fixture.PluginScenarioFixtureCandidate()
    with pytest.raises(MemoryError) as caught:
        candidate.step()

    assert caught.value is cleanup
    assert candidate.complete is True
    assert candidate.available is False
    assert candidate.reason == plugin_fixture.REASON_NONE
    assert candidate._stream is None
    assert candidate._source_snapshot is None
    assert len(streams) == 1
    assert streams[0].close_calls == 2
    assert streams[0].closed is True


@pytest.mark.parametrize("cleanup_type", (OSError, MemoryError))
def test_primary_memory_error_survives_ordinary_or_oom_cleanup_failure(
        monkeypatch, cleanup_type):
    disk = _disk()
    error = MemoryError("injected fixture stream exhaustion")
    cleanup = cleanup_type("injected fixture stream cleanup failure")
    disk.read_error = error
    disk.close_errors[disk.root + "/release.manifest"] = [cleanup]
    _install(monkeypatch, disk)
    candidate = plugin_fixture.PluginScenarioFixtureCandidate()

    assert candidate.step() is False
    assert candidate.step() is False
    stream = disk.streams[-1]
    with pytest.raises(MemoryError) as caught:
        candidate.step()

    assert caught.value is error
    assert candidate.complete is True
    assert candidate.available is False
    assert candidate.snapshot is None
    assert candidate.reason == plugin_fixture.REASON_NONE
    assert candidate._stream is stream
    assert stream.close_calls == 1
    assert candidate.close() is True
    assert stream.close_calls == 2
    assert stream.closed is True

    retry_disk = _disk()
    _install(monkeypatch, retry_disk)
    retry = _finish(plugin_fixture.PluginScenarioFixtureCandidate())
    assert retry.available is True


def test_ordinary_terminal_failure_reraises_cleanup_oom_and_keeps_retry(
        monkeypatch):
    disk = _disk()
    cleanup = MemoryError("injected fixture terminal cleanup OOM")
    wrong_digest = bytes([disk.manifest_sha256[0] ^ 1]) + (
        disk.manifest_sha256[1:])
    disk.close_errors[disk.root + "/release.manifest"] = [cleanup]
    _install(monkeypatch, disk, manifest_sha256=wrong_digest)
    candidate = plugin_fixture.PluginScenarioFixtureCandidate()

    assert candidate.step() is False
    assert candidate.step() is False
    stream = disk.streams[-1]
    with pytest.raises(MemoryError) as caught:
        _finish(candidate)

    assert caught.value is cleanup
    assert candidate.complete is True
    assert candidate.available is False
    assert candidate.reason == plugin_fixture.REASON_MANIFEST
    assert candidate._stream is stream
    assert stream.close_calls == 1
    assert candidate.close() is True
    assert stream.close_calls == 2
    assert stream.closed is True


def test_ordinary_terminal_failure_keeps_stream_after_ordinary_close_fault(
        monkeypatch):
    disk = _disk()
    cleanup = OSError("injected fixture terminal close failure")
    wrong_digest = bytes([disk.manifest_sha256[0] ^ 1]) + (
        disk.manifest_sha256[1:])
    disk.close_errors[disk.root + "/release.manifest"] = [cleanup]
    _install(monkeypatch, disk, manifest_sha256=wrong_digest)
    candidate = plugin_fixture.PluginScenarioFixtureCandidate()

    assert candidate.step() is False
    assert candidate.step() is False
    stream = disk.streams[-1]
    _assert_unavailable(candidate, plugin_fixture.REASON_MANIFEST)

    assert candidate._stream is stream
    assert stream.close_calls == 1
    assert candidate.close() is True
    assert stream.close_calls == 2
    assert stream.closed is True


def test_close_is_idempotent_and_retries_after_a_stream_close_fault(monkeypatch):
    disk = _disk()
    disk.close_failures[disk.root + "/release.manifest"] = 1
    _install(monkeypatch, disk)
    candidate = plugin_fixture.PluginScenarioFixtureCandidate()

    assert candidate.step() is False
    assert candidate.step() is False
    stream = disk.streams[-1]
    with pytest.raises(OSError, match="injected close failure"):
        candidate.close()

    assert stream.close_calls == 1
    assert candidate.close() is True
    assert stream.close_calls == 2
    assert stream.closed is True
    assert candidate.close() is True
    with pytest.raises(RuntimeError, match="closed"):
        candidate.step()
