# Integration test for the deployment sequence over the device twin.
import ast
from dataclasses import replace
import hashlib

import pytest

import bootsel
from tools import release_deploy
from tools.release_plan import ReleaseTreeSnapshot, plan_release

from test_release_device_mpremote import _DeviceTwin, _smoke_lines


BASELINE_130 = {
    "boot.py": b"# 1.3.0 boot\n",
    "main.py": b"# 1.3.0 internal main\n",
    "sdcard.py": b"# 1.3.0 sdcard driver\n",
    "recovery.py": b"# 1.3.0 recovery\n",
    "display/mono_palette.py": b"# 1.3.0 palette\n",
    "display/ssd1322.py": b"# 1.3.0 display driver\n",
}


def _project(tmp_path):
    source = tmp_path / "source"
    (source / "display").mkdir(parents=True)
    (source / "fonts").mkdir()
    files = {
        "boot.py": b"# new boot chain\n",
        "internal_main.py": b"# new supervisor shim\n",
        "sdcard.py": BASELINE_130["sdcard.py"],
        "bootsel.py": b"# codec\n",
        "bootlog.py": b"# boot log\n",
        "bootsupervisor.py": b"# supervisor\n",
        "bootenv.py": b"# environment\n",
        "recovery.py": BASELINE_130["recovery.py"],
        "display/mono_palette.py": BASELINE_130["display/mono_palette.py"],
        "display/ssd1322.py": BASELINE_130["display/ssd1322.py"],
        "main.py": b"# app main\n",
        "version.py": b'VERSION = "1.4.0"\n',
        "settings.json": b"{}\n",
        "vars.json": b"{}\n",
    }
    for path, payload in files.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    from test_release_build import _fake_font_c
    for name, width, height in (
            ("Bally7x9", 7, 9), ("Neato5x7", 5, 7), ("FixedFont5x8", 5, 8)):
        (source / "fonts" / (name + ".c")).write_bytes(
            _fake_font_c(width, height))
    return tmp_path, files


def _compiler(calls):
    def compile_module(source_path, output_path):
        from pathlib import Path
        calls.append(Path(source_path).name)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"MPY" + Path(source_path).name.encode())
    return compile_module


def test_release_compiler_strips_unused_source_line_tables(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(
        release_deploy.subprocess, "run",
        lambda command, capture_output: calls.append(command) or Result())

    release_deploy._mpy_cross_compiler("mpy-cross")("input.py", "output.mpy")

    assert calls == [(
        "mpy-cross", "-march=xtensawin", "-X", "no-source-lines",
        "-s", "input.py", "-o", "output.mpy", "input.py",
    )]


def _baseline_hashes():
    return {
        path: hashlib.sha256(payload).hexdigest()
        for path, payload in BASELINE_130.items()
    }


# Device double whose transport never opens; close must still happen.
class _ConnectRefusedDevice:
    def __init__(self, fail_close=False):
        self._fail_close = fail_close
        self.connects = 0
        self.resets = 0
        self.closes = 0
        self.connect_error = None
        self.close_error = None

    def connect(self):
        self.connects += 1
        self.connect_error = OSError("connect refused")
        raise self.connect_error

    def reset(self):
        self.resets += 1

    def close(self):
        self.closes += 1
        if self._fail_close:
            self.close_error = OSError("close failed")
            raise self.close_error


# Device double answering every hash query with one fixed receipt; a
# receipt_text of None fails the adoption, fail_close fails every close.
class _ScriptedDevice:
    def __init__(self, receipt_text, fail_close=False):
        self._receipt_text = receipt_text
        self._fail_close = fail_close
        self.connects = 0
        self.resets = 0
        self.closes = 0
        self.close_error = None

    def connect(self):
        self.connects += 1

    def exec_limited(self, code, max_output_bytes, **params):
        if self._receipt_text is None:
            raise OSError("device receipt refused")
        if self._receipt_text == "ALL_MATCH":
            if code is release_deploy.release_device_mpremote.HASH_PATHS_CODE:
                pairs = ast.literal_eval(params["pairs"])
                return "H%03x000" % ((1 << len(pairs)) - 1)
            if code is release_deploy.release_adoption.DIRECTORY_AUDIT_CODE:
                return "D"
        return self._receipt_text

    def reset(self):
        self.resets += 1

    def close(self):
        self.closes += 1
        if self._fail_close:
            self.close_error = OSError("close failed")
            raise self.close_error


def test_deploy_sequence_adopts_then_applies_end_to_end(tmp_path):
    project_root, files = _project(tmp_path / "project")
    twin_root = tmp_path / "device"
    twin_root.mkdir()
    twin = _DeviceTwin(twin_root, lambda boot: _smoke_lines("1.4.0"))
    for path, payload in BASELINE_130.items():
        twin.write_file("/" + path, payload)
    probe_source = "probe-source"
    twin._probe_source_text = probe_source

    lines = []
    from tools.release_device_mpremote import MpremoteReleaseAdapter
    release_id = release_deploy.run(
        "TWIN",
        "source",
        _compiler([]),
        lambda: twin,
        project_root=project_root,
        baseline_hashes=_baseline_hashes(),
        adapter_factory=lambda factory: MpremoteReleaseAdapter(
            factory, probe_source=probe_source, boot_wait_s=0),
        boot_wait_s=0,
        emit=lines.append,
    )

    bootstrap_sha = next(
        line.split(" ", 1)[1] for line in lines
        if line.startswith("RELEASE_ADMISSION_BOOTSTRAP_SHA256 "))
    baseline_sha = next(
        line.split(" ", 1)[1] for line in lines
        if line.startswith("RELEASE_ADMISSION_BASELINE_SHA256 "))
    assert ("RELEASE_ADOPTION_RECEIPT " + bootstrap_sha + " "
            + baseline_sha + " applied") in lines
    assert any(line.startswith("RELEASE_APPLIED ") for line in lines)
    assert twin.read_file("/boot.py") == files["boot.py"]
    assert twin.read_file("/main.py") == files["internal_main.py"]

    store = bootsel.SelectorStore(
        str(twin_root / "sys" / "sel.0"), str(twin_root / "sys" / "sel.1"))
    selector = store.read()
    assert selector.confirmed.release_id == release_id
    assert selector.retired == ()
    assert selector.confirmation_pending is False
    manifest_path = (
        twin_root / "sd" / ".slots" / "A" / "release.manifest")
    assert manifest_path.is_file()
    assert twin.resets >= 4
    assert twin.closes >= 4


def test_fast_deploy_skips_repeated_adoption(monkeypatch, tmp_path):
    project_root, _files = _project(tmp_path / "project")
    device_calls = []
    applied = []
    lines = []

    def unexpected_adoption_device():
        device_calls.append(True)
        raise AssertionError("fast deploy must not open an adoption session")

    def apply(plan, adapter):
        applied.append((plan, adapter))
        return plan.release_id

    monkeypatch.setattr(release_deploy, "apply_release", apply)

    release_id = release_deploy.run(
        "TWIN",
        "source",
        _compiler([]),
        unexpected_adoption_device,
        project_root=project_root,
        adapter_factory=lambda factory: ("adapter", factory),
        boot_wait_s=0,
        emit=lines.append,
        adopt=False,
    )

    assert release_id == applied[0][0].release_id
    assert applied[0][1] == ("adapter", unexpected_adoption_device)
    assert device_calls == []
    assert "RELEASE_ADOPTION_SKIPPED already-provisioned" in lines
    assert not any(
        line.startswith("RELEASE_ADOPTION_RECEIPT ") for line in lines)


def test_run_uses_fast_release_when_transactional_mode_is_disabled(
        monkeypatch, tmp_path):
    project_root, _files = _project(tmp_path / "project")
    applied = []

    def fast_apply(plan, adapter):
        applied.append((plan, adapter))
        return plan.release_id

    monkeypatch.setattr(release_deploy, "apply_fast_release", fast_apply)
    monkeypatch.setattr(
        release_deploy, "apply_release",
        lambda plan, adapter: pytest.fail("transactional release was used"))

    release_id = release_deploy.run(
        "TWIN",
        "source",
        _compiler([]),
        lambda: pytest.fail("unexpected direct device contact"),
        project_root=project_root,
        adapter_factory=lambda factory: ("adapter", factory),
        boot_wait_s=0,
        emit=lambda line: None,
        adopt=False,
        transactional=False,
    )

    assert release_id == applied[0][0].release_id
    assert applied[0][1][0] == "adapter"


def test_run_forwards_boot_wait_to_the_default_release_adapter(
        monkeypatch, tmp_path):
    project_root, _files = _project(tmp_path / "project")
    adapters = []

    def make_adapter(factory, boot_wait_s):
        adapter = (factory, boot_wait_s)
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(
        release_deploy.release_device_mpremote,
        "MpremoteReleaseAdapter", make_adapter)
    monkeypatch.setattr(
        release_deploy, "apply_fast_release",
        lambda plan, adapter: plan.release_id)

    release_deploy.run(
        "TWIN",
        "source",
        _compiler([]),
        lambda: None,
        project_root=project_root,
        boot_wait_s=3,
        emit=lambda line: None,
        adopt=False,
        transactional=False,
    )

    assert len(adapters) == 1
    assert adapters[0][1] == 3


def test_deploy_rejects_an_invalid_plan_before_creating_a_device(
        monkeypatch, tmp_path):
    project_root, _ = _project(tmp_path / "project")
    plans = release_deploy.release_build.prepare_release_plans(
        project_root, _compiler([]))
    invalid_plan = replace(plans.source, manifest_sha256="0" * 64)
    monkeypatch.setattr(
        release_deploy.release_build,
        "prepare_release_plans",
        lambda *_args, **_kwargs: replace(plans, source=invalid_plan),
    )
    factory_calls = []

    def device_factory():
        factory_calls.append("called")
        raise AssertionError("invalid plan must not create a device")

    with pytest.raises(ValueError, match="manifest"):
        release_deploy.run(
            "TWIN",
            "source",
            _compiler([]),
            device_factory,
            project_root=project_root,
            baseline_hashes=_baseline_hashes(),
            boot_wait_s=0,
        )

    assert not factory_calls


def test_deploy_connect_failure_closes_device_without_boot_wait(
        monkeypatch, tmp_path):
    project_root, _ = _project(tmp_path / "project")
    devices = []

    def device_factory():
        device = _ConnectRefusedDevice(fail_close=True)
        devices.append(device)
        return device

    sleeps = []
    monkeypatch.setattr(release_deploy.time, "sleep", sleeps.append)

    lines = []
    with pytest.raises(OSError) as excinfo:
        release_deploy.run(
            "TWIN",
            "source",
            _compiler([]),
            device_factory,
            project_root=project_root,
            baseline_hashes=_baseline_hashes(),
            boot_wait_s=9.0,
            emit=lines.append,
        )

    assert excinfo.value is devices[0].connect_error
    assert sleeps == []
    assert devices[0].closes == 1
    assert devices[0].close_error is not None
    assert devices[0].resets == 0
    assert len(devices) == 2
    assert devices[1].closes == 1
    assert devices[1].resets == 0


def test_deploy_factory_failure_does_not_attempt_recovery_contact(tmp_path):
    project_root, _ = _project(tmp_path / "project")
    factory_error = OSError("factory refused")
    factory_calls = []

    def device_factory():
        factory_calls.append("called")
        raise factory_error

    with pytest.raises(OSError) as excinfo:
        release_deploy.run(
            "TWIN",
            "source",
            _compiler([]),
            device_factory,
            project_root=project_root,
            baseline_hashes=_baseline_hashes(),
            boot_wait_s=0,
            emit=lambda _line: None,
        )

    assert excinfo.value is factory_error
    assert factory_calls == ["called"]


def test_deploy_sleep_interruption_closes_without_masking_interrupt(
        monkeypatch, tmp_path):
    project_root, _ = _project(tmp_path / "project")
    initial = _ScriptedDevice("unused", fail_close=True)
    recovery = _ScriptedDevice("unused")
    devices = [initial, recovery]

    def device_factory():
        return devices.pop(0)

    def already_current(_device, admission):
        return release_deploy.release_adoption.AdoptionReceipt(
            admission.release_id,
            admission.manifest_sha256,
            admission.bootstrap_sha256,
            admission.baseline_sha256,
            False,
        )

    sleep_error = KeyboardInterrupt("boot wait interrupted")

    def interrupt_sleep(_seconds):
        raise sleep_error

    monkeypatch.setattr(
        release_deploy.release_adoption,
        "adopt_prepared_device",
        already_current,
    )
    monkeypatch.setattr(release_deploy.time, "sleep", interrupt_sleep)

    with pytest.raises(KeyboardInterrupt) as excinfo:
        release_deploy.run(
            "TWIN",
            "source",
            _compiler([]),
            device_factory,
            project_root=project_root,
            baseline_hashes=_baseline_hashes(),
            boot_wait_s=9.0,
            emit=lambda _line: None,
        )

    assert excinfo.value is sleep_error
    assert initial.connects == 1
    assert initial.resets == 1
    assert initial.closes == 1
    assert initial.close_error is not None
    assert recovery.connects == 1
    assert recovery.resets == 1
    assert recovery.closes == 1
    assert not devices


def test_deploy_host_side_admission_failure_never_contacts_device(
        monkeypatch, tmp_path):
    project_root, _ = _project(tmp_path / "project")

    def rejected_admission(plan, baseline_hashes=None):
        raise release_deploy.release_adoption.AdoptionError(
            "admission rejected")

    monkeypatch.setattr(
        release_deploy.release_adoption,
        "prepare_adoption",
        rejected_admission,
    )
    factory_calls = []

    def device_factory():
        factory_calls.append("called")
        raise AssertionError("a rejected admission must not create a device")

    lines = []
    with pytest.raises(
            release_deploy.release_adoption.AdoptionError,
            match="admission rejected"):
        release_deploy.run(
            "TWIN",
            "source",
            _compiler([]),
            device_factory,
            project_root=project_root,
            baseline_hashes=_baseline_hashes(),
            boot_wait_s=0,
            emit=lines.append,
        )

    assert not factory_calls
    assert not lines


def test_deploy_close_failure_without_primary_error_propagates(
        monkeypatch, tmp_path):
    project_root, _ = _project(tmp_path / "project")
    plans = release_deploy.release_build.prepare_release_plans(
        project_root, _compiler([]))
    admission = release_deploy.release_adoption.prepare_adoption(
        plans.source, baseline_hashes=_baseline_hashes())
    devices = []

    def device_factory():
        device = _ScriptedDevice("ALL_MATCH", fail_close=True)
        devices.append(device)
        return device

    def already_current(_device, verified_admission):
        return release_deploy.release_adoption.AdoptionReceipt(
            verified_admission.release_id,
            verified_admission.manifest_sha256,
            verified_admission.bootstrap_sha256,
            verified_admission.baseline_sha256,
            False,
        )

    monkeypatch.setattr(
        release_deploy.release_adoption,
        "adopt_prepared_device",
        already_current,
    )

    lines = []
    with pytest.raises(OSError, match="close failed") as excinfo:
        release_deploy.run(
            "TWIN",
            "source",
            _compiler([]),
            device_factory,
            project_root=project_root,
            baseline_hashes=_baseline_hashes(),
            boot_wait_s=0,
            emit=lines.append,
        )

    assert excinfo.value is devices[0].close_error
    assert devices[0].closes == 1
    assert devices[0].resets == 1
    assert ("RELEASE_ADOPTION_RECEIPT " + admission.bootstrap_sha256 + " "
            + admission.baseline_sha256 + " already-current") in lines


def test_deploy_close_failure_does_not_mask_primary_adoption_error(tmp_path):
    project_root, _ = _project(tmp_path / "project")
    devices = []

    def device_factory():
        device = _ScriptedDevice(None, fail_close=True)
        devices.append(device)
        return device

    lines = []
    with pytest.raises(
            release_deploy.release_adoption.AdoptionError,
            match="bounded device SHA verification failed"):
        release_deploy.run(
            "TWIN",
            "source",
            _compiler([]),
            device_factory,
            project_root=project_root,
            baseline_hashes=_baseline_hashes(),
            boot_wait_s=0,
            emit=lines.append,
        )

    assert devices[0].closes == 1
    assert devices[0].resets == 1
    assert not any(
        line.startswith("RELEASE_ADOPTION_RECEIPT ") for line in lines)


def test_deploy_emits_admission_evidence_before_any_device_contact(tmp_path):
    project_root, _ = _project(tmp_path / "project")
    plans = release_deploy.release_build.prepare_release_plans(
        project_root, _compiler([]))
    admission = release_deploy.release_adoption.prepare_adoption(
        plans.source, baseline_hashes=_baseline_hashes())
    lines = []
    devices = []

    def device_factory():
        lines.append("DEVICE_CONTACT")
        device = _ConnectRefusedDevice()
        devices.append(device)
        return device

    with pytest.raises(OSError):
        release_deploy.run(
            "TWIN",
            "source",
            _compiler([]),
            device_factory,
            project_root=project_root,
            baseline_hashes=_baseline_hashes(),
            boot_wait_s=0,
            emit=lines.append,
        )

    assert lines[:5] == [
        "RELEASE_ADMISSION_PLAN " + plans.source.release_id,
        "RELEASE_ADMISSION_MANIFEST_SHA256 " + plans.source.manifest_sha256,
        "RELEASE_ADMISSION_BOOTSTRAP_SHA256 " + admission.bootstrap_sha256,
        "RELEASE_ADMISSION_BASELINE_SHA256 " + admission.baseline_sha256,
        "DEVICE_CONTACT",
    ]
    assert not any(
        line.startswith("RELEASE_ADOPTION_RECEIPT ") for line in lines)
