# Host behaviour tests for the production mpremote release adapter.
# The device twin maps device paths onto a real temp filesystem and runs
# the same boot codecs plus the supervisor decision logic, so the adapter
# is exercised through its real wire format end to end.
import ast
import builtins
import binascii
import hashlib
import inspect
import json

import pytest

import bootenv
import bootlog
import bootsel
import bootsupervisor
from tools import release_deploy
from tools import release_device_mpremote as mpadapter
from tools.release_apply import ReleaseFailure, apply_release
from tools.release_plan import ReleaseTreeSnapshot, plan_release
from tools.release_protocol import SlotRef, owner_marker_payload


@pytest.mark.parametrize("code,module_name", (
    (mpadapter.SELECTOR_READ_CODE, "bootsel"),
    (mpadapter.SELECTOR_WRITE_CODE, "bootsel"),
    (mpadapter.BOOTLOG_READ_CODE, "bootlog"),
))
def test_selector_controls_restore_trusted_root_before_import(
        code, module_name):
    root_restore = "sys.path.insert(0,'/')"

    assert root_restore in code
    assert code.index(root_restore) < code.index("import " + module_name)
    assert "'/sd'" not in code


def _plan(version, legacy=False, mode="source"):
    files = [
        ("main.py", ("# main " + version + "\n").encode("ascii")),
        ("settings.json", b'{"brightness":20}\n'),
        ("vars.json", b'{"seed":0}\n'),
        ("version.py", ('VERSION = "' + version + '"\n').encode("ascii")),
        ("boot.py", b"# boot anchor\n"),
    ]
    if legacy:
        files.append(("legacy.py", b"# old managed module\n"))
    else:
        files.append(("catalog.py", b"# new managed module\n"))
    build_files = ()
    if mode == "mpy":
        build_files = tuple(
            (path[:-3] + ".mpy", b"M\x06" + content)
            for path, content in files
            if path.endswith(".py") and path != "boot.py"
        )
    return plan_release(
        ReleaseTreeSnapshot.from_files(files, build_files=build_files),
        mode=mode,
    )


class _DeviceTwin:
    def __init__(self, root, probe):
        self._root = root
        self._probe = probe
        (root / "sys").mkdir(parents=True, exist_ok=True)
        (root / "sd").mkdir(parents=True, exist_ok=True)
        self.sessions = 0
        self.resets = 0
        self.clean_resets = 0
        self.closes = 0
        self.fail_reset = False
        self.fail_close = False
        self.staging_mutator = None
        self.limited_calls = []
        self.read_file_calls = 0
        self.write_paths = []
        self._connected = False
        self._last_boot = None

    def _map(self, path):
        assert path.startswith("/")
        return self._root.joinpath(*path[1:].split("/"))

    def connect(self):
        assert not self._connected
        self._connected = True
        self.sessions += 1

    def close(self):
        self._connected = False
        self.closes += 1
        if self.fail_close:
            self.fail_close = False
            raise OSError("close failed")

    def reset(self):
        if self.fail_reset:
            self.fail_reset = False
            raise OSError("reset failed")
        self.resets += 1
        self._supervise_boot()

    def reset_to_boot_repl(self):
        assert self._connected
        self.clean_resets += 1

    def _supervise_boot(self):
        store = bootsel.SelectorStore(
            str(self._map("/sys/sel.0")), str(self._map("/sys/sel.1")))
        selector = store.read()

        def slot_exists(name):
            probe_path = self._map(
                bootenv.SLOT_BASE + "/" + name + "/" + bootenv.MANIFEST_NAME)
            return probe_path.exists()

        plan = bootsupervisor.decide(selector, slot_exists)
        selector_generation = 0 if selector is None else selector.generation
        if plan.consume is not None:
            stored = store.write(plan.consume)
            selector_generation = stored.generation
        selected = None
        if plan.action == bootsupervisor.ACTION_SLOT:
            selected = plan.slot_ref
        log = bootlog.BootLogStore(
            str(self._map("/sys/boot.0")), str(self._map("/sys/boot.1")))
        log.write(bootlog.BootEntry(
            0, selector_generation, plan.selection_generation, selected))
        self._last_boot = plan

    def read_file(self, path):
        self.read_file_calls += 1
        mapped = self._map(path)
        if not mapped.is_file():
            return None
        return mapped.read_bytes()

    def write_file(self, path, data):
        self.write_paths.append(path)
        mapped = self._map(path)
        mapped.parent.mkdir(parents=True, exist_ok=True)
        mapped.write_bytes(bytes(data))

    def exists(self, path):
        return self._map(path).exists()

    def makedirs(self, path):
        self._map(path).mkdir(parents=True, exist_ok=True)

    def exec_limited(self, code, max_output_bytes, **params):
        assert type(max_output_bytes) is int and max_output_bytes > 0
        self.limited_calls.append((code, max_output_bytes))
        out = self.exec(code, **params)
        assert len(out.encode("utf-8")) <= max_output_bytes
        return out

    def exec(self, code, **params):
        assert self._connected
        if code is mpadapter.SELECTOR_READ_CODE:
            store = bootsel.SelectorStore(
                str(self._map("/sys/sel.0")), str(self._map("/sys/sel.1")))
            record = store.read()
            if record is None:
                return "NONE"
            return binascii.hexlify(bootsel.pack_record(record)).decode()
        if code is mpadapter.SELECTOR_WRITE_CODE:
            fields = ast.literal_eval(params["fields"])

            def ref(item):
                if item is None:
                    return None
                return bootsel.SlotEntry(
                    item[0], item[1], binascii.unhexlify(item[2]))

            record = bootsel.SelectorData(
                0,
                ref(fields[0]),
                ref(fields[1]),
                fields[2],
                fields[3],
                tuple(ref(item) for item in fields[4]),
                fields[5],
            )
            store = bootsel.SelectorStore(
                str(self._map("/sys/sel.0")), str(self._map("/sys/sel.1")))
            stored = store.write(record)
            return binascii.hexlify(bootsel.pack_record(stored)).decode()
        if code is mpadapter.BOOTLOG_READ_CODE:
            log = bootlog.BootLogStore(
                str(self._map("/sys/boot.0")), str(self._map("/sys/boot.1")))
            entry = log.read()
            if entry is None:
                return "NONE"
            return binascii.hexlify(bootlog.pack_record(entry)).decode()
        if code is mpadapter.HASH_PATHS_CODE:
            matched = 0
            missing = 0
            for index, (path, expected) in enumerate(
                    ast.literal_eval(params["pairs"])):
                mapped = self._map(path)
                if not mapped.is_file():
                    missing |= 1 << index
                    continue
                actual = hashlib.sha256(mapped.read_bytes()).hexdigest()
                if actual != expected:
                    continue
                matched |= 1 << index
            return "H{0:03x}{1:03x}".format(matched, missing)
        if code is mpadapter.VERIFY_SLOT_CODE:
            if self.staging_mutator is not None:
                self.staging_mutator(self)
                self.staging_mutator = None
            return self._verify_slot(params)
        if code is mpadapter.VALIDATE_MANIFEST_CODE:
            mapped = self._map(params["manifest_path"])
            if not mapped.is_file():
                return "MISSING"
            actual = hashlib.sha256(mapped.read_bytes()).hexdigest()
            if actual != params["manifest_sha256"]:
                return "HASH"
            return "OK"
        if code is mpadapter.OWNED_TREE_RECEIPT_CODE:
            return self._owned_tree_receipt(params)
        if code is mpadapter.OWNED_TREE_FILE_RECEIPT_CODE:
            return self._owned_file_receipt(params)
        if code is mpadapter.OWNED_TREE_READ_CHUNK_CODE:
            return self._owned_read_chunk(params)
        if code is mpadapter.OWNED_TREE_DIRECTORY_COUNT_CODE:
            return self._owned_directory_count(params)
        if code is mpadapter.OWNED_TREE_ENTRY_KIND_CODE:
            return self._owned_entry_kind(params)
        if code is mpadapter.OWNED_TREE_REMOVE_BATCH_CODE:
            return self._owned_remove_batch(params)
        if code is mpadapter.OWNED_TREE_REMOVE_FILE_CODE:
            return self._owned_remove_file(params)
        if code is mpadapter.OWNED_TREE_REMOVE_DIRECTORY_CODE:
            return self._owned_remove_directory(params)
        if code is mpadapter.OWNED_TREE_ACTIVATE_CODE:
            return self._owned_activate(params)
        if "mkdir" in code:
            import re
            match = re.search(r"os\.mkdir\('([^']+)'\)", code)
            if match:
                self._map(match.group(1)).mkdir(parents=True, exist_ok=True)
            return "OK"
        if code is mpadapter.RELEASE_CONTROL_COLLECT_CODE:
            return "OK"
        if code == self._probe_source_text:
            return self._probe(self._last_boot)
        raise AssertionError("unexpected device exec: " + code[:60])

    _probe_source_text = None

    def _owned_tree_receipt(self, params):
        """Return marker/manifest evidence; shape is checked separately."""
        try:
            root_path = self._owned_string(params, "root")
            manifest_name = self._owned_string(params, "manifest_name")
            owner_name = self._owned_string(params, "owner_name")
        except (KeyError, TypeError, ValueError, SyntaxError):
            return "F"
        manifest_sha256 = params.get("manifest_sha256")
        owner_sha256 = params.get("owner_sha256")
        if (not self._safe_owned_relative(manifest_name)
                or not self._is_lower_hash(manifest_sha256)
                or owner_name != ".sci-calc-owner"
                or not self._is_lower_hash(owner_sha256)
                or not root_path.startswith("/")):
            return "F"
        try:
            root = self._map(root_path)
        except (AssertionError, TypeError):
            return "F"
        if not root.exists():
            return "M"
        if not root.is_dir():
            return "F"
        manifest_path = root.joinpath(*manifest_name.split("/"))
        owner_path = root.joinpath(*owner_name.split("/"))
        if not manifest_path.is_file() or not owner_path.is_file():
            return "F"
        try:
            return "O" if (
                hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                == manifest_sha256
                and hashlib.sha256(owner_path.read_bytes()).hexdigest()
                == owner_sha256) else "F"
        except MemoryError:
            raise
        except OSError:
            return "F"

    @staticmethod
    def _owned_string(params, name):
        value = ast.literal_eval(params[name])
        if type(value) is not str:
            raise ValueError("owned protocol requires a string")
        return value

    def _owned_file_receipt(self, params):
        try:
            path = self._map(self._owned_string(params, "path"))
        except (AssertionError, KeyError, TypeError, ValueError, SyntaxError):
            return "F"
        expected = params.get("sha256")
        if not self._is_lower_hash(expected):
            return "F"
        if not path.exists():
            return "M"
        if not path.is_file():
            return "F"
        try:
            return (
                "O" if hashlib.sha256(path.read_bytes()).hexdigest() == expected
                else "F")
        except MemoryError:
            raise
        except OSError:
            return "F"

    def _owned_read_chunk(self, params):
        try:
            path = self._map(self._owned_string(params, "path"))
            offset = params["offset"]
        except (AssertionError, KeyError, TypeError, ValueError, SyntaxError):
            return "F"
        if type(offset) is not int or isinstance(offset, bool) or offset < 0:
            return "F"
        if not path.exists():
            return "M"
        if not path.is_file():
            return "F"
        try:
            data = path.read_bytes()
        except MemoryError:
            raise
        except OSError:
            return "F"
        chunk = data[offset:offset + mpadapter._OWNED_TREE_CHUNK_BYTES]
        if not chunk:
            return "E"
        return "D" + binascii.hexlify(chunk).decode()

    def _owned_directory_count(self, params):
        try:
            path = self._map(self._owned_string(params, "path"))
        except (AssertionError, KeyError, TypeError, ValueError, SyntaxError):
            return "F"
        if not path.exists():
            return "M"
        if not path.is_dir():
            return "F"
        try:
            count = sum(1 for _child in path.iterdir())
        except MemoryError:
            raise
        except OSError:
            return "F"
        if count > mpadapter._OWNED_TREE_MAX_DIRECTORY_ENTRIES:
            return "F"
        return "N{0:03x}".format(count)

    def _owned_entry_kind(self, params):
        try:
            parent = self._map(self._owned_string(params, "parent"))
            name = self._owned_string(params, "name")
        except (AssertionError, KeyError, TypeError, ValueError, SyntaxError):
            return "F"
        if (not self._safe_owned_relative(name)
                or "/" in name):
            return "F"
        if not parent.exists():
            return "M"
        if not parent.is_dir():
            return "F"
        try:
            entries = tuple(parent.iterdir())
        except MemoryError:
            raise
        except OSError:
            return "F"
        if len(entries) > mpadapter._OWNED_TREE_MAX_DIRECTORY_ENTRIES:
            return "F"
        matches = [entry for entry in entries if entry.name == name]
        if not matches:
            return "M"
        if len(matches) != 1:
            return "F"
        return "D" if matches[0].is_dir() else "R" if matches[0].is_file() else "F"

    def _owned_remove_file(self, params):
        try:
            path = self._map(self._owned_string(params, "path"))
        except (AssertionError, KeyError, TypeError, ValueError, SyntaxError):
            return "F"
        try:
            path.unlink()
        except MemoryError:
            raise
        except FileNotFoundError:
            return "M"
        except OSError:
            return "F"
        return "E"

    def _owned_remove_directory(self, params):
        try:
            path = self._map(self._owned_string(params, "path"))
        except (AssertionError, KeyError, TypeError, ValueError, SyntaxError):
            return "F"
        try:
            path.rmdir()
        except MemoryError:
            raise
        except FileNotFoundError:
            return "M"
        except OSError:
            return "F"
        return "E"

    def _owned_activate(self, params):
        try:
            src = self._map(self._owned_string(params, "src"))
            dst = self._map(self._owned_string(params, "dst"))
        except (AssertionError, KeyError, TypeError, ValueError, SyntaxError):
            return "F"
        if not src.exists() or not src.is_dir():
            return "F"
        if dst.exists():
            return "C"
        try:
            src.rename(dst)
        except MemoryError:
            raise
        except OSError:
            return "F"
        return "E"

    def _owned_remove_batch(self, params):
        try:
            files = ast.literal_eval(params["files"])
            directories = ast.literal_eval(params["directories"])
            root = self._map(self._owned_string(params, "root"))
            for path in files:
                mapped = self._map(path)
                if mapped.exists():
                    mapped.unlink()
            for path in directories:
                mapped = self._map(path)
                if mapped.exists():
                    mapped.rmdir()
            if root.exists():
                root.rmdir()
            return "E"
        except (AssertionError, KeyError, OSError, TypeError, ValueError,
                SyntaxError):
            return "F"

    @staticmethod
    def _safe_owned_relative(path):
        return (
            type(path) is str
            and bool(path)
            and path.isascii()
            and not path.startswith("/")
            and not path.endswith("/")
            and "\\" not in path
            and "\x00" not in path
            and ":" not in path
            and all(part not in ("", ".", "..") for part in path.split("/"))
        )

    @staticmethod
    def _is_lower_hash(value):
        return (
            type(value) is str
            and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)
        )

    def _verify_slot(self, params):
        root = self._map(params["slot_root"])
        manifest_path = root / params["manifest_name"]
        if not manifest_path.is_file():
            return "MISSING_MANIFEST"
        manifest_bytes = manifest_path.read_bytes()
        actual = hashlib.sha256(manifest_bytes).hexdigest()
        if actual != params["manifest_sha256"]:
            return "MANIFEST"
        import json
        manifest = json.loads(manifest_bytes.decode())
        for record in manifest["assets"]:
            if record.get("role") != "managed_release":
                continue
            if record.get("zone") != "sd":
                continue
            asset_path = root / record["path"]
            if not asset_path.is_file():
                return "MISSING " + record["path"]
            data = asset_path.read_bytes()
            if len(data) != record["size"]:
                return "HASH " + record["path"]
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                return "HASH " + record["path"]
        return "OK"


def _smoke_lines(version, mode="source", identity="12345"):
    return "\n".join((
        "BOOT_VERSION " + version,
        "BOOT_RUNTIME_READY True",
        "BOOT_ROOT_VISIBLE True",
        "BOOT_BUFFERS main:8192:" + identity,
        "BOOT_MODE " + mode,
        "BOOT_ABI_VIPER ok",
    ))


def _adapter_and_twin(tmp_path, probe):
    twin = _DeviceTwin(tmp_path, probe)
    twin._probe_source_text = "probe-source"
    adapter = mpadapter.MpremoteReleaseAdapter(
        lambda: twin, probe_source="probe-source", boot_wait_s=0)
    return adapter, twin


def test_control_records_use_host_codecs_without_device_imports(tmp_path):
    twin = _DeviceTwin(tmp_path, "probe-source")
    twin.connect()
    ref = bootsel.SlotEntry("A", "release-a", b"a" * 32)
    selector = bootsel.SelectorData(
        5, ref, None, 0, False, (), False)
    entry = bootlog.BootEntry(7, 5, None, ref)
    twin.write_file("/sys/sel.0", bootsel.pack_record(selector))
    twin.write_file(
        "/sys/sel.1", b"x" * (mpadapter._SELECTOR_RECORD_MAX_BYTES + 1))
    twin.write_file("/sys/boot.0", bootlog.pack_record(entry))
    session = mpadapter._MpremoteSession(twin, "probe-source")

    assert session._read_selector() == selector
    assert session._read_boot_entry() == entry
    trial_ref = bootsel.SlotEntry("B", "release-b", b"b" * 32)
    stored = session._write_selector(bootsel.SelectorData(
        0, ref, trial_ref, 0, False, (), False))

    assert stored.generation == 6
    assert stored.trial_generation == 6
    assert bootsel.unpack_record(twin.read_file("/sys/sel.1")) == stored
    assert twin.limited_calls == []
    assert len(bootsel.pack_record(stored)) <= (
        mpadapter._SELECTOR_RECORD_MAX_BYTES)
    assert len(bootlog.pack_record(entry)) <= (
        mpadapter._BOOTLOG_RECORD_MAX_BYTES)


def _run_verify_slot_protocol(tmp_path, capsys, manifest_bytes, files=()):
    """Execute the emitted raw-REPL verifier against a local slot twin."""
    root = tmp_path / "protocol-slot"
    root.mkdir(parents=True, exist_ok=True)
    (root / bootenv.MANIFEST_NAME).write_bytes(manifest_bytes)
    for relative_path, payload in files:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    source = mpadapter.VERIFY_SLOT_CODE.format(
        slot_root=root.as_posix(),
        manifest_name=bootenv.MANIFEST_NAME,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
    exec(source, {})
    return capsys.readouterr().out.strip()


def _managed_release_files(plan):
    return tuple(
        (asset.relative_path, asset.payload)
        for asset in plan.assets
        if asset.role == "managed_release" and asset.zone == "sd"
    )


def _canonical_manifest_bytes(manifest):
    return json.dumps(
        manifest,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def test_verify_slot_protocol_streams_one_bounded_record_at_a_time(
        tmp_path, capsys):
    plan = _plan("1.4.0")

    assert _run_verify_slot_protocol(
        tmp_path, capsys, plan.manifest_bytes,
        _managed_release_files(plan)) == "OK"
    assert ".read()" not in mpadapter.VERIFY_SLOT_CODE
    assert "readinto" in mpadapter.VERIFY_SLOT_CODE
    assert "manifest_bytes>max_manifest_bytes" in mpadapter.VERIFY_SLOT_CODE
    assert "record_length>=max_record_bytes" in mpadapter.VERIFY_SLOT_CODE


def test_verify_slot_protocol_preserves_asset_oom_after_closing_streams(
        tmp_path):
    plan = _plan("1.4.0")
    root = tmp_path / "oom-slot"
    root.mkdir()
    (root / bootenv.MANIFEST_NAME).write_bytes(plan.manifest_bytes)
    files = _managed_release_files(plan)
    for relative_path, payload in files:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    target = root / files[0][0]
    primary = MemoryError("device asset hash OOM")
    cleanup = MemoryError("device asset stream close OOM")

    class _FatalAssetStream:
        def __init__(self):
            self.close_calls = 0

        def readinto(self, _buffer):
            raise primary

        def close(self):
            self.close_calls += 1
            raise cleanup

    asset_stream = _FatalAssetStream()

    def open_stream(path, mode):
        if path == target.as_posix():
            return asset_stream
        return builtins.open(path, mode)

    source = mpadapter.VERIFY_SLOT_CODE.format(
        slot_root=root.as_posix(),
        manifest_name=bootenv.MANIFEST_NAME,
        manifest_sha256=plan.manifest_sha256,
    )
    with pytest.raises(MemoryError) as caught:
        exec(source, {"open": open_stream})

    assert caught.value is primary
    assert asset_stream.close_calls == 1


def test_verify_slot_protocol_rejects_truncated_and_oversize_records(
        tmp_path, capsys):
    plan = _plan("1.4.0")

    assert _run_verify_slot_protocol(
        tmp_path / "truncated",
        capsys,
        plan.manifest_bytes[:-1],
        _managed_release_files(plan),
    ) == "MANIFEST"

    oversized_record = {
        "format": "source",
        "key": "sd:oversized",
        "path": "a" * (mpadapter._VERIFY_RECORD_MAX_BYTES + 1),
        "role": "managed_release",
        "sha256": "0" * 64,
        "size": 0,
        "zone": "sd",
    }
    oversized_manifest = json.dumps(
        {
            "abi_tag": "source",
            "app_version": "1.4.0",
            "assets": [oversized_record],
            "mode": "source",
            "product": "sci-calc",
            "release_id": "0" * 64,
            "schema": 1,
            "seeds": [],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")

    assert _run_verify_slot_protocol(
        tmp_path / "oversized", capsys, oversized_manifest) == "MANIFEST"

    oversized_bytes = plan.manifest_bytes + (
        b" " * mpadapter._VERIFY_MANIFEST_MAX_BYTES)
    assert _run_verify_slot_protocol(
        tmp_path / "oversized-bytes",
        capsys,
        oversized_bytes,
        _managed_release_files(plan),
    ) == "MANIFEST"

    bounded_record = dict(oversized_record)
    bounded_record["path"] = "bounded.py"
    many_records_manifest = json.dumps(
        {
            "abi_tag": "source",
            "app_version": "1.4.0",
            "assets": [
                {
                    **bounded_record,
                    "key": "internal:" + str(index),
                    "role": "bootstrap_fixed",
                    "zone": "internal",
                }
                for index in range(mpadapter._VERIFY_MANIFEST_MAX_RECORDS + 1)
            ],
            "mode": "source",
            "product": "sci-calc",
            "release_id": "0" * 64,
            "schema": 1,
            "seeds": [],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")

    assert _run_verify_slot_protocol(
        tmp_path / "too-many-records", capsys, many_records_manifest) == (
            "MANIFEST")


def test_verify_slot_protocol_rejects_trailing_bytes_after_canonical_manifest(
        tmp_path, capsys):
    plan = _plan("1.4.0")

    assert _run_verify_slot_protocol(
        tmp_path,
        capsys,
        plan.manifest_bytes + b"\n",
        _managed_release_files(plan),
    ) == "MANIFEST"


def test_verify_slot_protocol_rejects_semantically_invalid_asset_record(
        tmp_path, capsys):
    plan = _plan("1.4.0")
    manifest = json.loads(plan.manifest_bytes)
    record = next(
        record for record in manifest["assets"]
        if record["role"] == "managed_release" and record["zone"] == "sd"
    )
    record["format"] = "invalid"
    release_payload = dict(manifest)
    del release_payload["release_id"]
    manifest["release_id"] = hashlib.sha256(
        _canonical_manifest_bytes(release_payload)).hexdigest()

    assert _run_verify_slot_protocol(
        tmp_path,
        capsys,
        _canonical_manifest_bytes(manifest),
        _managed_release_files(plan),
    ) == "MANIFEST"


def test_verify_slot_protocol_rejects_unicode_asset_paths_before_receipts(
        tmp_path, capsys):
    plan = _plan("1.4.0")
    manifest = json.loads(plan.manifest_bytes)
    record = next(
        record for record in manifest["assets"]
        if record["role"] == "managed_release" and record["zone"] == "sd"
    )
    record["path"] = "caf\u00e9.py"

    assert _run_verify_slot_protocol(
        tmp_path,
        capsys,
        _canonical_manifest_bytes(manifest),
        _managed_release_files(plan),
    ) == "MANIFEST"


def test_verify_slot_protocol_accepts_canonical_mpy_release_plan(
        tmp_path, capsys):
    plan = _plan("1.4.0", mode="mpy")

    assert _run_verify_slot_protocol(
        tmp_path,
        capsys,
        plan.manifest_bytes,
        _managed_release_files(plan),
    ) == "OK"


def test_verify_slot_protocol_accepts_canonical_font_record(tmp_path, capsys):
    plan = plan_release(
        ReleaseTreeSnapshot.from_files(
            (
                ("version.py", b'VERSION = "1.4.0"\n'),
                ("fonts/Bally7x9.c", b"/* font input */\n"),
            ),
            build_files=(("fonts/Bally7x9.xglcd", b"font-data"),),
        ),
        mode="source",
    )

    assert _run_verify_slot_protocol(
        tmp_path,
        capsys,
        plan.manifest_bytes,
        _managed_release_files(plan),
    ) == "OK"


def test_verify_slot_protocol_rejects_hash_mismatch_and_missing_managed_file(
        tmp_path, capsys):
    plan = _plan("1.4.0")
    files = _managed_release_files(plan)
    target_path, target_payload = files[0]
    corrupted_payload = target_payload[:-1] + b"!"
    corrupted_files = tuple(
        (path, corrupted_payload if path == target_path else payload)
        for path, payload in files
    )

    assert _run_verify_slot_protocol(
        tmp_path / "hash-mismatch",
        capsys,
        plan.manifest_bytes,
        corrupted_files,
    ) == "HASH " + target_path
    assert _run_verify_slot_protocol(
        tmp_path / "missing-managed-file",
        capsys,
        plan.manifest_bytes,
        tuple((path, payload) for path, payload in files if path != target_path),
    ) == "MISSING " + target_path


def _run_validate_manifest_protocol(tmp_path, capsys, manifest_bytes,
                                    expected_sha256):
    """Execute the emitted raw-REPL manifest validator against a local twin."""
    root = tmp_path / "protocol-manifest"
    root.mkdir(parents=True, exist_ok=True)
    target = root / bootenv.MANIFEST_NAME
    if manifest_bytes is not None:
        target.write_bytes(manifest_bytes)
    source = mpadapter.VALIDATE_MANIFEST_CODE.format(
        manifest_path=target.as_posix(),
        manifest_sha256=expected_sha256,
    )
    exec(source, {})
    return capsys.readouterr().out.strip()


def test_validate_manifest_protocol_streams_bounded_chunks(tmp_path, capsys):
    plan = _plan("1.4.0")

    assert _run_validate_manifest_protocol(
        tmp_path, capsys, plan.manifest_bytes, plan.manifest_sha256) == "OK"
    assert ".read()" not in mpadapter.VALIDATE_MANIFEST_CODE
    assert "readinto" in mpadapter.VALIDATE_MANIFEST_CODE
    assert "total>max_manifest_bytes" in mpadapter.VALIDATE_MANIFEST_CODE
    assert "reads>max_manifest_reads" in mpadapter.VALIDATE_MANIFEST_CODE


def test_validate_manifest_protocol_reports_each_bounded_failure(
        tmp_path, capsys):
    plan = _plan("1.4.0")

    assert _run_validate_manifest_protocol(
        tmp_path / "missing", capsys, None, plan.manifest_sha256) == "MISSING"
    assert _run_validate_manifest_protocol(
        tmp_path / "hash-mismatch",
        capsys,
        plan.manifest_bytes + b" ",
        plan.manifest_sha256,
    ) == "HASH"
    oversized = plan.manifest_bytes + (
        b" " * mpadapter._VERIFY_MANIFEST_MAX_BYTES)
    assert _run_validate_manifest_protocol(
        tmp_path / "oversized",
        capsys,
        oversized,
        hashlib.sha256(oversized).hexdigest(),
    ) == "MANIFEST"
    assert _run_validate_manifest_protocol(
        tmp_path / "bad-digest", capsys, plan.manifest_bytes, "00" * 8) == (
            "MANIFEST")


def test_adapter_waits_for_boot_only_between_sessions(tmp_path):
    sleeps = []
    twin = _DeviceTwin(tmp_path, lambda boot: _smoke_lines("1.4.0"))
    twin._probe_source_text = "probe-source"
    adapter = mpadapter.MpremoteReleaseAdapter(
        lambda: twin,
        probe_source="probe-source",
        boot_wait_s=7.5,
        sleep=sleeps.append,
    )
    _seed_confirmed_device(twin, _plan("1.3.0", legacy=True))

    apply_release(_plan("1.4.0"), adapter)

    assert sleeps == [7.5, 7.5]


def test_first_release_session_enters_boot_only_repl_before_staging(tmp_path):
    twin = _DeviceTwin(tmp_path, lambda boot: _smoke_lines("1.4.0"))
    adapter = mpadapter.MpremoteReleaseAdapter(
        lambda: twin,
        probe_source="probe-source",
        boot_wait_s=0,
        sleep=lambda _seconds: None,
    )

    clean_resets_seen = adapter.run_session(
        lambda _session: twin.clean_resets)

    assert clean_resets_seen == 1
    assert twin.clean_resets == 1


def _seed_confirmed_device(twin, plan, extra_files=()):
    for asset in plan.assets:
        if asset.role == "bootstrap_fixed":
            twin.write_file("/" + asset.relative_path, asset.payload)
        elif asset.role == "seed_if_absent":
            twin.write_file("/sd/" + asset.relative_path, asset.payload)
    for location, payload in extra_files:
        twin.write_file(location, payload)
    slot_root = bootenv.SLOT_BASE + "/A"
    twin.write_file(
        slot_root + "/" + bootenv.MANIFEST_NAME, plan.manifest_bytes)
    twin.write_file(
        slot_root + "/" + mpadapter.OWNER_MARKER_NAME,
        owner_marker_payload(plan.release_id, plan.manifest_sha256))
    for asset in plan.assets:
        if asset.role == "managed_release" and asset.zone == "sd":
            twin.write_file(
                slot_root + "/" + asset.relative_path, asset.payload)
    store = bootsel.SelectorStore(
        str(twin._map("/sys/sel.0")), str(twin._map("/sys/sel.1")))
    store.write(bootsel.SelectorData(
        0,
        bootsel.SlotEntry(
            "A", plan.release_id,
            binascii.unhexlify(plan.manifest_sha256)),
        None, 0, False, (), False))
    twin.reset()
    twin.sessions = 0
    twin.resets = 0
    twin.closes = 0
    twin.write_paths = []


def _read_selector(twin):
    store = bootsel.SelectorStore(
        str(twin._map("/sys/sel.0")), str(twin._map("/sys/sel.1")))
    return store.read()


def test_happy_path_release_end_to_end(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    sentinels = {
        "/sd/settings.json": b'{"brightness":73,"user":true}\n',
        "/sd/vars.json": b'{"answer":42}\n',
        "/sd/Add-ons/user_pack.py": b"# user add-on\n",
    }
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, old_plan, sentinels.items())

    result = apply_release(new_plan, adapter)

    assert result == new_plan.release_id
    selector = _read_selector(twin)
    assert selector.confirmed.release_id == new_plan.release_id
    assert selector.confirmed.name == "B"
    assert selector.trial is None
    assert selector.retired == ()
    assert selector.confirmation_pending is False
    slot_b = tmp_path / "sd" / ".slots" / "B"
    assert (slot_b / bootenv.MANIFEST_NAME).read_bytes() == (
        new_plan.manifest_bytes)
    assert not (tmp_path / "sd" / ".staging" / new_plan.release_id).exists()
    assert not (tmp_path / "sd" / ".slots" / "A" / "legacy.py").exists()
    for location, payload in sentinels.items():
        assert twin.read_file(location) == payload
    assert twin.sessions == 3
    assert twin.resets == 3
    assert twin.clean_resets == 3
    assert twin.closes == 3
    assert sum(
        code is mpadapter.RELEASE_CONTROL_COLLECT_CODE
        for code, _limit in twin.limited_calls
    ) == 2
    batch_remove = getattr(mpadapter, "OWNED_TREE_REMOVE_BATCH_CODE", None)
    assert batch_remove is not None
    assert sum(
        code is batch_remove for code, _limit in twin.limited_calls
    ) == 1
    assert sum(
        code is mpadapter.VERIFY_SLOT_CODE
        for code, _limit in twin.limited_calls
    ) == 3


def test_fast_release_syncs_only_managed_changes_in_the_confirmed_slot(
        tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    sentinels = {
        "/sd/settings.json": b'{"brightness":73,"user":true}\n',
        "/sd/vars.json": b'{"answer":42}\n',
        "/sd/Add-ons/user_pack.py": b"# user add-on\n",
        "/sd/.slots/A/notes.txt": b"private notes\n",
    }
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, old_plan, sentinels.items())

    result = release_deploy.apply_fast_release(new_plan, adapter)

    assert result == new_plan.release_id
    selector = _read_selector(twin)
    assert selector.confirmed.name == "A"
    assert selector.confirmed.release_id == new_plan.release_id
    assert not twin.exists("/sd/.slots/B")
    assert not twin.exists("/sd/.slots/A/legacy.py")
    for location, payload in sentinels.items():
        assert twin.read_file(location) == payload
    root = "/sd/.slots/A/"
    assert [path for path in twin.write_paths if path.startswith(root)] == [
        root + "catalog.py",
        root + "main.py",
        root + "version.py",
        root + bootenv.MANIFEST_NAME,
        root + mpadapter.OWNER_MARKER_NAME,
    ]
    assert twin.sessions == 1
    assert twin.resets == 1


def test_fast_release_does_not_retransmit_an_unchanged_release(tmp_path):
    plan = _plan("1.4.0")
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, plan)

    assert release_deploy.apply_fast_release(plan, adapter) == plan.release_id

    assert not any(
        path.startswith("/sd/.slots/A/") for path in twin.write_paths)
    assert twin.sessions == 1
    assert twin.resets == 1


def test_fast_release_rejects_an_untrusted_owner_before_writing(tmp_path):
    plan = _plan("1.4.0")
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, plan)
    twin.write_file("/sd/.slots/A/" + mpadapter.OWNER_MARKER_NAME, b"bad")
    twin.write_paths = []

    with pytest.raises(ValueError, match="owner marker"):
        release_deploy.apply_fast_release(plan, adapter)

    assert twin.write_paths == []
    assert twin.sessions == 1
    assert twin.resets == 1


def test_fast_release_preserves_an_unowned_new_managed_path(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, old_plan)
    path = "/sd/.slots/A/catalog.py"
    twin.write_file(path, b"# user upload\n")
    twin.write_paths = []

    with pytest.raises(ValueError, match="unowned file"):
        release_deploy.apply_fast_release(new_plan, adapter)

    assert twin.read_file(path) == b"# user upload\n"
    assert twin.write_paths == []
    assert _read_selector(twin).confirmed.release_id == old_plan.release_id


def test_fast_release_requires_transactional_first_provision_without_confirmed_slot(
        tmp_path):
    plan = _plan("1.4.0")
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))

    with pytest.raises(
            ValueError, match=r"--transactional"):
        release_deploy.apply_fast_release(plan, adapter)

    assert twin.write_paths == []
    assert twin.sessions == 1
    assert twin.resets == 1


def test_interrupted_fast_release_does_not_commit_the_new_selector(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, old_plan)
    write_file = twin.write_file

    def interrupt_manifest(path, payload):
        if path.endswith("/" + bootenv.MANIFEST_NAME):
            raise KeyboardInterrupt("transfer interrupted")
        write_file(path, payload)

    twin.write_file = interrupt_manifest

    with pytest.raises(KeyboardInterrupt, match="transfer interrupted"):
        release_deploy.apply_fast_release(new_plan, adapter)

    selector = _read_selector(twin)
    assert selector.confirmed.release_id == old_plan.release_id
    assert (binascii.hexlify(selector.confirmed.manifest_sha256).decode()
            == old_plan.manifest_sha256)


def test_trial_smoke_failure_rejects_the_trial(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    calls = []

    def probe(boot):
        calls.append(boot.slot_ref.name if boot.slot_ref else None)
        if len(calls) == 1:
            raise RuntimeError("device smoke failed")
        return _smoke_lines("1.3.0")

    adapter, twin = _adapter_and_twin(tmp_path, probe)
    _seed_confirmed_device(twin, old_plan)

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "smoke_trial"
    selector = _read_selector(twin)
    assert selector.confirmed.release_id == old_plan.release_id
    assert selector.trial is None


def test_confirmed_smoke_failure_rolls_back_confirmation(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    attempts = []

    def probe(boot):
        attempts.append(1)
        if len(attempts) == 2:
            raise RuntimeError("confirmed smoke failed")
        return _smoke_lines("1.4.0")

    adapter, twin = _adapter_and_twin(tmp_path, probe)
    _seed_confirmed_device(twin, old_plan)

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "smoke_confirmed"
    selector = _read_selector(twin)
    assert selector.confirmed.release_id == old_plan.release_id
    assert selector.confirmation_pending is False

    apply_release(new_plan, adapter)

    selector = _read_selector(twin)
    assert selector.confirmed.release_id == new_plan.release_id
    assert selector.retired == ()


def test_staging_verification_failure_never_arms_the_trial(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")

    def corrupt(twin):
        target = (
            tmp_path / "sd" / ".staging" / new_plan.release_id
            / "catalog.py")
        target.write_bytes(b"# corrupted in transit\n")

    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    twin.staging_mutator = corrupt
    _seed_confirmed_device(twin, old_plan)

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "verify"
    selector = _read_selector(twin)
    assert selector.confirmed.release_id == old_plan.release_id
    assert selector.trial is None
    assert (tmp_path / "sd" / ".staging" / new_plan.release_id).exists()


def test_bootstrap_mismatch_refuses_the_release_before_slot_writes(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, old_plan)
    twin.write_file("/boot.py", b"# tampered anchor\n")

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "bootstrap"
    selector = _read_selector(twin)
    assert selector.confirmed.release_id == old_plan.release_id
    assert not (tmp_path / "sd" / ".staging" / new_plan.release_id).exists()


def test_reset_failure_reports_failure_but_keeps_the_armed_trial(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, old_plan)
    twin.fail_reset = True

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "reset"
    selector = _read_selector(twin)
    assert selector.trial is not None
    assert selector.trial.release_id == new_plan.release_id

    apply_release(new_plan, adapter)

    selector = _read_selector(twin)
    assert selector.confirmed.release_id == new_plan.release_id


def test_foreign_staging_sibling_survives_owned_release(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, old_plan)
    twin.write_file("/sd/.staging/foreign/keep.txt", b"do not remove\n")

    apply_release(new_plan, adapter)

    assert twin.read_file("/sd/.staging/foreign/keep.txt") == (
        b"do not remove\n")
    assert not (tmp_path / "sd" / ".staging" / new_plan.release_id).exists()


def test_occupied_candidate_slot_is_not_replaced(tmp_path):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    adapter, twin = _adapter_and_twin(
        tmp_path, lambda boot: _smoke_lines("1.4.0"))
    _seed_confirmed_device(twin, old_plan)
    twin.write_file("/sd/.slots/B/foreign.txt", b"leave this alone\n")

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "activate_trial"
    assert twin.read_file("/sd/.slots/B/foreign.txt") == (
        b"leave this alone\n")
    selector = _read_selector(twin)
    assert selector.confirmed.release_id == old_plan.release_id
    assert selector.trial is None


def test_retired_cleanup_refuses_unknown_slot_content(tmp_path):
    plan = _plan("1.3.0", legacy=True)
    session, twin = _connected_session(tmp_path, plan)
    ref = SlotRef("A", plan.release_id, plan.manifest_sha256)
    twin.write_file("/sd/.slots/A/foreign/keep.txt", b"do not remove\n")
    selector = bootsel.SelectorData(
        0,
        None,
        None,
        0,
        False,
        (bootsel.SlotEntry(
            "A", plan.release_id,
            binascii.unhexlify(plan.manifest_sha256)),),
        False,
    )

    with pytest.raises(ValueError, match="unknown content"):
        session._erase_retired(selector, ref)

    assert twin.read_file("/sd/.slots/A/foreign/keep.txt") == (
        b"do not remove\n")
    assert selector.retired[0].name == "A"


def test_owned_cleanup_preserves_memory_error_identity():
    plan = _plan("1.4.0")
    failure = MemoryError("owned cleanup OOM")

    class _OOMDevice:
        def exec_limited(self, code, max_output_bytes, **params):
            raise failure

    trees = mpadapter._OwnedReleaseTrees(_OOMDevice(), lambda *args: None)
    spec = mpadapter._owned_tree_spec(
        "/sd/.slots/A", plan.release_id,
        plan.manifest_sha256, plan.manifest_bytes)

    with pytest.raises(MemoryError) as caught:
        trees._root_receipt(spec)

    assert caught.value is failure


class _ManifestReceiptDevice:
    def __init__(self, response):
        self.response = response
        self.limited_calls = []
        self.read_file_calls = 0

    def exec_limited(self, code, max_output_bytes, **params):
        self.limited_calls.append((code, max_output_bytes, params))
        return self.response

    def read_file(self, path):
        self.read_file_calls += 1
        return None


def test_hash_receipt_rejects_over_cap_padding_before_strip():
    response = "H001000" + " " * (
        mpadapter._HASH_RECEIPT_MAX_OUTPUT_BYTES - len("H001000") + 1)
    device = _ManifestReceiptDevice(response)

    with pytest.raises(ValueError, match="hash receipt"):
        mpadapter.stream_hash_receipt(
            device, (("/boot.py", "0" * 64),))

    assert device.limited_calls[0][1] == mpadapter._HASH_RECEIPT_MAX_OUTPUT_BYTES


def _connected_session(tmp_path, plan):
    twin = _DeviceTwin(tmp_path, lambda boot: _smoke_lines("1.4.0"))
    twin._probe_source_text = "probe-source"
    twin.connect()
    slot_root = bootenv.SLOT_BASE + "/A"
    twin.write_file(
        slot_root + "/" + bootenv.MANIFEST_NAME, plan.manifest_bytes)
    twin.write_file(
        slot_root + "/" + mpadapter.OWNER_MARKER_NAME,
        owner_marker_payload(plan.release_id, plan.manifest_sha256))
    for asset in plan.assets:
        if asset.role == "managed_release" and asset.zone == "sd":
            twin.write_file(
                slot_root + "/" + asset.relative_path, asset.payload)
    return mpadapter._MpremoteSession(twin, "probe-source"), twin


def test_slot_verification_and_manifest_validation_use_bounded_exec(tmp_path):
    plan = _plan("1.4.0")
    session, twin = _connected_session(tmp_path, plan)
    ref = SlotRef("A", plan.release_id, plan.manifest_sha256)

    assert session._validate_slot_manifest(ref, plan.manifest_bytes) is None
    session._verify_slot_assets(bootenv.SLOT_BASE + "/A", plan)

    assert (mpadapter.VALIDATE_MANIFEST_CODE,
            mpadapter.VALIDATE_MANIFEST_RECEIPT_MAX_BYTES
            ) in twin.limited_calls
    assert (mpadapter.VERIFY_SLOT_CODE,
            mpadapter.VERIFY_SLOT_RECEIPT_MAX_BYTES) in twin.limited_calls
    assert mpadapter.VERIFY_SLOT_RECEIPT_MAX_BYTES >= (
        len("MISSING ") + mpadapter._VERIFY_PATH_MAX_CHARS)
    assert twin.read_file_calls == 0


def test_slot_verification_uses_dedicated_bounded_timeout_when_available():
    plan = _plan("1.4.0")

    class _TimedReceiptDevice(_ManifestReceiptDevice):
        def __init__(self):
            super().__init__("OK")
            self.timed_calls = []

        def exec_limited_timeout(
                self, code, max_output_bytes, timeout_s, **params):
            self.timed_calls.append(
                (code, max_output_bytes, timeout_s, params))
            return self.response

    device = _TimedReceiptDevice()
    session = mpadapter._MpremoteSession(device, "probe-source")

    session._verify_slot_assets(bootenv.SLOT_BASE + "/A", plan)

    assert device.limited_calls == []
    code, cap, timeout_s, params = device.timed_calls[0]
    assert code is mpadapter.VERIFY_SLOT_CODE
    assert cap == mpadapter.VERIFY_SLOT_RECEIPT_MAX_BYTES
    assert timeout_s == mpadapter.VERIFY_SLOT_TIMEOUT_S
    assert params["manifest_sha256"] == plan.manifest_sha256


def test_timed_bounded_exec_passes_timeout_and_rejects_raw_error_output():
    class _RawTransport:
        def __init__(self):
            self.calls = []
            self.error_output = b""

        def exec_raw(self, code, timeout, data_consumer):
            self.calls.append((code, timeout))
            data_consumer(b"OK\x04")
            return b"", self.error_output

    device = mpadapter.MpremoteDevice("COM-test")
    device._transport = _RawTransport()

    assert device.exec_limited_timeout(
        "print('OK')", 16, 60) == "OK"
    assert device._transport.calls == [("print('OK')", 60)]

    device._transport.error_output = b"raw stderr"
    with pytest.raises(OSError, match="raw stderr"):
        device.exec_limited_timeout("raise RuntimeError", 16, 60)


def test_cleanup_reset_enters_boot_only_raw_repl():
    class _RawTransport:
        def __init__(self):
            self.soft_resets = []

        def enter_raw_repl(self, soft_reset):
            self.soft_resets.append(soft_reset)

    device = mpadapter.MpremoteDevice("COM-test")
    device._transport = _RawTransport()

    device.reset_to_boot_repl()

    assert device._transport.soft_resets == [True]


def test_device_reuses_created_directories_across_file_uploads():
    class _FsTransport:
        def __init__(self):
            self.directories = set()
            self.mkdir_calls = []
            self.writes = []

        def fs_mkdir(self, path):
            self.mkdir_calls.append(path)
            if path in self.directories:
                raise OSError("already exists")
            self.directories.add(path)

        def fs_writefile(self, path, payload):
            self.writes.append((path, payload))

    device = mpadapter.MpremoteDevice("COM-test")
    device._transport = _FsTransport()

    device.write_file("/sd/.staging/release/calc/a.mpy", b"a")
    device.write_file("/sd/.staging/release/calc/b.mpy", b"b")

    assert device._transport.mkdir_calls == [
        "/sd",
        "/sd/.staging",
        "/sd/.staging/release",
        "/sd/.staging/release/calc",
    ]
    assert device._transport.writes == [
        ("/sd/.staging/release/calc/a.mpy", b"a"),
        ("/sd/.staging/release/calc/b.mpy", b"b"),
    ]


@pytest.mark.parametrize("response,message", (
    ("MISSING", "slot manifest is missing"),
    ("HASH", "slot manifest hash mismatch"),
    ("MANIFEST", "slot manifest validation failed"),
    ("UNEXPECTED", "slot manifest validation failed"),
))
def test_validate_slot_manifest_maps_each_device_receipt(response, message):
    plan = _plan("1.4.0")
    device = _ManifestReceiptDevice(response)
    session = mpadapter._MpremoteSession(device, "probe-source")
    ref = SlotRef("A", plan.release_id, plan.manifest_sha256)

    with pytest.raises(ValueError, match=message):
        session._validate_slot_manifest(ref)

    assert device.read_file_calls == 0
    code, cap, params = device.limited_calls[0]
    assert code is mpadapter.VALIDATE_MANIFEST_CODE
    assert cap == mpadapter.VALIDATE_MANIFEST_RECEIPT_MAX_BYTES
    assert params["manifest_sha256"] == plan.manifest_sha256


def test_validate_slot_manifest_accepts_ok_and_checks_expected_bytes():
    plan = _plan("1.4.0")
    device = _ManifestReceiptDevice("OK")
    session = mpadapter._MpremoteSession(device, "probe-source")
    ref = SlotRef("A", plan.release_id, plan.manifest_sha256)

    assert session._validate_slot_manifest(ref, plan.manifest_bytes) is None

    with pytest.raises(ValueError, match="slot manifest bytes mismatch"):
        session._validate_slot_manifest(ref, plan.manifest_bytes + b" ")
    assert len(device.limited_calls) == 1
    assert device.read_file_calls == 0


def test_session_source_has_no_unbounded_device_exec():
    source = inspect.getsource(mpadapter._MpremoteSession)
    device_source = inspect.getsource(mpadapter.MpremoteDevice)

    assert "self._device.exec(" not in source
    assert "self._device.exec_limited(" in source
    assert "remove_tree" not in source
    assert "remove_tree" not in device_source
    assert "def rename(" not in device_source


def test_release_smoke_cleanup_drops_raw_probe_functions_before_collecting():
    code = mpadapter.RELEASE_CONTROL_COLLECT_CODE

    collect_at = code.index("gc.collect()")
    for name in ("_viper_identity", "_resident_binding", "run", "micropython"):
        assert code.index("g.pop('" + name + "',None)") < collect_at


def test_adapter_closes_failed_connect_without_masking_primary_error():
    class _ConnectFailureDevice:
        def __init__(self):
            self.connect_error = MemoryError("connect OOM")
            self.close_error = OSError("close failed")
            self.closes = 0

        def connect(self):
            raise self.connect_error

        def close(self):
            self.closes += 1
            raise self.close_error

    device = _ConnectFailureDevice()
    adapter = mpadapter.MpremoteReleaseAdapter(
        lambda: device, probe_source="probe-source", boot_wait_s=0)
    operations = []

    with pytest.raises(MemoryError) as caught:
        adapter.run_session(lambda session: operations.append(session))

    assert caught.value is device.connect_error
    assert device.closes == 1
    assert operations == []
