# Host behaviour tests for the production mpremote release adapter.
# The device twin maps device paths onto a real temp filesystem and runs
# the same boot codecs plus the supervisor decision logic, so the adapter
# is exercised through its real wire format end to end.
import ast
import binascii
import hashlib
import shutil

import pytest

import bootenv
import bootlog
import bootsel
import bootsupervisor
from tools import release_device_mpremote as mpadapter
from tools.release_apply import ReleaseFailure, apply_release
from tools.release_plan import ReleaseTreeSnapshot, plan_release


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
        self.closes = 0
        self.fail_reset = False
        self.fail_close = False
        self.staging_mutator = None
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
        mapped = self._map(path)
        if not mapped.is_file():
            return None
        return mapped.read_bytes()

    def write_file(self, path, data):
        mapped = self._map(path)
        mapped.parent.mkdir(parents=True, exist_ok=True)
        mapped.write_bytes(bytes(data))

    def exists(self, path):
        return self._map(path).exists()

    def makedirs(self, path):
        self._map(path).mkdir(parents=True, exist_ok=True)

    def remove_tree(self, path):
        shutil.rmtree(self._map(path), ignore_errors=True)

    def rename(self, src, dst):
        import os
        mapped_dst = self._map(dst)
        if mapped_dst.exists():
            if mapped_dst.is_dir():
                shutil.rmtree(mapped_dst)
            else:
                mapped_dst.unlink()
        os.rename(self._map(src), mapped_dst)

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
            for path, expected in ast.literal_eval(params["pairs"]):
                mapped = self._map(path)
                if not mapped.is_file():
                    return "MISSING " + path
                actual = hashlib.sha256(mapped.read_bytes()).hexdigest()
                if actual != expected:
                    return "HASH " + path
            return "OK"
        if code is mpadapter.VERIFY_SLOT_CODE:
            if self.staging_mutator is not None:
                self.staging_mutator(self)
                self.staging_mutator = None
            return self._verify_slot(params)
        if code is mpadapter.RMTREE_CODE:
            self.remove_tree(params["path"])
            return "OK"
        if code is mpadapter.RENAME_CODE:
            self.rename(params["src"], params["dst"])
            return "OK"
        if "mkdir" in code:
            import re
            match = re.search(r"os\.mkdir\('([^']+)'\)", code)
            if match:
                self._map(match.group(1)).mkdir(parents=True, exist_ok=True)
            return "OK"
        if code == self._probe_source_text:
            return self._probe(self._last_boot)
        raise AssertionError("unexpected device exec: " + code[:60])

    _probe_source_text = None

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
        lambda: twin, probe_source="probe-source")
    return adapter, twin


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
    assert not (tmp_path / "sd" / ".staging").exists()
    assert not (tmp_path / "sd" / ".slots" / "A" / "legacy.py").exists()
    for location, payload in sentinels.items():
        assert twin.read_file(location) == payload
    assert twin.sessions == 3
    assert twin.resets == 3
    assert twin.closes == 3


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
        target = tmp_path / "sd" / ".staging" / "catalog.py"
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
    assert not (tmp_path / "sd" / ".staging").exists()


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
    assert not (tmp_path / "sd" / ".staging").exists()


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
