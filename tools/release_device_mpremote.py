# Production mpremote release adapter.
# Drives a real device through the A/B slot transaction: selector and
# boot-log records travel as hexlified codec frames, so the device and the
# host validate the exact same bytes with the exact same code. File
# staging lands in /sd/.staging and is renamed into the candidate slot
# only after device-side SHA-256 verification passes.
import binascii
import hashlib
from pathlib import Path

import bootenv
import bootlog
import bootsel
from tools.release_plan import MPY_ABI_TAG, SOURCE_ABI_TAG
from tools.release_protocol import (
    ColdBootObservation,
    ReleaseSmokeResult,
    SelectionTicket,
    SlotRef,
    run_guarded_session,
)

_SEL0, _SEL1 = bootenv.SELECTOR_PATHS
_LOG0, _LOG1 = bootenv.BOOTLOG_PATHS
_STAGING_ROOT = "/sd/.staging"

SELECTOR_READ_CODE = (
    "import bootsel,binascii\n"
    "d=bootsel.SelectorStore('" + _SEL0 + "','" + _SEL1 + "').read()\n"
    "print(binascii.hexlify(bootsel.pack_record(d)).decode()"
    " if d else 'NONE')")

SELECTOR_WRITE_CODE = (
    "import bootsel,binascii\n"
    "fields={fields}\n"
    "def _ref(r):\n"
    "    return None if r is None else bootsel.SlotEntry(\n"
    "        r[0],r[1],binascii.unhexlify(r[2]))\n"
    "d=bootsel.SelectorData(0,_ref(fields[0]),_ref(fields[1]),\n"
    "    fields[2],fields[3],tuple(_ref(r) for r in fields[4]),fields[5])\n"
    "s=bootsel.SelectorStore('" + _SEL0 + "','" + _SEL1 + "')\n"
    "stored=s.write(d)\n"
    "print(binascii.hexlify(bootsel.pack_record(stored)).decode())")

BOOTLOG_READ_CODE = (
    "import bootlog,binascii\n"
    "e=bootlog.BootLogStore('" + _LOG0 + "','" + _LOG1 + "').read()\n"
    "print(binascii.hexlify(bootlog.pack_record(e)).decode()"
    " if e else 'NONE')")

HASH_PATHS_CODE = (
    "import hashlib,binascii\n"
    "bad=''\n"
    "for path,sha in {pairs}:\n"
    "    try:\n"
    "        h=hashlib.sha256()\n"
    "        with open(path,'rb') as f:\n"
    "            while True:\n"
    "                c=f.read(512)\n"
    "                if not c: break\n"
    "                h.update(c)\n"
    "        if binascii.hexlify(h.digest()).decode()!=sha"
    " and not bad: bad='HASH '+path\n"
    "    except OSError:\n"
    "        if not bad: bad='MISSING '+path\n"
    "print(bad if bad else 'OK')")

VERIFY_SLOT_CODE = (
    "import hashlib,binascii,json\n"
    "root='{slot_root}'\n"
    "bad=''\n"
    "try:\n"
    "    with open(root+'/{manifest_name}','rb') as f: mbytes=f.read()\n"
    "    h=hashlib.sha256()\n"
    "    h.update(mbytes)\n"
    "    if binascii.hexlify(h.digest()).decode()"
    "!='{manifest_sha256}': bad='MANIFEST'\n"
    "except OSError:\n"
    "    bad='MISSING_MANIFEST'\n"
    "if not bad:\n"
    "    m=json.loads(mbytes.decode())\n"
    "    for rec in m['assets']:\n"
    "        if rec.get('role')!='managed_release': continue\n"
    "        if rec.get('zone')!='sd': continue\n"
    "        try:\n"
    "            with open(root+'/'+rec['path'],'rb') as f:"
    " data=f.read()\n"
    "            hh=hashlib.sha256()\n"
    "            hh.update(data)\n"
    "            if len(data)!=rec['size'] and not bad:"
    " bad='HASH '+rec['path']\n"
    "            if binascii.hexlify(hh.digest()).decode()"
    "!=rec['sha256'] and not bad: bad='HASH '+rec['path']\n"
    "        except OSError:\n"
    "            if not bad: bad='MISSING '+rec['path']\n"
    "print(bad if bad else 'OK')")

RMTREE_CODE = (
    "import os\n"
    "def _rm(p):\n"
    "    try:\n"
    "        for e in os.ilistdir(p):\n"
    "            c=p+'/'+e[0]\n"
    "            if e[1]&0x4000: _rm(c)\n"
    "            else: os.remove(c)\n"
    "        os.rmdir(p)\n"
    "    except OSError:\n"
    "        pass\n"
    "_rm('{path}')\n"
    "print('OK')")

RENAME_CODE = "import os\nos.rename('{src}','{dst}')\nprint('OK')"

_BOOT_PREFIXES = (
    "BOOT_VERSION ",
    "BOOT_RUNTIME_READY ",
    "BOOT_ROOT_VISIBLE ",
    "BOOT_BUFFERS ",
    "BOOT_MODE ",
    "BOOT_ABI_VIPER ",
)


def _entry_to_ref(entry):
    return SlotRef(
        entry.name,
        entry.release_id,
        binascii.hexlify(entry.manifest_sha256).decode())


def _ref_to_entry(ref):
    return bootsel.SlotEntry(
        ref.name, ref.release_id, binascii.unhexlify(ref.manifest_sha256))


def _device_path(zone, relative_path):
    if zone == "sd":
        return "/sd/" + relative_path
    return "/" + relative_path


class _MpremoteSession:
    def __init__(self, device, probe_source):
        self._device = device
        self._probe_source = probe_source
        self._staged_slot = None
        self._reset = False
        self._closed = False

    def _read_selector(self):
        out = self._device.exec(SELECTOR_READ_CODE).strip()
        if out == "NONE":
            return None
        record = bootsel.unpack_record(binascii.unhexlify(out))
        if record is None:
            raise ValueError("device returned an invalid selector record")
        return record

    def _write_selector(self, selector):
        def ref_data(entry):
            if entry is None:
                return None
            return (
                entry.name,
                entry.release_id,
                binascii.hexlify(entry.manifest_sha256).decode(),
            )
        literal = repr((
            ref_data(selector.confirmed),
            ref_data(selector.trial),
            selector.trial_generation,
            selector.trial_consumed,
            tuple(ref_data(entry) for entry in selector.retired),
            selector.confirmation_pending,
        ))
        out = self._device.exec(
            SELECTOR_WRITE_CODE,
            fields=literal,
        ).strip()
        stored = bootsel.unpack_record(binascii.unhexlify(out))
        if stored is None:
            raise ValueError("device returned an invalid stored selector")
        return stored

    def _read_boot_entry(self):
        out = self._device.exec(BOOTLOG_READ_CODE).strip()
        if out == "NONE":
            return None
        entry = bootlog.unpack_record(binascii.unhexlify(out))
        if entry is None:
            raise ValueError("device returned an invalid boot record")
        return entry

    def _hash_paths(self, pairs):
        literal = "[" + ",".join(
            "('" + path + "','" + sha + "')" for path, sha in pairs) + "]"
        return self._device.exec(HASH_PATHS_CODE, pairs=literal).strip()

    @staticmethod
    def _slot_root(name):
        return bootenv.SLOT_BASE + "/" + name

    def _slot_manifest_path(self, name):
        return self._slot_root(name) + "/" + bootenv.MANIFEST_NAME

    def _validate_slot_manifest(self, ref, expected_bytes=None):
        data = self._device.read_file(self._slot_manifest_path(ref.name))
        if data is None:
            raise ValueError("slot manifest is missing")
        if hashlib.sha256(data).hexdigest() != ref.manifest_sha256:
            raise ValueError("slot manifest hash mismatch")
        if expected_bytes is not None and data != expected_bytes:
            raise ValueError("slot manifest bytes mismatch")
        return data

    def _verify_slot_assets(self, slot_root, plan):
        out = self._device.exec(
            VERIFY_SLOT_CODE,
            slot_root=slot_root,
            manifest_name=bootenv.MANIFEST_NAME,
            manifest_sha256=plan.manifest_sha256,
        ).strip()
        if out != "OK":
            raise ValueError("slot asset verification failed: " + out)

    def resume_confirmed(self, plan):
        selector = self._read_selector()
        confirmed = selector.confirmed if selector else None
        if confirmed is None or confirmed.release_id != plan.release_id:
            return None
        ref = _entry_to_ref(confirmed)
        if ref.manifest_sha256 != plan.manifest_sha256:
            raise ValueError(
                "confirmed release identity conflicts with local plan")
        self._validate_slot_manifest(ref, plan.manifest_bytes)
        return SelectionTicket(
            selector.generation, ref, already_confirmed=True)

    def resume_trial(self, plan):
        selector = self._read_selector()
        trial = selector.trial if selector else None
        if trial is None:
            return None
        ref = _entry_to_ref(trial)
        if (ref.release_id != plan.release_id
                or ref.manifest_sha256 != plan.manifest_sha256):
            self.reject_trial(
                SelectionTicket(selector.trial_generation, ref))
            return None
        self._validate_slot_manifest(ref, plan.manifest_bytes)
        self._verify_slot_assets(self._slot_root(ref.name), plan)
        if selector.trial_consumed:
            stored = self._write_selector(bootsel.SelectorData(
                0,
                selector.confirmed,
                trial,
                0,
                False,
                selector.retired,
                selector.confirmation_pending))
            return SelectionTicket(stored.generation, ref)
        return SelectionTicket(selector.trial_generation, ref)

    def resume_cleanup(self):
        selector = self._read_selector()
        if selector is not None and selector.confirmation_pending:
            raise ValueError(
                "pending confirmation must be resolved before staging")
        if selector is not None and selector.trial is not None:
            raise ValueError(
                "pending trial must be resolved before staging")
        if selector is None or not selector.retired:
            return
        for entry in selector.retired:
            ref = _entry_to_ref(entry)
            self._validate_slot_manifest(ref)
            self._device.remove_tree(self._slot_root(ref.name))
        self._write_selector(bootsel.SelectorData(
            0, selector.confirmed, None, 0, False, (), False))

    def validate_bootstrap(self, plan):
        pairs = tuple(
            (_device_path(asset.zone, asset.relative_path), asset.sha256)
            for asset in plan.assets
            if asset.role == "bootstrap_fixed")
        if pairs:
            out = self._hash_paths(pairs)
            if out != "OK":
                raise ValueError(
                    "stable bootstrap anchor verification failed: " + out)

    def stage(self, plan):
        selector = self._read_selector()
        if selector is not None and selector.trial is not None:
            raise ValueError("another trial selection is still pending")
        confirmed = selector.confirmed if selector else None
        if confirmed is None:
            slot_name = "A"
        else:
            slot_name = "B" if confirmed.name == "A" else "A"
        if selector is not None and any(
                entry.name == slot_name for entry in selector.retired):
            raise ValueError(
                "previous retired slot must be finalized before staging")
        self._device.remove_tree(_STAGING_ROOT)
        self._device.write_file(
            _STAGING_ROOT + "/" + bootenv.MANIFEST_NAME,
            plan.manifest_bytes)
        for asset in plan.assets:
            if asset.role != "managed_release" or asset.zone != "sd":
                continue
            self._device.write_file(
                _STAGING_ROOT + "/" + asset.relative_path, asset.payload)
        self._staged_slot = slot_name

    def verify(self, plan):
        if self._staged_slot is None:
            raise ValueError("no staged release to verify")
        self._verify_slot_assets(_STAGING_ROOT, plan)

    def select_trial(self, plan):
        if self._staged_slot is None:
            raise ValueError("no staged release to activate")
        selector = self._read_selector()
        slot_name = self._staged_slot
        self._device.makedirs(bootenv.SLOT_BASE)
        self._device.remove_tree(self._slot_root(slot_name))
        self._device.rename(_STAGING_ROOT, self._slot_root(slot_name))
        stored = self._write_selector(bootsel.SelectorData(
            0,
            selector.confirmed if selector else None,
            bootsel.SlotEntry(
                slot_name,
                plan.release_id,
                binascii.unhexlify(plan.manifest_sha256)),
            0,
            False,
            selector.retired if selector else (),
            selector.confirmation_pending if selector else False))
        self._staged_slot = None
        return SelectionTicket(
            stored.generation, _entry_to_ref(stored.trial))

    def reconcile_trial_selection(self, plan):
        selector = self._read_selector()
        trial = selector.trial if selector else None
        if trial is None:
            return None
        ref = _entry_to_ref(trial)
        if (ref.release_id != plan.release_id
                or ref.manifest_sha256 != plan.manifest_sha256
                or selector.trial_generation is None):
            raise ValueError("trial selector readback is inconsistent")
        self._validate_slot_manifest(ref, plan.manifest_bytes)
        return SelectionTicket(selector.trial_generation, ref)

    def abort_staging(self, release_id):
        self._device.remove_tree(_STAGING_ROOT)
        self._staged_slot = None

    def _run_smoke(self):
        text = self._device.exec(self._probe_source)
        fields = {}
        for line in text.splitlines():
            for prefix in _BOOT_PREFIXES:
                if line.startswith(prefix):
                    fields[prefix.strip()] = line[len(prefix):]
        if len(fields) != len(_BOOT_PREFIXES):
            raise ValueError("boot smoke report is incomplete")
        mode = fields["BOOT_MODE"]
        if mode == "source":
            abi_tag = SOURCE_ABI_TAG
        elif mode == "mpy" and fields["BOOT_ABI_VIPER"] == "ok":
            abi_tag = MPY_ABI_TAG
        else:
            raise ValueError("boot smoke ABI evidence failed")
        buffers = []
        for part in fields["BOOT_BUFFERS"].split(","):
            name, length, identity = part.split(":")
            buffers.append((name, int(length), int(identity)))
        return ReleaseSmokeResult(
            release_id="",
            app_version=fields["BOOT_VERSION"],
            mode=mode,
            abi_tag=abi_tag,
            resident_runtime=fields["BOOT_RUNTIME_READY"] == "True",
            root_visible=fields["BOOT_ROOT_VISIBLE"] == "True",
            buffers=tuple(buffers),
        )

    def read_boot_observation(self, ticket, trial):
        entry = self._read_boot_entry()
        if entry is None:
            raise ValueError("no boot observation recorded")
        if entry.selected is None:
            raise ValueError("cold boot recorded no selected slot")
        selected = _entry_to_ref(entry.selected)
        smoke = self._run_smoke()
        return ColdBootObservation(
            selector_generation=entry.selector_generation,
            selection_generation=entry.selection_generation,
            boot_id=entry.generation,
            selected=selected,
            smoke=ReleaseSmokeResult(
                release_id=selected.release_id,
                app_version=smoke.app_version,
                mode=smoke.mode,
                abi_tag=smoke.abi_tag,
                resident_runtime=smoke.resident_runtime,
                root_visible=smoke.root_visible,
                buffers=smoke.buffers,
            ),
        )

    def confirm_trial(self, ticket):
        selector = self._read_selector()
        trial = selector.trial if selector else None
        if (trial is None
                or _entry_to_ref(trial) != ticket.slot_ref
                or selector.trial_generation != ticket.selector_generation
                or selector.trial_consumed is not True):
            raise ValueError("trial selector ticket is not confirmable")
        retired = selector.retired
        if selector.confirmed is not None:
            retired = retired + (selector.confirmed,)
        self._write_selector(bootsel.SelectorData(
            0,
            _ref_to_entry(ticket.slot_ref),
            None,
            0,
            False,
            retired,
            True))

    def is_release_confirmed(self, ticket):
        selector = self._read_selector()
        if selector is None or selector.confirmed is None:
            return False
        if (_entry_to_ref(selector.confirmed) != ticket.slot_ref
                or selector.trial is not None
                or selector.trial_generation != 0
                or selector.trial_consumed):
            return False
        try:
            self._validate_slot_manifest(ticket.slot_ref)
        except ValueError:
            return False
        return True

    def reject_trial(self, ticket):
        selector = self._read_selector()
        if (selector is not None
                and selector.trial is not None
                and _entry_to_ref(selector.trial) == ticket.slot_ref):
            self._write_selector(bootsel.SelectorData(
                0,
                selector.confirmed,
                None,
                0,
                False,
                selector.retired,
                selector.confirmation_pending))

    def rollback_confirmation(self, ticket):
        selector = self._read_selector()
        if (selector is None
                or selector.confirmed is None
                or _entry_to_ref(selector.confirmed) != ticket.slot_ref
                or not selector.confirmation_pending):
            return False
        fallback = selector.retired[0] if selector.retired else None
        if fallback is not None:
            self._validate_slot_manifest(_entry_to_ref(fallback))
        failed = selector.confirmed
        self._write_selector(bootsel.SelectorData(
            0, fallback, None, 0, False, (failed,), False))
        failed_ref = _entry_to_ref(failed)
        try:
            self._validate_slot_manifest(failed_ref)
            self._device.remove_tree(self._slot_root(failed_ref.name))
        except ValueError:
            pass
        self._write_selector(bootsel.SelectorData(
            0, fallback, None, 0, False, (), False))
        return True

    def finalize_release(self, ticket, plan):
        selector = self._read_selector()
        if (selector is None
                or selector.confirmed is None
                or _entry_to_ref(selector.confirmed) != ticket.slot_ref):
            raise ValueError("release is not confirmed for finalization")
        if selector.confirmation_pending:
            selector = self._write_selector(bootsel.SelectorData(
                0,
                selector.confirmed,
                None,
                0,
                False,
                selector.retired,
                False))
        for entry in selector.retired:
            ref = _entry_to_ref(entry)
            self._validate_slot_manifest(ref)
            self._device.remove_tree(self._slot_root(ref.name))
        for asset in plan.assets:
            if asset.role == "seed_if_absent":
                path = _device_path(asset.zone, asset.relative_path)
                if not self._device.exists(path):
                    self._device.write_file(path, asset.payload)
        self._write_selector(bootsel.SelectorData(
            0, selector.confirmed, None, 0, False, (), False))
        self._device.remove_tree(_STAGING_ROOT)

    def _reset_device(self):
        if self._reset:
            raise RuntimeError("release session reset more than once")
        self._reset = True
        self._device.reset()

    def _close(self):
        if self._closed:
            raise RuntimeError("release session closed more than once")
        self._closed = True
        self._device.close()


class MpremoteReleaseAdapter:
    """Applies releases through one connect/reset/close session per phase."""

    def __init__(self, device_factory, probe_source=None, boot_wait_s=8.0,
                 sleep=None):
        self._device_factory = device_factory
        if probe_source is None:
            probe_source = (
                Path(__file__).parent / "device_boot_probe.py"
            ).read_text(encoding="utf-8")
        self._probe_source = probe_source
        self._boot_wait_s = boot_wait_s
        if sleep is None:
            import time
            sleep = time.sleep
        self._sleep = sleep
        self._needs_boot_wait = False

    def run_session(self, operation):
        if self._needs_boot_wait:
            self._sleep(self._boot_wait_s)
            self._needs_boot_wait = False
        device = self._device_factory()
        device.connect()
        session = _MpremoteSession(device, self._probe_source)
        try:
            return run_guarded_session(
                lambda: operation(session),
                session._reset_device,
                session._close,
            )
        finally:
            self._needs_boot_wait = True


class MpremoteDevice:
    """Thin real-transport wrapper used by the production adapter."""

    def __init__(self, port, baudrate=115200, connect_wait=10):
        self._port = port
        self._baudrate = baudrate
        self._connect_wait = connect_wait
        self._transport = None

    def connect(self):
        from mpremote.transport_serial import SerialTransport
        self._transport = SerialTransport(
            self._port, self._baudrate, wait=self._connect_wait)
        self._transport.enter_raw_repl(soft_reset=False)

    def exec(self, code, **params):
        if params:
            code = code.format(**params)
        chunks = []
        try:
            self._transport.exec(code, data_consumer=chunks.append)
        except Exception as error:
            raise OSError(
                "device exec failed: " + str(error)) from error
        return b"".join(chunks).decode("utf-8", errors="replace")

    def read_file(self, path):
        try:
            return bytes(self._transport.fs_readfile(path))
        except OSError:
            return None

    def write_file(self, path, data):
        parent = path.rsplit("/", 1)[0]
        if parent:
            self._mkdirs(parent)
        self._transport.fs_writefile(path, bytes(data))

    def _mkdirs(self, path):
        current = ""
        for part in path.strip("/").split("/"):
            current += "/" + part
            try:
                self._transport.fs_mkdir(current)
            except OSError:
                pass

    def exists(self, path):
        return self._transport.fs_exists(path)

    def makedirs(self, path):
        self._mkdirs(path)

    def remove_tree(self, path):
        self.exec(RMTREE_CODE, path=path)

    def rename(self, src, dst):
        self.exec(RENAME_CODE, src=src, dst=dst)

    def reset(self):
        try:
            self._transport.exec("import machine\nmachine.reset()")
        except Exception:
            # The connection dies with the reset; the next boot record is
            # the proof that the reset actually happened.
            pass

    def close(self):
        if self._transport is not None:
            transport = self._transport
            self._transport = None
            transport.close()
