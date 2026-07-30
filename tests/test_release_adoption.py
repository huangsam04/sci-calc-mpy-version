# Host behaviour tests for trusted first-takeover adoption.  The twin models
# only the bounded raw-REPL operations used by release_adoption; ordinary
# device reads and unrestricted exec are deliberately unavailable.
import ast
import binascii
from dataclasses import replace
import hashlib

import bootenv
import bootlog
import bootsel
import pytest

from tools import release_adoption
from tools import release_device_mpremote as mpadapter
from tools import release_plan
from tools.release_plan import ReleaseTreeSnapshot, plan_release
from tools.release_protocol import OWNER_MARKER_NAME, owner_marker_payload


BASELINE_130 = {
    "boot.py": b"# 1.3.0 boot\n",
    "main.py": b"# 1.3.0 internal main\n",
    "sdcard.py": b"# 1.3.0 sdcard driver\n",
    "recovery.py": b"# 1.3.0 recovery\n",
    "display/mono_palette.py": b"# 1.3.0 palette\n",
    "display/ssd1322.py": b"# 1.3.0 display driver\n",
}


def _baseline_hashes():
    return {
        path: hashlib.sha256(payload).hexdigest()
        for path, payload in BASELINE_130.items()
    }


def _new_plan():
    files = [
        ("boot.py", b"# new boot chain\n"),
        ("internal_main.py", b"# new supervisor shim\n"),
        ("sdcard.py", BASELINE_130["sdcard.py"]),
        ("bootsel.py", b"# codec\n"),
        ("bootlog.py", b"# boot log\n"),
        ("bootsupervisor.py", b"# supervisor\n"),
        ("bootenv.py", b"# environment\n"),
        ("recovery.py", BASELINE_130["recovery.py"]),
        ("display/mono_palette.py", BASELINE_130["display/mono_palette.py"]),
        ("display/ssd1322.py", BASELINE_130["display/ssd1322.py"]),
        ("main.py", b"# app main\n"),
        ("launch.py", b"# slot launcher\n"),
        ("version.py", b'VERSION = "1.4.0"\n'),
        ("settings.json", b"{}\n"),
        ("vars.json", b"{}\n"),
    ]
    return plan_release(
        ReleaseTreeSnapshot.from_files(files), mode="source")


def _rebuilt_plan(base_plan, assets):
    """Re-derive a manifest so an edited asset tuple stays self-consistent."""
    payload = {
        "abi_tag": base_plan.abi_tag,
        "app_version": base_plan.app_version,
        "assets": [
            release_plan._asset_record(asset) for asset in assets
            if asset.role in ("bootstrap_fixed", "managed_release")],
        "mode": base_plan.mode,
        "product": release_plan.PRODUCT,
        "schema": release_plan.SCHEMA_VERSION,
        "seeds": [
            release_plan._asset_record(asset) for asset in assets
            if asset.role == "seed_if_absent"],
    }
    release_id = hashlib.sha256(
        release_plan._canonical_json(payload)).hexdigest()
    manifest = dict(payload)
    manifest["release_id"] = release_id
    manifest_bytes = release_plan._canonical_json(manifest)
    return release_plan.ReleasePlan(
        schema=release_plan.SCHEMA_VERSION,
        app_version=base_plan.app_version,
        mode=base_plan.mode,
        abi_tag=base_plan.abi_tag,
        release_id=release_id,
        assets=tuple(assets),
        manifest_bytes=manifest_bytes,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def _with_bootstrap_replaced(plan, original_source_path, **changes):
    assets = tuple(
        replace(asset, **changes)
        if (asset.role == "bootstrap_fixed"
            and asset.source_path == original_source_path)
        else asset
        for asset in plan.assets)
    return _rebuilt_plan(plan, assets)


class _AdoptionTwin:
    def __init__(self, files=None):
        self.files = {}
        # The production device has an already-mounted SD root before
        # adoption; child directories must still be created one level at a
        # time by the bounded control programs.
        self.directories = {"/", "/sd"}
        for path, payload in (files or {}).items():
            self._add_parent_directories(path)
            self.files[path] = bytes(payload)
        self.write_log = []
        self.rename_log = []
        self.hash_requests = []
        self.hash_output_limits = []
        self.hash_chunk_sizes = []
        self.control_calls = []
        self.read_file_calls = []
        self.fail_on_write = None
        self.fail_on_commit = None
        self.foreign_before_commit = None
        self.atomic_stat_error = None
        self.rename_stat_error = None

    def _add_parent_directories(self, path):
        parts = path.strip("/").split("/")
        for index in range(1, len(parts)):
            self.directories.add("/" + "/".join(parts[:index]))

    def _children(self, path):
        prefix = "/" if path == "/" else path + "/"
        names = set()
        for collection in (self.files, self.directories):
            for candidate in collection:
                if candidate == path or not candidate.startswith(prefix):
                    continue
                remainder = candidate[len(prefix):]
                if "/" not in remainder:
                    names.add(remainder)
        return names

    def read_file(self, path):
        self.read_file_calls.append(path)
        return self.files.get(path)

    def write_file(self, path, data):
        if (self.fail_on_write is not None
                and path.startswith(self.fail_on_write)):
            raise OSError("flash write failed")
        self._add_parent_directories(path)
        self.files[path] = bytes(data)
        self.write_log.append(path)

    def _hash_paths(self, params):
        pairs = ast.literal_eval(params["pairs"])
        self.hash_requests.append(pairs)
        matched = 0
        missing = 0
        for index, (path, expected) in enumerate(pairs):
            payload = self.files.get(path)
            if payload is None:
                missing |= 1 << index
                continue
            digest = hashlib.sha256()
            for start in range(0, len(payload), 512):
                chunk = payload[start:start + 512]
                self.hash_chunk_sizes.append(len(chunk))
                digest.update(chunk)
            if digest.hexdigest() == expected:
                matched |= 1 << index
        return "H{0:03x}{1:03x}".format(matched, missing)

    def _directory_audit(self, params):
        path = ast.literal_eval(params["path"])
        allowed = set(ast.literal_eval(params["allowed"]))
        if path in self.files or path not in self.directories:
            return "F" if path in self.files else "M"
        names = self._children(path)
        if len(names) > 16 or any(name not in allowed for name in names):
            return "F"
        return "E" if not names else "D"

    def _directory_count(self, params):
        path = ast.literal_eval(params["path"])
        if path in self.files or path not in self.directories:
            return "F" if path in self.files else "M"
        names = self._children(path)
        if len(names) > release_adoption._SLOT_DIRECTORY_ENTRY_LIMIT:
            return "F"
        return "N{0:03x}".format(len(names))

    def _entry_kind(self, params):
        path = ast.literal_eval(params["path"])
        if path in self.directories:
            return "D"
        if path in self.files:
            return "R"
        return "M"

    def _create_directory(self, params):
        path = ast.literal_eval(params["path"])
        if path in self.files:
            return "F"
        if path in self.directories:
            return "D"
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in self.directories:
            return "F"
        self.directories.add(path)
        return "C"

    def _atomic_create(self, params):
        path = ast.literal_eval(params["path"])
        if path == self.atomic_stat_error:
            return "F"
        if path in self.files:
            return "E"
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in self.directories:
            return "F"
        self.write_file(path, binascii.unhexlify(params["payload_hex"]))
        return "C"

    def _conditional_rename(self, params):
        src = ast.literal_eval(params["src"])
        dst = ast.literal_eval(params["dst"])
        expected = set(ast.literal_eval(params["expected"]))
        wanted = params["wanted"]
        if self.fail_on_commit is not None and dst.startswith(self.fail_on_commit):
            raise OSError("commit interrupted")
        if dst == self.rename_stat_error:
            return "IO"
        if self.foreign_before_commit == dst:
            self.write_file(dst, b"foreign concurrent content")
            self.foreign_before_commit = None
        payload = self.files.get(src)
        if payload is None or hashlib.sha256(payload).hexdigest() != wanted:
            return "SOURCE"
        previous = self.files.get(dst)
        if (previous is not None
                and hashlib.sha256(previous).hexdigest() not in expected):
            return "CONFLICT"
        self._add_parent_directories(dst)
        self.files[dst] = self.files.pop(src)
        self.rename_log.append((src, dst))
        self.write_log.append(dst)
        return "RENAMED"

    def _selector_winner(self):
        best = None
        best_index = -1
        for index, path in enumerate(bootenv.SELECTOR_PATHS):
            record = bootsel.unpack_record(self.files.get(path))
            if (record is not None
                    and (best is None or record.generation > best.generation)):
                best = record
                best_index = index
        return best, best_index

    def _boot_winner(self):
        best = None
        for path in bootenv.BOOTLOG_PATHS:
            record = bootlog.unpack_record(self.files.get(path))
            if (record is not None
                    and (best is None or record.generation > best.generation)):
                best = record
        return best

    def _selector_read(self):
        record, _index = self._selector_winner()
        if record is None:
            return "NONE"
        return binascii.hexlify(bootsel.pack_record(record)).decode()

    def _boot_read(self):
        record = self._boot_winner()
        if record is None:
            return "NONE"
        return binascii.hexlify(bootlog.pack_record(record)).decode()

    def _selector_write(self, params):
        fields = ast.literal_eval(params["fields"])

        def ref(value):
            if value is None:
                return None
            return bootsel.SlotEntry(
                value[0], value[1], binascii.unhexlify(value[2]))

        winner, winner_index = self._selector_winner()
        generation = 1 if winner is None else winner.generation + 1
        trial = ref(fields[1])
        trial_generation = fields[2]
        if trial is not None and trial_generation == 0:
            trial_generation = generation
        stored = bootsel.SelectorData(
            generation,
            ref(fields[0]),
            trial,
            trial_generation,
            fields[3],
            tuple(ref(value) for value in fields[4]),
            fields[5],
        )
        target = (
            bootenv.SELECTOR_PATHS[1]
            if winner_index == 0 else bootenv.SELECTOR_PATHS[0])
        payload = bootsel.pack_record(stored)
        self.write_file(target, payload)
        return binascii.hexlify(payload).decode()

    def exec(self, code, **params):
        raise AssertionError("unbounded device exec is forbidden in adoption")

    def exec_limited(self, code, max_output_bytes, **params):
        if code is mpadapter.HASH_PATHS_CODE:
            self.hash_output_limits.append(max_output_bytes)
            return self._hash_paths(params)
        self.control_calls.append((code, max_output_bytes, params))
        if code is mpadapter.SELECTOR_READ_CODE:
            return self._selector_read()
        if code is mpadapter.BOOTLOG_READ_CODE:
            return self._boot_read()
        if code is mpadapter.SELECTOR_WRITE_CODE:
            return self._selector_write(params)
        if code is release_adoption.DIRECTORY_AUDIT_CODE:
            return self._directory_audit(params)
        if code is release_adoption.DIRECTORY_COUNT_CODE:
            return self._directory_count(params)
        if code is release_adoption.ENTRY_KIND_CODE:
            return self._entry_kind(params)
        if code is release_adoption.CREATE_DIRECTORY_CODE:
            return self._create_directory(params)
        if code is release_adoption.ATOMIC_CREATE_CODE:
            return self._atomic_create(params)
        if code is release_adoption.CONDITIONAL_RENAME_CODE:
            return self._conditional_rename(params)
        raise AssertionError("unexpected bounded control operation")


def _baseline_device():
    return _AdoptionTwin(
        {"/" + path: payload for path, payload in BASELINE_130.items()})


def _assert_rejected_without_device_io(bad_plan, match):
    twin = _baseline_device()
    with pytest.raises(
            (ValueError, release_adoption.AdoptionError), match=match):
        release_adoption.adopt_device(
            twin, bad_plan, baseline_hashes=_baseline_hashes())
    assert not twin.hash_requests
    assert not twin.write_log
    assert not twin.rename_log
    assert not twin.control_calls
    assert not twin.read_file_calls


def test_adoption_stages_a_trial_slot_before_main_then_boot():
    twin = _baseline_device()
    twin.write_file("/sd/Add-ons/private.py", b"private user code")
    twin.write_file("/sd/settings.json", b'{"user":true}\n')
    twin.write_log.clear()
    admission = release_adoption.prepare_adoption(
        _new_plan(), baseline_hashes=_baseline_hashes())

    receipt = release_adoption.adopt_prepared_device(twin, admission)

    assert receipt.changed is True
    for asset in admission.bootstrap:
        assert twin.files["/" + asset.relative_path] == asset.payload
    for item in admission.slot_files:
        assert twin.files[item.path] == item.payload
    selector = bootsel.unpack_record(twin.files["/sys/sel.0"])
    assert selector is not None
    assert selector.confirmed is None
    assert selector.trial.name == "A"
    assert selector.trial.release_id == admission.release_id
    assert twin.files["/sd/Add-ons/private.py"] == b"private user code"
    assert twin.files["/sd/settings.json"] == b'{"user":true}\n'
    assert not any(path.startswith("/sd/settings.json") for path in twin.write_log)

    root_commits = [
        dst for _src, dst in twin.rename_log if not dst.startswith("/sd/")]
    assert root_commits[-2:] == ["/main.py", "/boot.py"]
    first_root_commit = min(
        twin.rename_log.index(entry)
        for entry in twin.rename_log
        if not entry[1].startswith("/sd/"))
    assert any(
        dst == "/sys/sel.0" for _src, dst in twin.rename_log) is False
    assert any(
        item.path == "/sd/.slots/A/release.manifest"
        for item in admission.slot_files)
    assert any(
        item.path == "/sd/.slots/A/" + OWNER_MARKER_NAME
        and item.payload == owner_marker_payload(
            admission.release_id, admission.manifest_sha256)
        for item in admission.slot_files)
    assert not twin.read_file_calls
    assert first_root_commit > 0


def test_adoption_upgrades_an_exact_trusted_transitional_bootstrap(
        monkeypatch):
    transitional_payload = b"# trusted transitional environment\n"
    transitional_digest = hashlib.sha256(transitional_payload).hexdigest()
    transitional_main = b"# trusted transitional main\n"
    transitional_main_digest = hashlib.sha256(
        transitional_main).hexdigest()
    transitional_pairs = (
        ("/bootenv.py", transitional_digest),
        ("/main.py", transitional_main_digest),
    )
    monkeypatch.setattr(
        release_adoption,
        "_TRUSTED_TRANSITIONAL_PAIRS",
        transitional_pairs,
        raising=False,
    )
    plan = _new_plan()
    expected = {
        asset.relative_path: asset.payload for asset in plan.assets
        if asset.role == "bootstrap_fixed"
    }
    twin = _baseline_device()
    twin.write_file("/bootenv.py", transitional_payload)
    twin.write_file("/main.py", transitional_main)
    twin.write_file("/boot.py", expected["boot.py"])
    twin.write_file(
        "/sys/sel.0",
        bootsel.pack_record(bootsel.SelectorData(
            3, None, None, 0, False, (), False)))
    twin.write_file(
        "/sys/boot.0",
        bootlog.pack_record(bootlog.BootEntry(36, 3, None, None)))
    twin.write_log.clear()
    admission = release_adoption.prepare_adoption(
        plan, baseline_hashes=_baseline_hashes())

    receipt = release_adoption.adopt_prepared_device(twin, admission)

    assert receipt.changed is True
    assert twin.files["/bootenv.py"] == expected["bootenv.py"]
    assert twin.files["/main.py"] == expected["main.py"]
    assert admission.baseline_sha256 == release_adoption._pairs_sha256(
        admission.baseline_pairs + transitional_pairs)


def test_adoption_rejects_unlisted_transitional_bootstrap_before_writing():
    twin = _baseline_device()
    twin.write_file("/bootenv.py", b"foreign transitional content\n")
    before = dict(twin.files)
    twin.write_log.clear()

    with pytest.raises(
            release_adoption.AdoptionError,
            match="foreign boot module conflict: /bootenv.py"):
        release_adoption.adopt_device(
            twin, _new_plan(), baseline_hashes=_baseline_hashes())

    assert twin.files == before
    assert not twin.write_log
    assert not twin.rename_log


def test_unjournaled_current_anchor_without_a_transition_still_fails_closed():
    plan = _new_plan()
    current_boot = next(
        asset.payload for asset in plan.assets
        if asset.role == "bootstrap_fixed"
        and asset.relative_path == "boot.py")
    twin = _baseline_device()
    twin.write_file("/boot.py", current_boot)
    before = dict(twin.files)
    twin.write_log.clear()

    with pytest.raises(
            release_adoption.AdoptionError,
            match="partial root handoff has no trusted journal"):
        release_adoption.adopt_device(
            twin, plan, baseline_hashes=_baseline_hashes())

    assert twin.files == before
    assert not twin.write_log
    assert not twin.rename_log


def test_trusted_transition_does_not_adopt_a_nonempty_selector_namespace(
        monkeypatch):
    transitional_payload = b"# trusted transitional environment\n"
    transitional_digest = hashlib.sha256(transitional_payload).hexdigest()
    monkeypatch.setattr(
        release_adoption,
        "_TRUSTED_TRANSITIONAL_PAIRS",
        (("/bootenv.py", transitional_digest),),
    )
    plan = _new_plan()
    current_boot = next(
        asset.payload for asset in plan.assets
        if asset.role == "bootstrap_fixed"
        and asset.relative_path == "boot.py")
    foreign = bootsel.SlotEntry("A", "foreign", b"x" * 32)
    twin = _baseline_device()
    twin.write_file("/bootenv.py", transitional_payload)
    twin.write_file("/boot.py", current_boot)
    twin.write_file(
        "/sys/sel.0",
        bootsel.pack_record(bootsel.SelectorData(
            3, foreign, None, 0, False, (), False)))
    before = dict(twin.files)
    twin.write_log.clear()

    with pytest.raises(
            release_adoption.AdoptionError,
            match="system namespace already exists"):
        release_adoption.adopt_device(
            twin, plan, baseline_hashes=_baseline_hashes())

    assert twin.files == before
    assert not twin.write_log
    assert not twin.rename_log


@pytest.mark.parametrize("release_id,manifest_sha256", (
    ("0" * 63, "1" * 64),
    ("0" * 64, "1" * 63),
    ("G" * 64, "1" * 64),
    ("0" * 64, "G" * 64),
    (b"0" * 64, "1" * 64),
))
def test_owner_marker_payload_rejects_noncanonical_identity(
        release_id, manifest_sha256):
    with pytest.raises(ValueError, match="ownership identity"):
        owner_marker_payload(release_id, manifest_sha256)


def test_owner_marker_payload_is_a_fixed_content_bound_ascii_record():
    assert owner_marker_payload("0" * 64, "1" * 64) == (
        b"SCI-CALC-OWNER-1\n"
        + b"release=" + b"0" * 64 + b"\n"
        + b"manifest=" + b"1" * 64 + b"\n"
    )


def test_adoption_is_idempotent_after_root_transaction_is_complete():
    plan = _new_plan()
    first = _baseline_device()
    release_adoption.adopt_device(
        first, plan, baseline_hashes=_baseline_hashes())
    adopted_files = dict(first.files)

    second = _AdoptionTwin(adopted_files)
    receipt = release_adoption.adopt_prepared_device(
        second,
        release_adoption.prepare_adoption(
            plan, baseline_hashes=_baseline_hashes()),
    )

    assert receipt.changed is False
    assert second.files == adopted_files
    assert not second.write_log
    assert not second.rename_log
    assert not second.read_file_calls


def test_completed_root_handoff_does_not_re_adopt_a_later_application_plan():
    first_plan = _new_plan()
    changed_payload = b"# app main v2\n"
    changed_assets = tuple(
        replace(
            asset,
            payload=changed_payload,
            size=len(changed_payload),
            sha256=hashlib.sha256(changed_payload).hexdigest(),
        )
        if asset.key == "sd:main"
        else asset
        for asset in first_plan.assets
    )
    next_plan = _rebuilt_plan(first_plan, changed_assets)
    first_admission = release_adoption.prepare_adoption(
        first_plan, baseline_hashes=_baseline_hashes())
    next_admission = release_adoption.prepare_adoption(
        next_plan, baseline_hashes=_baseline_hashes())
    assert next_admission.bootstrap_sha256 == first_admission.bootstrap_sha256
    assert next_admission.release_id != first_admission.release_id

    twin = _baseline_device()
    release_adoption.adopt_prepared_device(twin, first_admission)
    adopted_files = dict(twin.files)
    twin.write_log.clear()
    twin.rename_log.clear()

    receipt = release_adoption.adopt_prepared_device(twin, next_admission)

    assert receipt.changed is False
    assert twin.files == adopted_files
    assert not twin.write_log
    assert not twin.rename_log
    assert not twin.read_file_calls


def test_exact_bootstrap_without_current_journal_enters_normal_release():
    first_plan = _new_plan()
    changed_payload = b"# app main after journal retirement\n"
    next_plan = _rebuilt_plan(
        first_plan,
        tuple(
            replace(
                asset,
                payload=changed_payload,
                size=len(changed_payload),
                sha256=hashlib.sha256(changed_payload).hexdigest(),
            )
            if asset.key == "sd:main" else asset
            for asset in first_plan.assets
        ),
    )
    first_admission = release_adoption.prepare_adoption(
        first_plan, baseline_hashes=_baseline_hashes())
    next_admission = release_adoption.prepare_adoption(
        next_plan, baseline_hashes=_baseline_hashes())
    assert next_admission.bootstrap_sha256 == first_admission.bootstrap_sha256

    twin = _baseline_device()
    release_adoption.adopt_prepared_device(twin, first_admission)
    for phase in ("claim", "slots", "staged", "armed", "root"):
        path, _digest = release_adoption._phase_pair(first_admission, phase)
        twin.files.pop(path, None)
    adopted_files = dict(twin.files)
    twin.write_log.clear()
    twin.rename_log.clear()

    receipt = release_adoption.adopt_prepared_device(twin, next_admission)

    assert receipt.changed is False
    assert twin.files == adopted_files
    assert not twin.write_log
    assert not twin.rename_log


@pytest.mark.parametrize("path,payload,match", (
    ("/sys/sel.0", b"tampered selector", "initial selector"),
    ("/sd/.slots/A/launch.py", b"tampered slot payload", "initial slot"),
))
def test_completed_adoption_rechecks_selector_and_slot_before_receipt(
        path, payload, match):
    plan = _new_plan()
    first = _baseline_device()
    admission = release_adoption.prepare_adoption(
        plan, baseline_hashes=_baseline_hashes())
    release_adoption.adopt_prepared_device(first, admission)

    second = _AdoptionTwin(dict(first.files))
    second.write_file(path, payload)
    second.write_log.clear()

    with pytest.raises(release_adoption.AdoptionError, match=match):
        release_adoption.adopt_prepared_device(second, admission)

    assert not second.rename_log
    assert not second.write_log


def test_completed_adoption_rejects_unknown_nested_slot_content():
    plan = _new_plan()
    first = _baseline_device()
    admission = release_adoption.prepare_adoption(
        plan, baseline_hashes=_baseline_hashes())
    release_adoption.adopt_prepared_device(first, admission)

    second = _AdoptionTwin(dict(first.files))
    second.write_file("/sd/.slots/A/unknown/note.py", b"foreign")
    second.write_log.clear()

    with pytest.raises(release_adoption.AdoptionError, match="unknown content"):
        release_adoption.adopt_prepared_device(second, admission)

    assert not second.rename_log
    assert not second.write_log


def test_interrupted_initial_slot_file_commit_resumes_from_its_bound_temp():
    twin = _baseline_device()
    admission = release_adoption.prepare_adoption(
        _new_plan(), baseline_hashes=_baseline_hashes())
    twin.fail_on_commit = "/sd/.slots/A/"

    with pytest.raises(release_adoption.AdoptionError, match="conditional"):
        release_adoption.adopt_prepared_device(twin, admission)

    temporary_paths = {
        release_adoption._temporary_path(item.path, item.sha256)
        for item in admission.slot_files
    }
    assert any(path in twin.files for path in temporary_paths)

    twin.fail_on_commit = None
    receipt = release_adoption.adopt_prepared_device(twin, admission)

    assert receipt.changed is True
    assert not any(path in twin.files for path in temporary_paths)


def test_admission_rejects_slot_file_below_a_bound_temporary_path():
    plan = _new_plan()
    target = next(
        asset for asset in plan.assets
        if asset.role == "managed_release" and asset.zone == "sd")
    temporary = release_adoption._temporary_path(
        "/sd/.slots/A/" + target.relative_path, target.sha256)
    relative_path = (
        temporary[len("/sd/.slots/A/"):] + "/child.py")
    collision = release_plan._asset(
        "sd:temporary-child",
        "temporary_child.py",
        "source/temporary_child.py",
        b"# collision\n",
        "sd",
        relative_path,
        release_plan.SOURCE_MODE,
        "managed_release",
    )

    with pytest.raises(
            release_adoption.AdoptionError,
            match="temporary path collision"):
        release_adoption.prepare_adoption(
            _rebuilt_plan(plan, plan.assets + (collision,)),
            baseline_hashes=_baseline_hashes(),
        )


def test_adoption_control_scripts_fail_closed_for_nonmissing_stat_errors():
    assert "if code!=17:" in release_adoption.CREATE_DIRECTORY_CODE
    assert "if code!=2:" in release_adoption.ATOMIC_CREATE_CODE
    assert "if code==2:" in release_adoption.CONDITIONAL_RENAME_CODE

    compile(
        release_adoption.ATOMIC_CREATE_CODE.format(
            path=repr("/sys/marker"), payload_hex="00"),
        "atomic-create", "exec")
    compile(
        release_adoption.CONDITIONAL_RENAME_CODE.format(
            src=repr("/source"), dst=repr("/destination"),
            expected=repr(("0" * 64,)), wanted="1" * 64),
        "conditional-rename", "exec")


def test_root_marker_is_recreated_only_from_a_complete_authenticated_handoff():
    plan = _new_plan()
    twin = _baseline_device()
    admission = release_adoption.prepare_adoption(
        plan, baseline_hashes=_baseline_hashes())
    release_adoption.adopt_prepared_device(twin, admission)
    root_marker, _digest = release_adoption._phase_pair(admission, "root")
    del twin.files[root_marker]

    receipt = release_adoption.adopt_prepared_device(twin, admission)

    assert receipt.changed is True
    assert root_marker in twin.files


def test_interrupted_root_commit_keeps_legacy_boot_and_main_then_resumes():
    twin = _baseline_device()
    admission = release_adoption.prepare_adoption(
        _new_plan(), baseline_hashes=_baseline_hashes())
    twin.fail_on_commit = "/main.py"

    with pytest.raises(release_adoption.AdoptionError, match="conditional"):
        release_adoption.adopt_prepared_device(twin, admission)

    assert twin.files["/boot.py"] == BASELINE_130["boot.py"]
    assert twin.files["/main.py"] == BASELINE_130["main.py"]
    assert "/sd/.slots/A/release.manifest" in twin.files
    assert "/sys/sel.0" in twin.files

    twin.fail_on_commit = None
    receipt = release_adoption.adopt_prepared_device(twin, admission)

    assert receipt.changed is True
    assert twin.files["/boot.py"] == b"# new boot chain\n"
    assert twin.files["/main.py"] == b"# new supervisor shim\n"


def test_conditional_root_commit_refuses_a_concurrent_foreign_destination():
    twin = _baseline_device()
    admission = release_adoption.prepare_adoption(
        _new_plan(), baseline_hashes=_baseline_hashes())
    twin.foreign_before_commit = "/main.py"

    with pytest.raises(release_adoption.AdoptionError, match="conditional"):
        release_adoption.adopt_prepared_device(twin, admission)

    assert twin.files["/main.py"] == b"foreign concurrent content"
    assert twin.files["/boot.py"] == BASELINE_130["boot.py"]


def test_adoption_refuses_a_foreign_content_bound_temp_without_overwrite():
    plan = _new_plan()
    admission = release_adoption.prepare_adoption(
        plan, baseline_hashes=_baseline_hashes())
    twin = _baseline_device()
    target = next(
        asset for asset in admission.bootstrap
        if asset.relative_path == "boot.py")
    temp = release_adoption._temporary_path("/boot.py", target.sha256)
    twin.write_file(temp, b"unknown temp")
    before = dict(twin.files)

    with pytest.raises(release_adoption.AdoptionError, match="temporary"):
        release_adoption.adopt_prepared_device(twin, admission)

    assert twin.files == before
    assert not twin.rename_log


def test_adoption_refuses_foreign_system_or_slot_namespaces_before_writing():
    for path in ("/sys/user-note", "/sd/.slots/foreign"):
        twin = _baseline_device()
        twin.write_file(path, b"user data")
        before = dict(twin.files)

        with pytest.raises(release_adoption.AdoptionError, match="namespace"):
            release_adoption.adopt_device(
                twin, _new_plan(), baseline_hashes=_baseline_hashes())

        assert twin.files == before
        assert not twin.rename_log


def test_adoption_refuses_an_existing_selector_before_root_change():
    twin = _baseline_device()
    twin.write_file("/sys/sel.0", b"foreign selector")
    before = dict(twin.files)

    with pytest.raises(release_adoption.AdoptionError, match="namespace"):
        release_adoption.adopt_device(
            twin, _new_plan(), baseline_hashes=_baseline_hashes())

    assert twin.files == before


def test_admission_rejects_a_forged_instance_without_device_io():
    admission = release_adoption.prepare_adoption(
        _new_plan(), baseline_hashes=_baseline_hashes())
    forged = replace(admission, bootstrap_pairs=admission.bootstrap_pairs[:-1])
    twin = _baseline_device()

    with pytest.raises(ValueError, match="capability"):
        release_adoption.adopt_prepared_device(twin, forged)

    assert not twin.hash_requests
    assert not twin.write_log
    assert not twin.control_calls


def test_admission_rejects_a_tampered_baseline_without_mutating():
    twin = _baseline_device()
    twin.files["/recovery.py"] = b"# tampered\n"
    before = dict(twin.files)

    with pytest.raises(release_adoption.AdoptionError, match="baseline"):
        release_adoption.adopt_device(
            twin, _new_plan(), baseline_hashes=_baseline_hashes())

    assert twin.files == before
    assert not twin.write_log
    assert not twin.rename_log


def test_admission_rejects_a_missing_baseline_anchor_without_mutating():
    twin = _baseline_device()
    del twin.files["/recovery.py"]
    before = dict(twin.files)

    with pytest.raises(
            release_adoption.AdoptionError,
            match="baseline anchor is missing"):
        release_adoption.adopt_device(
            twin, _new_plan(), baseline_hashes=_baseline_hashes())

    assert twin.files == before
    assert not twin.write_log


def test_admission_rejects_missing_extra_and_invalid_bootstrap_anchors():
    plan = _new_plan()
    missing = tuple(
        asset for asset in plan.assets
        if not (asset.role == "bootstrap_fixed"
                and asset.source_path == "bootsel.py"))
    _assert_rejected_without_device_io(
        _rebuilt_plan(plan, missing), "missing bootstrap anchor")

    extra = release_plan._asset(
        "bootstrap:extra", "extra.py", "source/extra.py", b"# extra\n",
        "internal", "extra.py", release_plan.SOURCE_MODE, "bootstrap_fixed")
    _assert_rejected_without_device_io(
        _rebuilt_plan(plan, plan.assets + (extra,)),
        "unexpected bootstrap anchor")

    _assert_rejected_without_device_io(
        _with_bootstrap_replaced(plan, "boot.py", zone="sd"),
        "zone must be internal")
    _assert_rejected_without_device_io(
        _with_bootstrap_replaced(plan, "boot.py", relative_path="boot2.py"),
        "device path mismatch")


class _ExplosiveRepr:
    def __repr__(self):
        raise AssertionError("untrusted repr must not run")


def test_admission_never_formats_an_untrusted_bootstrap_source_path():
    plan = _new_plan()
    malformed = _with_bootstrap_replaced(
        plan, "boot.py", source_path=_ExplosiveRepr())
    _assert_rejected_without_device_io(malformed, "unexpected bootstrap anchor")


class _GeneratorBaseline:
    def __init__(self, count):
        self._count = count
        self.yields = 0

    def items(self):
        for index in range(self._count):
            self.yields += 1
            yield ("path%02d.py" % index, "a" * 64)


def test_baseline_normalization_bounds_a_hostile_items_generator():
    mapping = _GeneratorBaseline(25)

    with pytest.raises(ValueError, match="too large"):
        release_adoption.prepare_adoption(
            _new_plan(), baseline_hashes=mapping)

    assert mapping.yields <= mpadapter.HASH_RECEIPT_MAX_PAIRS + 1


@pytest.mark.parametrize("path", (
    "b" + chr(0xE4) + "d.py",
    "d" * (release_adoption.BASELINE_PATH_MAX_BYTES + 1),
    "/boot.py",
    "../boot.py",
    "boot:py",
    "boot.py/",
))
def test_baseline_normalization_rejects_unsafe_paths(path):
    hashes = _baseline_hashes()
    hashes[path] = "b" * 64

    with pytest.raises(ValueError, match="invalid trusted baseline"):
        release_adoption.prepare_adoption(
            _new_plan(), baseline_hashes=hashes)


def test_large_baseline_file_uses_bounded_streaming_hashes_only():
    twin = _baseline_device()
    large_recovery = b"R" * 1500
    twin.files["/recovery.py"] = large_recovery
    baseline_hashes = _baseline_hashes()
    baseline_hashes["recovery.py"] = hashlib.sha256(
        large_recovery).hexdigest()

    release_adoption.adopt_prepared_device(
        twin,
        release_adoption.prepare_adoption(
            _new_plan(), baseline_hashes=baseline_hashes),
    )

    assert twin.hash_requests
    assert set(twin.hash_output_limits) == {
        mpadapter._HASH_RECEIPT_MAX_OUTPUT_BYTES}
    assert max(twin.hash_chunk_sizes) == 512
    assert "readinto(buf)" in mpadapter.HASH_PATHS_CODE
    assert "f.read(512)" not in mpadapter.HASH_PATHS_CODE
    assert "view=None\nbuf=None\nh=None\nstream=None\ngc.collect()" in (
        mpadapter.HASH_PATHS_CODE)
    assert not twin.read_file_calls


def test_adoption_uses_the_pinned_com6_baseline_by_default():
    assert release_adoption.BASELINE_130_HASHES == {
        "boot.py": "e918c98fdf02faf11d6af325eec8c42399a070281a9988a9672b9774665b5af4",
        "main.py": "44fe63c3a3f98c1a8f2779addc11df4c6a31dd54d422e6828bf2e211e5d61ab0",
        "sdcard.py": "d2d4b98ed0d466c49a6c121c90a313d782defb59483a931b1ce1aae7904b60ea",
        "recovery.py": "46feef5addb3039b94b765d4b01291c8e5a442c0adda6b53f48ee5992d180cd1",
        "display/mono_palette.py": "5f9804a6dc8be3451e5e9ab6d3a35ef0a29889582b079a8cd5b796ba73f219a7",
        "display/ssd1322.py": "4065905c2d3c6f77709606c4c09a15ef20964022d7756f55fa6e948cfdfb86e6",
    }
