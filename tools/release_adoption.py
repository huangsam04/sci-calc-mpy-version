# Trusted first-takeover adoption for devices running the 1.3.0 baseline.
#
# The caller sees one small interface: prepare immutable host evidence, then
# apply it to one connected device.  Internally this module establishes a
# content-bound transaction before it changes a boot anchor: root payloads are
# first staged under non-overwriting names, an initial A slot and trial
# selector are ready, and only then are main.py and finally boot.py committed.
# An interrupted run therefore resumes from authenticated markers instead of
# treating a partial root update as a fresh 1.3.0 baseline.
import binascii
import hashlib
import re
from dataclasses import dataclass, field

import bootenv
import bootlog
import bootsel

from tools.release_device_mpremote import (
    BOOTLOG_READ_CODE,
    BOOTLOG_READ_MAX_BYTES,
    HASH_RECEIPT_MAX_PAIRS,
    HASH_PATH_MAX_CHARS,
    SELECTOR_READ_CODE,
    SELECTOR_READ_MAX_BYTES,
    SELECTOR_WRITE_CODE,
    SELECTOR_WRITE_MAX_BYTES,
    VERIFY_SLOT_CODE,
    VERIFY_SLOT_RECEIPT_MAX_BYTES,
    _OwnedReleaseTrees,
    stream_hash_receipt,
)
from tools.release_plan import (
    SOURCE_MODE,
    ReleasePlan,
    _BOOTSTRAP_PATHS,
    validate_release_plan,
)
from tools.release_protocol import (
    OWNER_MARKER_NAME,
    SlotRef,
    owner_marker_payload,
)


BASELINE_130_HASHES = {
    "boot.py": "e918c98fdf02faf11d6af325eec8c42399a070281a9988a9672b9774665b5af4",
    "main.py": "44fe63c3a3f98c1a8f2779addc11df4c6a31dd54d422e6828bf2e211e5d61ab0",
    "sdcard.py": "d2d4b98ed0d466c49a6c121c90a313d782defb59483a931b1ce1aae7904b60ea",
    "recovery.py": "46feef5addb3039b94b765d4b01291c8e5a442c0adda6b53f48ee5992d180cd1",
    "display/mono_palette.py": "5f9804a6dc8be3451e5e9ab6d3a35ef0a29889582b079a8cd5b796ba73f219a7",
    "display/ssd1322.py": "4065905c2d3c6f77709606c4c09a15ef20964022d7756f55fa6e948cfdfb86e6",
}

# COM5 contains three bootstrap modules from earlier, committed release work.
# Their exact Git-content hashes are admitted as replaceable transition state;
# any other existing bytes at these paths still fail closed.
_TRUSTED_TRANSITIONAL_PAIRS = (
    (
        "/bootenv.py",
        "6e7c0b1c99c47792631466831e37503f7934f1b0ab82254de101b31a3ba6f2da",
    ),
    (
        "/main.py",
        "774b2f4126d668ee67656fa807507cbc2a7e6676fe09f203618d1ce7f6425286",
    ),
    (
        "/bootsupervisor.py",
        "cf206fa87903b529f6f0dd5bab32c4fcfc2e549744f7481a59866b6f7ed87266",
    ),
)

_TRUSTED_TRANSITIONAL_SLOT = (
    "A",
    "0fb8e578f77a80b0446f4aad4a921708a4f47ee4275a483c4e370a317a25e525",
    "2b977404a62e94a66e8e3965b413d1afc50fb437e84a9e11879b499ebb5f64bc",
)

BASELINE_PATH_MAX_BYTES = 96
INITIAL_SLOT_MAX_FILES = 256
_TEMP_SUFFIX_BYTES = len(".scn-") + 16
# A bounded namespace audit counts both a finished file and its one possible
# content-bound staging sibling.  Limit the shape before a device query sees
# it, rather than trusting a FAT directory listing to stay small.
INITIAL_SLOT_MAX_DIRECTORIES = INITIAL_SLOT_MAX_FILES
_SLOT_DIRECTORY_ENTRY_LIMIT = INITIAL_SLOT_MAX_FILES * 2
# Every staged path is re-read through HASH_PATHS_CODE.  Keep both the final
# file and its content-bound temporary sibling below that query's path cap.
INITIAL_SLOT_PATH_MAX_BYTES = min(
    192,
    HASH_PATH_MAX_CHARS - len("/sd/.slots/A/") - _TEMP_SUFFIX_BYTES,
)
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_LOWER_HEX = frozenset("0123456789abcdef")

_SYSTEM_ROOT = bootenv.SELECTOR_PATHS[0].rsplit("/", 1)[0]
_INITIAL_SLOT_NAME = "A"
_INITIAL_SLOT_ROOT = bootenv.SLOT_BASE + "/" + _INITIAL_SLOT_NAME
_SLOT_ROOT = bootenv.SLOT_BASE
_PHASES = ("claim", "slots", "staged", "armed", "root")
_MARKER_PREFIX = ".sci-calc-adopt-"
_SYSTEM_STATIC_NAMES = ("sel.0", "sel.1", "boot.0", "boot.1")

_CONTROL_OUTPUT_MAX_BYTES = 16
_CONTROL_SOURCE_MAX_BYTES = 2048
_CONDITIONAL_RENAME_SOURCE_MAX_BYTES = 3072

# These snippets run as one raw-REPL evaluation.  Each has a fixed small
# response and is rendered only from host-validated ASCII literals.
DIRECTORY_AUDIT_CODE = (
    "import os\n"
    "p={path}\n"
    "allowed={allowed}\n"
    "try:\n"
    "    mode=os.stat(p)[0]\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    print('M' if code==2 else 'F')\n"
    "else:\n"
    "    if not (mode&0x4000):\n"
    "        print('F')\n"
    "    else:\n"
    "        count=0\n"
    "        foreign=False\n"
    "        try:\n"
    "            for entry in os.ilistdir(p):\n"
    "                count+=1\n"
    "                if count>16 or entry[0] not in allowed:\n"
    "                    foreign=True\n"
    "                    break\n"
    "        except OSError:\n"
    "            foreign=True\n"
    "        if foreign:\n"
    "            print('F')\n"
    "        elif count==0:\n"
    "            print('E')\n"
    "        else:\n"
    "            print('D')")

CREATE_DIRECTORY_CODE = (
    "import os\n"
    "p={path}\n"
    "try:\n"
    "    os.mkdir(p)\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    if code!=17:\n"
    "        print('F')\n"
    "    else:\n"
    "        try:\n"
    "            mode=os.stat(p)[0]\n"
    "        except OSError:\n"
    "            print('F')\n"
    "        else:\n"
    "            print('D' if mode&0x4000 else 'F')\n"
    "else:\n"
    "    print('C')")

ATOMIC_CREATE_CODE = (
    "import os,binascii\n"
    "p={path}\n"
    "payload=binascii.unhexlify('{payload_hex}')\n"
    "try:\n"
    "    os.stat(p)\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    if code!=2:\n"
    "        print('F')\n"
    "    else:\n"
    "        stream=None\n"
    "        primary=None\n"
    "        try:\n"
    "            stream=open(p,'wb')\n"
    "            stream.write(payload)\n"
    "            stream.flush()\n"
    "        except BaseException as error:\n"
    "            primary=error\n"
    "            raise\n"
    "        finally:\n"
    "            if stream is not None:\n"
    "                try:\n"
    "                    stream.close()\n"
    "                except Exception:\n"
    "                    if primary is None: raise\n"
    "        print('C')\n"
    "else:\n"
    "    print('E')")

CONDITIONAL_RENAME_CODE = (
    "import os,hashlib,binascii\n"
    "src={src}\n"
    "dst={dst}\n"
    "expected={expected}\n"
    "wanted='{wanted}'\n"
    "def _digest(path):\n"
    "    stream=None\n"
    "    primary=None\n"
    "    try:\n"
    "        digest=hashlib.sha256()\n"
    "        chunk=bytearray(512)\n"
    "        view=memoryview(chunk)\n"
    "        stream=open(path,'rb')\n"
    "        while True:\n"
    "            count=stream.readinto(chunk)\n"
    "            if not count: break\n"
    "            if count<0 or count>512: raise ValueError('readinto')\n"
    "            if count==512: digest.update(chunk)\n"
    "            else: digest.update(view[:count])\n"
    "        return binascii.hexlify(digest.digest()).decode()\n"
    "    except BaseException as error:\n"
    "        primary=error\n"
    "        raise\n"
    "    finally:\n"
    "        if stream is not None:\n"
    "            try:\n"
    "                stream.close()\n"
    "            except Exception:\n"
    "                if primary is None: raise\n"
    "try:\n"
    "    if _digest(src)!=wanted:\n"
    "        print('SOURCE')\n"
    "    else:\n"
    "        try:\n"
    "            previous=_digest(dst)\n"
    "        except OSError as error:\n"
    "            code=error.args[0] if error.args else -1\n"
    "            if code==2:\n"
    "                previous=None\n"
    "            else:\n"
    "                raise\n"
    "        if previous is not None and previous not in expected:\n"
    "            print('CONFLICT')\n"
    "        else:\n"
    "            os.rename(src,dst)\n"
    "            print('RENAMED')\n"
    "except OSError:\n"
    "    print('IO')")

DIRECTORY_COUNT_CODE = (
    "import os\n"
    "p={path}\n"
    "limit=" + str(_SLOT_DIRECTORY_ENTRY_LIMIT) + "\n"
    "try:\n"
    "    mode=os.stat(p)[0]\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    print('M' if code==2 else 'F')\n"
    "else:\n"
    "    if not (mode&0x4000):\n"
    "        print('F')\n"
    "    else:\n"
    "        count=0\n"
    "        try:\n"
    "            for entry in os.ilistdir(p):\n"
    "                count+=1\n"
    "                if count>limit: break\n"
    "        except OSError:\n"
    "            print('F')\n"
    "        else:\n"
    "            print('F' if count>limit else 'N%03x'%count)")

ENTRY_KIND_CODE = (
    "import os\n"
    "p={path}\n"
    "try:\n"
    "    mode=os.stat(p)[0]\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    print('M' if code==2 else 'F')\n"
    "else:\n"
    "    print('D' if mode&0x4000 else 'R')")


class AdoptionError(Exception):
    """The device is not in a state this flow is allowed to touch."""


@dataclass(frozen=True, slots=True)
class _SlotFile:
    path: str
    payload: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class _SlotDirectory:
    path: str
    entries: tuple


class _AdmissionSeal:
    """Private capability tying a prepared admission to its canonical fields."""

    __slots__ = (
        "plan", "bootstrap", "bootstrap_pairs", "baseline_pairs",
        "slot_files", "slot_pairs", "selector_bytes",
    )

    def __init__(self, plan, bootstrap, bootstrap_pairs, baseline_pairs,
                 slot_files, slot_pairs, selector_bytes):
        self.plan = plan
        self.bootstrap = bootstrap
        self.bootstrap_pairs = bootstrap_pairs
        self.baseline_pairs = baseline_pairs
        self.slot_files = slot_files
        self.slot_pairs = slot_pairs
        self.selector_bytes = selector_bytes


@dataclass(frozen=True, slots=True)
class AdoptionAdmission:
    """Immutable host evidence prepared before opening a device session."""

    plan: object
    bootstrap: tuple
    bootstrap_pairs: tuple
    baseline_pairs: tuple
    slot_files: tuple
    slot_pairs: tuple
    release_id: str
    manifest_sha256: str
    bootstrap_sha256: str
    baseline_sha256: str
    slot_sha256: str
    selector_bytes: bytes
    _seal: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class AdoptionReceipt:
    """Stable outcome evidence; it intentionally carries no port or time."""

    release_id: str
    manifest_sha256: str
    bootstrap_sha256: str
    baseline_sha256: str
    changed: bool


def _device_path(relative_path):
    return "/" + relative_path


def _is_lower_hash(value):
    return (type(value) is str
            and len(value) == 64
            and all(char in _LOWER_HEX for char in value))


def _normalized_baseline_pairs(baseline_hashes):
    items = getattr(baseline_hashes, "items", None)
    if not callable(items):
        raise ValueError("baseline hashes must be a mapping")

    pairs = []
    paths = set()
    seen_folded = set()
    for entry in items():
        # Bound the pull first: a hostile iterator is abandoned as soon as
        # the receipt limit is exceeded and is never materialized in full.
        if len(pairs) >= HASH_RECEIPT_MAX_PAIRS:
            raise ValueError("trusted baseline hash set is too large")
        if type(entry) is not tuple or len(entry) != 2:
            raise ValueError("invalid trusted baseline hash")
        relative_path, digest = entry
        if (type(relative_path) is not str or type(digest) is not str
                or not relative_path
                or len(relative_path) > BASELINE_PATH_MAX_BYTES
                or not relative_path.isascii()
                or len(digest) != 64
                or "\\" in relative_path or "\x00" in relative_path
                or ":" in relative_path
                or any(ord(char) < 32 for char in relative_path)
                or relative_path.startswith("/")
                or relative_path.endswith("/")
                or any(part in ("", ".", "..")
                       for part in relative_path.split("/"))
                or _HASH_RE.fullmatch(digest) is None):
            raise ValueError("invalid trusted baseline hash")
        folded_path = relative_path.casefold()
        if folded_path in seen_folded:
            raise ValueError("trusted baseline path collision")
        seen_folded.add(folded_path)
        paths.add(relative_path)
        pairs.append((_device_path(relative_path), digest))

    # Callers may supply different digests (tests use synthetic baselines)
    # but the path set itself is not negotiable.
    if paths != frozenset(BASELINE_130_HASHES):
        raise ValueError(
            "trusted baseline path set is not the 1.3.0 anchor set")
    return tuple(sorted(pairs))


def _trusted_transitional_pairs():
    transitional = _TRUSTED_TRANSITIONAL_PAIRS
    if type(transitional) is not tuple:
        raise ValueError("trusted transitional hashes are invalid")
    if len(transitional) > HASH_RECEIPT_MAX_PAIRS:
        raise ValueError("trusted transitional hash set is too large")

    allowed_paths = {
        _device_path(relative_path)
        for _key, relative_path in _BOOTSTRAP_PATHS.values()
    }
    seen = set()
    for pair in transitional:
        if type(pair) is not tuple or len(pair) != 2:
            raise ValueError("trusted transitional hashes are invalid")
        path, digest = pair
        if (type(path) is not str or path not in allowed_paths
                or path in seen or not _is_lower_hash(digest)):
            raise ValueError("trusted transitional hashes are invalid")
        seen.add(path)
    return tuple(sorted(transitional))


def _pairs_sha256(pairs):
    digest = hashlib.sha256()
    for path, expected in pairs:
        digest.update(path.encode("ascii"))
        digest.update(b"\x00")
        digest.update(expected.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _admitted_bootstrap(plan):
    """Admit only the ten canonical internal-root anchors, byte-verified."""
    by_source = {}
    for asset in plan.assets:
        if asset.role != "bootstrap_fixed":
            continue
        if (type(asset.source_path) is not str
                or asset.source_path not in _BOOTSTRAP_PATHS):
            raise AdoptionError("unexpected bootstrap anchor")
        if asset.source_path in by_source:
            raise AdoptionError("duplicate bootstrap anchor")
        by_source[asset.source_path] = asset

    admitted = []
    device_paths = set()
    for source_path in sorted(_BOOTSTRAP_PATHS):
        key, relative_path = _BOOTSTRAP_PATHS[source_path]
        asset = by_source.get(source_path)
        if asset is None:
            raise AdoptionError("missing bootstrap anchor: " + source_path)
        if (type(asset.key) is not str or type(asset.local_path) is not str
                or type(asset.zone) is not str
                or type(asset.relative_path) is not str
                or type(asset.kind) is not str or type(asset.size) is not int
                or type(asset.sha256) is not str
                or type(asset.payload) is not bytes):
            raise AdoptionError("bootstrap anchor has invalid fields")
        if asset.zone != "internal":
            raise AdoptionError(
                "bootstrap anchor zone must be internal: " + source_path)
        if asset.relative_path != relative_path:
            raise AdoptionError(
                "bootstrap anchor device path mismatch: " + source_path)
        if (asset.key != key or asset.kind != SOURCE_MODE
                or asset.local_path != "source/" + source_path):
            raise AdoptionError(
                "bootstrap anchor identity mismatch: " + source_path)
        payload = asset.payload
        if (asset.size != len(payload)
                or not _is_lower_hash(asset.sha256)
                or asset.sha256 != hashlib.sha256(payload).hexdigest()):
            raise AdoptionError(
                "bootstrap anchor digest mismatch: " + source_path)
        device_path = _device_path(relative_path)
        if device_path in device_paths:
            raise AdoptionError("bootstrap anchor device path collision")
        device_paths.add(device_path)
        admitted.append(asset)
    if len(admitted) > HASH_RECEIPT_MAX_PAIRS:
        raise AdoptionError("release plan bootstrap exceeds the receipt bound")
    return tuple(admitted)


def _valid_slot_relative_path(path):
    if (type(path) is not str or not path
            or len(path) > INITIAL_SLOT_PATH_MAX_BYTES
            or not path.isascii()
            or "\\" in path or "\x00" in path or ":" in path
            or path.startswith("/") or path.endswith("/")):
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def _admitted_slot_files(plan):
    """Build the exact first-slot image without copying any payload bytes."""
    manifest = plan.manifest_bytes
    if type(manifest) is not bytes or len(manifest) > 65536:
        raise AdoptionError("release manifest exceeds initial-slot bound")
    owner_payload = owner_marker_payload(
        plan.release_id, plan.manifest_sha256)
    files = [
        _SlotFile(
            _INITIAL_SLOT_ROOT + "/" + bootenv.MANIFEST_NAME,
            manifest,
            plan.manifest_sha256,
        ),
        _SlotFile(
            _INITIAL_SLOT_ROOT + "/" + OWNER_MARKER_NAME,
            owner_payload,
            hashlib.sha256(owner_payload).hexdigest(),
        ),
    ]
    seen = {
        bootenv.MANIFEST_NAME.casefold(),
        OWNER_MARKER_NAME.casefold(),
    }
    managed_files = 0
    for asset in plan.assets:
        if asset.role != "managed_release" or asset.zone != "sd":
            continue
        if (type(asset.relative_path) is not str
                or type(asset.payload) is not bytes
                or type(asset.sha256) is not str
                or type(asset.size) is not int
                or not _valid_slot_relative_path(asset.relative_path)
                or asset.size != len(asset.payload)
                or not _is_lower_hash(asset.sha256)
                or asset.sha256 != hashlib.sha256(asset.payload).hexdigest()):
            raise AdoptionError("invalid initial-slot asset")
        folded = asset.relative_path.casefold()
        if folded in seen:
            raise AdoptionError("initial-slot path collision")
        seen.add(folded)
        if len(files) >= INITIAL_SLOT_MAX_FILES:
            raise AdoptionError("initial slot contains too many files")
        files.append(_SlotFile(
            _INITIAL_SLOT_ROOT + "/" + asset.relative_path,
            asset.payload,
            asset.sha256,
        ))
        managed_files += 1
    if managed_files == 0:
        raise AdoptionError("initial slot has no managed runtime assets")
    slot_files = tuple(sorted(files, key=lambda item: item.path))
    directories = _slot_directories(slot_files)
    known_paths = {item.path.casefold() for item in slot_files}
    directory_paths = {item.path.casefold() for item in directories}
    temporary_paths = set()
    for item in slot_files:
        temporary = _temporary_path(item.path, item.sha256)
        folded = temporary.casefold()
        if (folded in known_paths or folded in directory_paths
                or folded in temporary_paths):
            raise AdoptionError("initial-slot temporary path collision")
        temporary_paths.add(folded)
    return slot_files


def _initial_selector_bytes(plan):
    selector = bootsel.SelectorData(
        1,
        None,
        bootsel.SlotEntry(
            _INITIAL_SLOT_NAME,
            plan.release_id,
            binascii.unhexlify(plan.manifest_sha256)),
        1,
        False,
        (),
        False,
    )
    return bootsel.pack_record(selector)


def prepare_adoption(plan, baseline_hashes=None):
    """Validate all trusted input before a device adapter may be contacted."""
    if type(plan) is not ReleasePlan:
        raise ValueError("release plan must be immutable")
    if type(plan.assets) is not tuple or len(plan.assets) > INITIAL_SLOT_MAX_FILES:
        raise ValueError("release plan exceeds initial-slot asset bound")
    validate_release_plan(plan)
    if baseline_hashes is None:
        baseline_hashes = BASELINE_130_HASHES
    bootstrap = _admitted_bootstrap(plan)
    bootstrap_pairs = tuple(sorted(
        (_device_path(asset.relative_path), asset.sha256)
        for asset in bootstrap))
    baseline_pairs = _normalized_baseline_pairs(baseline_hashes)
    transitional_pairs = _trusted_transitional_pairs()
    slot_files = _admitted_slot_files(plan)
    slot_pairs = tuple((item.path, item.sha256) for item in slot_files)
    selector_bytes = _initial_selector_bytes(plan)
    seal = _AdmissionSeal(
        plan, bootstrap, bootstrap_pairs, baseline_pairs,
        slot_files, slot_pairs, selector_bytes)
    return AdoptionAdmission(
        plan=plan,
        bootstrap=bootstrap,
        bootstrap_pairs=bootstrap_pairs,
        baseline_pairs=baseline_pairs,
        slot_files=slot_files,
        slot_pairs=slot_pairs,
        release_id=plan.release_id,
        manifest_sha256=plan.manifest_sha256,
        bootstrap_sha256=_pairs_sha256(bootstrap_pairs),
        baseline_sha256=_pairs_sha256(
            baseline_pairs + transitional_pairs),
        slot_sha256=_pairs_sha256(slot_pairs),
        selector_bytes=selector_bytes,
        _seal=seal,
    )


def _baseline_mapping_from_pairs(pairs):
    if type(pairs) is not tuple or len(pairs) != len(BASELINE_130_HASHES):
        raise ValueError("admission baseline evidence is invalid")
    hashes = {}
    for pair in pairs:
        if (type(pair) is not tuple or len(pair) != 2
                or type(pair[0]) is not str or type(pair[1]) is not str
                or not pair[0].startswith("/")):
            raise ValueError("admission baseline evidence is invalid")
        relative_path = pair[0][1:]
        if relative_path in hashes:
            raise ValueError("admission baseline evidence is invalid")
        hashes[relative_path] = pair[1]
    if set(hashes) != set(BASELINE_130_HASHES):
        raise ValueError("admission baseline evidence is invalid")
    return hashes


def _canonical_admission(admission):
    """Reject a manually forged or stale admission before the first I/O."""
    if type(admission) is not AdoptionAdmission:
        raise ValueError("adoption must use a prepared admission")
    seal = admission._seal
    if (type(seal) is not _AdmissionSeal
            or seal.plan is not admission.plan
            or seal.bootstrap is not admission.bootstrap
            or seal.bootstrap_pairs is not admission.bootstrap_pairs
            or seal.baseline_pairs is not admission.baseline_pairs
            or seal.slot_files is not admission.slot_files
            or seal.slot_pairs is not admission.slot_pairs
            or seal.selector_bytes is not admission.selector_bytes):
        raise ValueError("adoption admission capability is invalid")
    canonical = prepare_adoption(
        admission.plan,
        baseline_hashes=_baseline_mapping_from_pairs(
            admission.baseline_pairs),
    )
    for name in (
            "bootstrap", "bootstrap_pairs", "baseline_pairs", "slot_files",
            "slot_pairs", "release_id", "manifest_sha256",
            "bootstrap_sha256", "baseline_sha256", "slot_sha256",
            "selector_bytes"):
        if getattr(admission, name) != getattr(canonical, name):
            raise ValueError("adoption admission does not match its plan")
    return canonical


def _hash(device, pairs):
    try:
        receipt = stream_hash_receipt(device, pairs)
    except (OSError, ValueError) as error:
        raise AdoptionError("bounded device SHA verification failed") from error
    if receipt.fault:
        raise AdoptionError("bounded device SHA verification fault")
    return receipt


def _read_codec_record(device, code, cap, unpack, label):
    try:
        text = device.exec_limited(code, cap)
    except (OSError, ValueError) as error:
        raise AdoptionError(label + " read failed") from error
    if (type(text) is not str or len(text) > cap or not text.isascii()):
        raise AdoptionError(label + " returned an invalid receipt")
    text = text.strip()
    if text == "NONE":
        return None
    try:
        record = unpack(binascii.unhexlify(text))
    except (ValueError, TypeError, binascii.Error) as error:
        raise AdoptionError(label + " returned an invalid receipt") from error
    if record is None:
        raise AdoptionError(label + " returned an invalid receipt")
    return record


def _read_selector(device):
    return _read_codec_record(
        device,
        SELECTOR_READ_CODE,
        SELECTOR_READ_MAX_BYTES,
        bootsel.unpack_record,
        "selector",
    )


def _read_boot_entry(device):
    return _read_codec_record(
        device,
        BOOTLOG_READ_CODE,
        BOOTLOG_READ_MAX_BYTES,
        bootlog.unpack_record,
        "boot log",
    )


def _empty_selector(selector):
    return (selector is not None
            and selector.confirmed is None
            and selector.trial is None
            and selector.trial_generation == 0
            and selector.trial_consumed is False
            and selector.retired == ()
            and selector.confirmation_pending is False)


def _slot_matches_admission(entry, admission):
    return (entry is not None
            and entry.name == _INITIAL_SLOT_NAME
            and entry.release_id == admission.release_id
            and bytes(entry.manifest_sha256)
            == binascii.unhexlify(admission.manifest_sha256))


def _selector_matches_admission(selector, admission):
    return (selector is not None
            and selector.confirmed is None
            and _slot_matches_admission(selector.trial, admission)
            and selector.trial_generation > 0
            and selector.retired == ()
            and selector.confirmation_pending is False)


def _boot_matches_selector(boot, selector, admission, require_empty=False):
    if boot is None:
        return True
    if boot.selector_generation > selector.generation:
        return False
    if boot.selected is None:
        if boot.selection_generation is not None:
            return False
        return (not require_empty
                or boot.selector_generation == selector.generation)
    if require_empty:
        return False
    return (_slot_matches_admission(boot.selected, admission)
            and boot.selection_generation == selector.trial_generation)


def _empty_legacy_system(device):
    selector = _read_selector(device)
    return (_empty_selector(selector)
            and _boot_matches_selector(
                _read_boot_entry(device), selector, None,
                require_empty=True))


def _write_initial_selector(device, admission):
    fields = (
        None,
        (
            _INITIAL_SLOT_NAME,
            admission.release_id,
            admission.manifest_sha256,
        ),
        0,
        False,
        (),
        False,
    )
    try:
        text = device.exec_limited(
            SELECTOR_WRITE_CODE,
            SELECTOR_WRITE_MAX_BYTES,
            fields=repr(fields),
        )
    except (OSError, ValueError) as error:
        raise AdoptionError("initial selector write failed") from error
    if (type(text) is not str or len(text) > SELECTOR_WRITE_MAX_BYTES
            or not text.isascii()):
        raise AdoptionError("initial selector returned an invalid receipt")
    try:
        stored = bootsel.unpack_record(binascii.unhexlify(text.strip()))
    except (ValueError, TypeError, binascii.Error) as error:
        raise AdoptionError(
            "initial selector returned an invalid receipt") from error
    if (not _selector_matches_admission(stored, admission)
            or stored.trial_consumed is not False):
        raise AdoptionError("initial selector returned an invalid receipt")
    return stored


def _transitional_slot_ref():
    identity = _TRUSTED_TRANSITIONAL_SLOT
    if (type(identity) is not tuple or len(identity) != 3
            or identity[0] not in ("A", "B")
            or not _is_lower_hash(identity[1])
            or not _is_lower_hash(identity[2])):
        raise ValueError("trusted transitional slot is invalid")
    return SlotRef(identity[0], identity[1], identity[2])


def _verify_transitional_slot_assets(
        device, slot_root, manifest_sha256, release_id):
    if (not _is_lower_hash(manifest_sha256)
            or not _is_lower_hash(release_id)):
        raise ValueError("trusted transitional slot is invalid")
    try:
        text = device.exec_limited(
            VERIFY_SLOT_CODE,
            VERIFY_SLOT_RECEIPT_MAX_BYTES,
            slot_root=slot_root,
            manifest_name=bootenv.MANIFEST_NAME,
            manifest_sha256=manifest_sha256,
        )
    except (OSError, ValueError) as error:
        raise AdoptionError("transitional slot verification failed") from error
    if (type(text) is not str or len(text) > VERIFY_SLOT_RECEIPT_MAX_BYTES
            or not text.isascii() or text.strip() != "OK"):
        raise AdoptionError("transitional slot verification failed")


def _transition_trees(device):
    return _OwnedReleaseTrees(
        device,
        lambda root, manifest, release: _verify_transitional_slot_assets(
            device, root, manifest, release),
    )


def _inspect_transitional_slot(device):
    try:
        return _transition_trees(device).inspect_transition_slot(
            _transitional_slot_ref())
    except AdoptionError:
        raise
    except (OSError, ValueError) as error:
        raise AdoptionError("transitional slot is not trusted") from error


def _cleanup_transitional_slot(device):
    try:
        result = _transition_trees(device).claim_and_erase_transition_slot(
            _transitional_slot_ref())
    except AdoptionError:
        raise
    except (OSError, ValueError) as error:
        raise AdoptionError("transitional slot cleanup failed") from error
    if result not in ("ABSENT", "ERASED"):
        raise AdoptionError("transitional slot cleanup failed")
    return result


def _matches_all(receipt, pair_count):
    return (receipt.missing_mask == 0
            and receipt.matched_mask == (1 << pair_count) - 1)


def _one_status(device, path, digest):
    receipt = _hash(device, ((path, digest),))
    if receipt.matched_mask == 1:
        return "matched"
    if receipt.missing_mask == 1:
        return "missing"
    return "mismatch"


def _pair_statuses(device, pairs):
    statuses = {}
    for offset in range(0, len(pairs), HASH_RECEIPT_MAX_PAIRS):
        batch = pairs[offset:offset + HASH_RECEIPT_MAX_PAIRS]
        receipt = _hash(device, batch)
        for index, (path, _digest) in enumerate(batch):
            if receipt.matched_mask & (1 << index):
                statuses[path] = "matched"
            elif receipt.missing_mask & (1 << index):
                statuses[path] = "missing"
            else:
                statuses[path] = "mismatch"
    return statuses


def _receipt_statuses(pairs, receipt):
    statuses = {}
    for index, (path, _digest) in enumerate(pairs):
        if receipt.matched_mask & (1 << index):
            statuses[path] = "matched"
        elif receipt.missing_mask & (1 << index):
            statuses[path] = "missing"
        else:
            statuses[path] = "mismatch"
    return statuses


def _classify_pending(
        admission, current, baseline, transitional_pairs, transitional):
    """Return writable anchors using only bounded precheck receipts."""
    required_baseline_paths = {
        _device_path(path) for path in BASELINE_130_HASHES
    }
    baseline_status = _receipt_statuses(admission.baseline_pairs, baseline)
    transitional_status = _receipt_statuses(
        transitional_pairs, transitional)

    pending = set()
    trusted_transition = False
    for index, (path, _digest) in enumerate(admission.bootstrap_pairs):
        if current.matched_mask & (1 << index):
            continue
        baseline_state = baseline_status.get(path)
        transitional_state = transitional_status.get(path)
        if baseline_state == "matched" or transitional_state == "matched":
            if transitional_state == "matched":
                trusted_transition = True
            pending.add(path)
        elif current.missing_mask & (1 << index):
            if path in required_baseline_paths:
                raise AdoptionError(
                    "trusted baseline anchor is missing: " + path)
            pending.add(path)
        elif path in required_baseline_paths:
            raise AdoptionError(
                "trusted baseline anchor hash mismatch: " + path)
        else:
            raise AdoptionError("foreign boot module conflict: " + path)
    return pending, trusted_transition


def _root_snapshot(device, admission):
    current = _hash(device, admission.bootstrap_pairs)
    baseline = _hash(device, admission.baseline_pairs)
    transitional_pairs = _trusted_transitional_pairs()
    transitional = _hash(device, transitional_pairs)
    pending, trusted_transition = _classify_pending(
        admission, current, baseline, transitional_pairs, transitional)
    return current, pending, trusted_transition


def _root_has_new_content(admission, current):
    baseline = dict(admission.baseline_pairs)
    for index, (path, digest) in enumerate(admission.bootstrap_pairs):
        if (current.matched_mask & (1 << index)
                and baseline.get(path) != digest):
            return True
    return False


def _phase_path(admission, phase):
    return (_SYSTEM_ROOT + "/" + _MARKER_PREFIX
            + admission.bootstrap_sha256 + "." + phase)


def _phase_payload(admission, phase):
    if phase not in _PHASES:
        raise ValueError("invalid adoption phase")
    return (
        "SCI-CALC-ADOPTION-1\n"
        "phase=" + phase + "\n"
        "release=" + admission.release_id + "\n"
        "manifest=" + admission.manifest_sha256 + "\n"
        "bootstrap=" + admission.bootstrap_sha256 + "\n"
        "baseline=" + admission.baseline_sha256 + "\n"
        "slot=" + admission.slot_sha256 + "\n"
    ).encode("ascii")


def _phase_pair(admission, phase):
    payload = _phase_payload(admission, phase)
    return _phase_path(admission, phase), hashlib.sha256(payload).hexdigest()


def _system_allowed_names(admission):
    return tuple(
        _phase_path(admission, phase).rsplit("/", 1)[1]
        for phase in _PHASES
    ) + _SYSTEM_STATIC_NAMES


def _render_limited(template, cap, **params):
    rendered = template.format(**params)
    if len(rendered.encode("ascii")) > cap:
        raise AdoptionError("bounded device control query is too large")
    return rendered


def _control_token(device, template, label, **params):
    _render_limited(template, _CONTROL_SOURCE_MAX_BYTES, **params)
    try:
        text = device.exec_limited(
            template, _CONTROL_OUTPUT_MAX_BYTES, **params)
    except (OSError, ValueError) as error:
        raise AdoptionError(label + " failed") from error
    if (type(text) is not str or len(text) > _CONTROL_OUTPUT_MAX_BYTES
            or not text.isascii()):
        raise AdoptionError(label + " returned an invalid receipt")
    return text.strip()


def _directory_state(device, path, allowed):
    token = _control_token(
        device,
        DIRECTORY_AUDIT_CODE,
        "directory audit",
        path=repr(path),
        allowed=repr(tuple(allowed)),
    )
    if token not in ("M", "E", "D", "F"):
        raise AdoptionError("directory audit returned an invalid receipt")
    return token


def _directory_entry_count(device, path):
    token = _control_token(
        device,
        DIRECTORY_COUNT_CODE,
        "directory count",
        path=repr(path),
    )
    if token == "M":
        return None
    if (len(token) != 4 or token[0] != "N"
            or any(char not in _LOWER_HEX for char in token[1:])):
        raise AdoptionError("directory count returned an invalid receipt")
    count = int(token[1:], 16)
    if count > _SLOT_DIRECTORY_ENTRY_LIMIT:
        raise AdoptionError("directory count exceeds adoption bound")
    return count


def _entry_kind(device, path):
    token = _control_token(
        device,
        ENTRY_KIND_CODE,
        "directory entry audit",
        path=repr(path),
    )
    if token not in ("M", "D", "R", "F"):
        raise AdoptionError(
            "directory entry audit returned an invalid receipt")
    return token


def _create_directory(device, path):
    token = _control_token(
        device,
        CREATE_DIRECTORY_CODE,
        "directory creation",
        path=repr(path),
    )
    if token not in ("C", "D"):
        raise AdoptionError("directory creation was refused")
    return token


def _ensure_directory(device, path, allowed):
    state = _directory_state(device, path, allowed)
    if state == "M":
        _create_directory(device, path)
        state = _directory_state(device, path, allowed)
    if state not in ("E", "D"):
        raise AdoptionError("directory ownership conflict: " + path)
    return state


def _ensure_small_file(device, path, payload, digest, label):
    status = _one_status(device, path, digest)
    if status == "matched":
        return False
    if status != "missing":
        raise AdoptionError(label + " conflicts with an existing file")
    token = _control_token(
        device,
        ATOMIC_CREATE_CODE,
        label,
        path=repr(path),
        payload_hex=payload.hex(),
    )
    if token not in ("C", "E"):
        raise AdoptionError(label + " creation was refused")
    if _one_status(device, path, digest) != "matched":
        raise AdoptionError(label + " read-back verification failed")
    return token == "C"


def _ensure_phase_marker(device, admission, phase):
    path, digest = _phase_pair(admission, phase)
    return _ensure_small_file(
        device, path, _phase_payload(admission, phase), digest,
        "adoption " + phase + " marker")


def _require_phase_marker(device, admission, phase):
    path, digest = _phase_pair(admission, phase)
    if _one_status(device, path, digest) != "matched":
        raise AdoptionError("adoption " + phase + " marker is not trusted")


def _temporary_path(path, digest):
    leaf = path.rsplit("/", 1)[-1]
    if len(leaf) + _TEMP_SUFFIX_BYTES > 255:
        raise AdoptionError("adoption temporary path exceeds device bound")
    temporary = path + ".scn-" + digest[:16]
    if len(temporary) > HASH_PATH_MAX_CHARS:
        raise AdoptionError("adoption temporary hash path exceeds device bound")
    return temporary


def _slot_directories(slot_files):
    """Return the exact, bounded directory shape for the initial slot."""
    directories = {_INITIAL_SLOT_ROOT: {}}
    prefix = _INITIAL_SLOT_ROOT + "/"
    for item in slot_files:
        if type(item) is not _SlotFile or not item.path.startswith(prefix):
            raise AdoptionError("invalid initial-slot file path")
        relative = item.path[len(prefix):]
        parts = relative.split("/")
        if not parts or any(not part for part in parts):
            raise AdoptionError("invalid initial-slot file path")
        parent = _INITIAL_SLOT_ROOT
        for index, name in enumerate(parts):
            is_directory = index + 1 < len(parts)
            entries = directories[parent]
            folded_name = name.casefold()
            if folded_name in entries:
                existing_name, existing_kind = entries[folded_name]
                if existing_name != name or existing_kind != is_directory:
                    raise AdoptionError("initial-slot file-directory collision")
            else:
                entries[folded_name] = (name, is_directory)
            if is_directory:
                parent = parent + "/" + name
                if parent not in directories:
                    if len(directories) >= INITIAL_SLOT_MAX_DIRECTORIES:
                        raise AdoptionError(
                            "initial slot has too many directories")
                    directories[parent] = {}

    result = []
    for path, entries in directories.items():
        if len(entries) > INITIAL_SLOT_MAX_FILES:
            raise AdoptionError("initial slot directory exceeds entry bound")
        result.append(_SlotDirectory(
            path,
            tuple(sorted(entries.values())),
        ))
    return tuple(sorted(
        result,
        key=lambda item: (item.path.count("/"), item.path),
    ))


def _temporary_pairs(admission):
    return tuple(
        (_temporary_path(_device_path(asset.relative_path), asset.sha256),
         asset.sha256)
        for asset in admission.bootstrap
    )


def _slot_temporary_pairs(admission):
    return tuple(
        (_temporary_path(item.path, item.sha256), item.sha256)
        for item in admission.slot_files
    )


def _stage_temporary_file(device, path, payload, digest):
    temp = _temporary_path(path, digest)
    status = _one_status(device, temp, digest)
    if status == "matched":
        return temp
    if status != "missing":
        raise AdoptionError("foreign adoption temporary conflict: " + temp)
    device.write_file(temp, payload)
    if _one_status(device, temp, digest) != "matched":
        raise AdoptionError("temporary write SHA verification failed: " + path)
    return temp


def _conditional_rename(device, src, dst, digest, allowed_existing):
    rendered = _render_limited(
        CONDITIONAL_RENAME_CODE,
        _CONDITIONAL_RENAME_SOURCE_MAX_BYTES,
        src=repr(src),
        dst=repr(dst),
        expected=repr(tuple(allowed_existing)),
        wanted=digest,
    )
    try:
        text = device.exec_limited(
            CONDITIONAL_RENAME_CODE,
            _CONTROL_OUTPUT_MAX_BYTES,
            src=repr(src),
            dst=repr(dst),
            expected=repr(tuple(allowed_existing)),
            wanted=digest,
        )
    except (OSError, ValueError) as error:
        raise AdoptionError("conditional file commit failed") from error
    del rendered
    if (type(text) is not str or len(text) > _CONTROL_OUTPUT_MAX_BYTES
            or not text.isascii()):
        raise AdoptionError("conditional file commit returned an invalid receipt")
    token = text.strip()
    if token != "RENAMED":
        raise AdoptionError("conditional file commit refused: " + token)


def _commit_temporary_file(device, path, payload, digest, allowed_existing):
    status = _one_status(device, path, digest)
    if status == "matched":
        return False
    if status != "missing":
        if not any(
                _one_status(device, path, expected) == "matched"
                for expected in allowed_existing):
            raise AdoptionError(
                "destination file conflicts with adoption: " + path)
    temp = _stage_temporary_file(device, path, payload, digest)
    _conditional_rename(device, temp, path, digest, allowed_existing)
    if _one_status(device, path, digest) != "matched":
        raise AdoptionError("committed file SHA verification failed: " + path)
    return True


def _sort_key(asset):
    # The legacy 1.3.0 boot only mounts /sd.  Its exact pinned hash therefore
    # remains compatible with the new main shim once the slot and selector are
    # prepared.  Commit main before boot so the inverse pairing never exists.
    if asset.relative_path == "boot.py":
        return 2
    if asset.relative_path == "main.py":
        return 1
    return 0


def _preflight_namespace(
        device, admission, root_changed, trusted_transition):
    claim_path, claim_digest = _phase_pair(admission, "claim")
    slots_path, slots_digest = _phase_pair(admission, "slots")
    claim = _one_status(device, claim_path, claim_digest)
    slots = _one_status(device, slots_path, slots_digest)
    sys_state = _directory_state(
        device, _SYSTEM_ROOT, _system_allowed_names(admission))
    slot_state = _directory_state(device, _SLOT_ROOT, (_INITIAL_SLOT_NAME,))
    transitional_slot_state = None
    if (slots != "matched" and slot_state == "D"
            and trusted_transition):
        transitional_slot_state = _inspect_transitional_slot(device)
    if claim == "mismatch" or slots == "mismatch":
        raise AdoptionError("foreign adoption transaction marker conflict")
    if claim == "matched":
        if sys_state != "D":
            raise AdoptionError("adoption system namespace is not owned")
    else:
        if root_changed and not trusted_transition:
            raise AdoptionError("partial root handoff has no trusted journal")
        if (sys_state == "D" and trusted_transition
                and _empty_legacy_system(device)):
            pass
        elif sys_state not in ("M", "E"):
            raise AdoptionError("system namespace already exists")
    if slots == "matched":
        if claim != "matched" or slot_state not in ("M", "E", "D"):
            raise AdoptionError("initial slot namespace is not owned")
    else:
        transition_allowed = (
            transitional_slot_state == "unmarked"
            or (claim == "matched"
                and transitional_slot_state in ("owned", "empty")))
        if slot_state not in ("M", "E") and not transition_allowed:
            raise AdoptionError("slot namespace already exists")
    return claim, slots, transitional_slot_state


def _ensure_claim(device, admission):
    _ensure_directory(device, _SYSTEM_ROOT, _system_allowed_names(admission))
    _ensure_phase_marker(device, admission, "claim")
    if _directory_state(
            device, _SYSTEM_ROOT, _system_allowed_names(admission)) != "D":
        raise AdoptionError("adoption system namespace is not owned")


def _ensure_slot_namespace(device, admission, allow_missing):
    slots_path, slots_digest = _phase_pair(admission, "slots")
    status = _one_status(device, slots_path, slots_digest)
    state = _directory_state(device, _SLOT_ROOT, (_INITIAL_SLOT_NAME,))
    if status == "matched":
        if state == "M":
            if not allow_missing:
                raise AdoptionError("initial slot namespace disappeared")
            _create_directory(device, _SLOT_ROOT)
            state = _directory_state(
                device, _SLOT_ROOT, (_INITIAL_SLOT_NAME,))
        if state not in ("E", "D"):
            raise AdoptionError("initial slot namespace is not owned")
        return
    if (not allow_missing or status != "missing"
            or state not in ("M", "E")):
        raise AdoptionError("initial slot namespace conflicts with existing data")
    _ensure_phase_marker(device, admission, "slots")
    if state == "M":
        _create_directory(device, _SLOT_ROOT)
    if _directory_state(
            device, _SLOT_ROOT, (_INITIAL_SLOT_NAME,)) != "E":
        raise AdoptionError("initial slot namespace creation failed")


def _ensure_initial_slot_directories(device, admission, allow_missing):
    directories = _slot_directories(admission.slot_files)
    for directory in directories:
        count = _directory_entry_count(device, directory.path)
        if count is None:
            if not allow_missing:
                raise AdoptionError(
                    "initial slot directory disappeared: " + directory.path)
            _create_directory(device, directory.path)
            count = _directory_entry_count(device, directory.path)
            if count is None:
                raise AdoptionError(
                    "initial slot directory creation failed: "
                    + directory.path)
    for directory in directories:
        for name, is_directory in directory.entries:
            if not is_directory:
                continue
            path = directory.path + "/" + name
            if _entry_kind(device, path) != "D":
                raise AdoptionError(
                    "initial slot directory conflicts with existing data: "
                    + path)
    return directories


def _audit_initial_slot(device, admission, directories, statuses,
                        temporary_statuses):
    by_path = {item.path: item for item in admission.slot_files}
    temporary_by_path = {
        item.path: _temporary_path(item.path, item.sha256)
        for item in admission.slot_files
    }
    for path, status in statuses.items():
        temporary_status = temporary_statuses[temporary_by_path[path]]
        if status == "mismatch" or temporary_status == "mismatch":
            raise AdoptionError("initial slot contains foreign content: " + path)
        if status == "matched" and temporary_status != "missing":
            raise AdoptionError(
                "initial slot contains stale temporary content: " + path)

    for directory in directories:
        expected_count = 0
        for name, is_directory in directory.entries:
            path = directory.path + "/" + name
            if is_directory:
                if _entry_kind(device, path) != "D":
                    raise AdoptionError(
                        "initial slot directory changed type: " + path)
                expected_count += 1
                continue
            status = statuses[path]
            if status == "matched":
                expected_count += 1
            elif temporary_statuses[temporary_by_path[path]] == "matched":
                expected_count += 1
        actual_count = _directory_entry_count(device, directory.path)
        if actual_count != expected_count:
            raise AdoptionError(
                "initial slot namespace contains unknown content: "
                + directory.path)
    return by_path


def _stage_initial_slot(device, admission):
    staged_path, staged_digest = _phase_pair(admission, "staged")
    staged = _one_status(device, staged_path, staged_digest)
    if staged == "mismatch":
        raise AdoptionError("initial slot marker conflicts with existing data")
    if staged == "missing":
        for phase in ("armed", "root"):
            path, digest = _phase_pair(admission, phase)
            if _one_status(device, path, digest) != "missing":
                raise AdoptionError(
                    "initial slot marker is missing after activation")
    allow_missing = staged == "missing"
    _ensure_slot_namespace(device, admission, allow_missing)
    directories = _ensure_initial_slot_directories(
        device, admission, allow_missing)
    statuses = _pair_statuses(device, admission.slot_pairs)
    temporary_statuses = _pair_statuses(
        device, _slot_temporary_pairs(admission))
    by_path = _audit_initial_slot(
        device, admission, directories, statuses, temporary_statuses)
    if staged == "matched":
        if (any(status != "matched" for status in statuses.values())
                or any(status != "missing"
                       for status in temporary_statuses.values())):
            raise AdoptionError("initial slot marker does not match slot state")
        return
    for path, status in statuses.items():
        if status == "missing":
            item = by_path[path]
            _commit_temporary_file(
                device, item.path, item.payload, item.sha256, ())
    verified = _pair_statuses(device, admission.slot_pairs)
    temporary_verified = _pair_statuses(
        device, _slot_temporary_pairs(admission))
    if (any(status != "matched" for status in verified.values())
            or any(status != "missing"
                   for status in temporary_verified.values())):
        raise AdoptionError("initial slot SHA verification failed")
    _audit_initial_slot(
        device, admission, directories, verified, temporary_verified)
    _ensure_phase_marker(device, admission, "staged")


def _initial_selector_state(device, admission):
    absence_pairs = tuple((path, "0" * 64) for path in (
        bootenv.SELECTOR_PATHS[0], bootenv.SELECTOR_PATHS[1],
        bootenv.BOOTLOG_PATHS[0], bootenv.BOOTLOG_PATHS[1]))
    presence = _pair_statuses(device, absence_pairs)
    selector = _read_selector(device)
    boot = _read_boot_entry(device)
    if (_selector_matches_admission(selector, admission)
            and _boot_matches_selector(boot, selector, admission)):
        return "matched"
    if (_empty_selector(selector)
            and _boot_matches_selector(
                boot, selector, admission, require_empty=True)):
        return "empty"
    if (selector is None
            and all(status == "missing" for status in presence.values())):
        return "missing"
    raise AdoptionError("initial selector conflicts with existing data")


def _arm_initial_selector(device, admission):
    armed_path, armed_digest = _phase_pair(admission, "armed")
    armed = _one_status(device, armed_path, armed_digest)
    if armed == "mismatch":
        raise AdoptionError("initial selector marker conflicts with existing data")
    selector = _initial_selector_state(device, admission)
    # A prior uncertain raw-REPL response may already have written sel.0; it
    # is safe to resume only if it is exactly the authenticated trial record.
    if selector in ("missing", "empty"):
        if armed == "matched":
            raise AdoptionError("initial selector disappeared after arming")
        _write_initial_selector(device, admission)
        if _initial_selector_state(device, admission) != "matched":
            raise AdoptionError("initial selector read-back verification failed")
    if armed == "missing":
        _ensure_phase_marker(device, admission, "armed")


def _commit_root(device, admission, pending):
    _require_phase_marker(device, admission, "claim")
    _require_phase_marker(device, admission, "slots")
    _require_phase_marker(device, admission, "staged")
    _require_phase_marker(device, admission, "armed")
    baseline = dict(admission.baseline_pairs)
    transitional = dict(_trusted_transitional_pairs())
    for asset in sorted(admission.bootstrap, key=_sort_key):
        path = _device_path(asset.relative_path)
        if path not in pending:
            continue
        allowed = []
        if path in baseline:
            allowed.append(baseline[path])
        if path in transitional:
            allowed.append(transitional[path])
        _commit_temporary_file(
            device, path, asset.payload, asset.sha256, tuple(allowed))


def _receipt(admission, changed):
    return AdoptionReceipt(
        release_id=admission.release_id,
        manifest_sha256=admission.manifest_sha256,
        bootstrap_sha256=admission.bootstrap_sha256,
        baseline_sha256=admission.baseline_sha256,
        changed=changed,
    )


def adopt_prepared_device(device, admission):
    """Apply one prepared adoption without reading a device file into host RAM."""
    admission = _canonical_admission(admission)
    current, pending, trusted_transition = _root_snapshot(device, admission)
    temp_statuses = _pair_statuses(device, _temporary_pairs(admission))
    if any(status == "mismatch" for status in temp_statuses.values()):
        raise AdoptionError("foreign adoption temporary file conflicts")
    root_path, root_digest = _phase_pair(admission, "root")
    root_marker = _one_status(device, root_path, root_digest)
    root_complete = _matches_all(current, len(admission.bootstrap_pairs))
    retired_root_journal = root_marker == "mismatch"
    if root_marker == "missing":
        claim_path, claim_digest = _phase_pair(admission, "claim")
        retired_root_journal = (
            _one_status(device, claim_path, claim_digest) != "matched")
    if root_complete and retired_root_journal:
        # Adoption is a one-time handoff of the stable boot root.  Its journal
        # was content-bound to the first application slot, so a later normal
        # release can have either a different marker payload or no current
        # marker after its retired journal was cleaned.  Exact hashes for every
        # immutable root anchor are the durable handoff proof; the ordinary
        # selector/slot protocol validates and advances the application next.
        if any(status != "missing" for status in temp_statuses.values()):
            raise AdoptionError(
                "complete root handoff retains temporary content")
        return _receipt(admission, False)
    if root_marker == "mismatch":
        raise AdoptionError("adoption root marker conflicts with existing data")
    root_changed = _root_has_new_content(admission, current)
    claim, slots, transitional_slot_state = _preflight_namespace(
        device, admission, root_changed, trusted_transition)

    if root_complete:
        if any(status != "missing" for status in temp_statuses.values()):
            raise AdoptionError("complete root handoff retains temporary content")
        if root_marker == "matched":
            _require_phase_marker(device, admission, "claim")
            _require_phase_marker(device, admission, "slots")
            _stage_initial_slot(device, admission)
            _arm_initial_selector(device, admission)
            return _receipt(admission, False)
        if claim != "matched":
            raise AdoptionError("complete root handoff has no trusted journal")
        _require_phase_marker(device, admission, "staged")
        _require_phase_marker(device, admission, "armed")
        _stage_initial_slot(device, admission)
        _arm_initial_selector(device, admission)
        _ensure_phase_marker(device, admission, "root")
        return _receipt(admission, True)

    if root_marker == "matched":
        raise AdoptionError("root marker conflicts with partial handoff")

    # This is the durable witness for the otherwise-empty /sys bootstrap.
    # No root anchor changes until every pending anchor has an exact staged
    # sibling, so a power loss before the selector is armed is legacy-safe.
    by_path = {_device_path(asset.relative_path): asset
               for asset in admission.bootstrap}
    for path in sorted(pending):
        asset = by_path[path]
        _stage_temporary_file(device, path, asset.payload, asset.sha256)

    _ensure_claim(device, admission)
    if slots != "matched" and transitional_slot_state is not None:
        _cleanup_transitional_slot(device)
    _stage_initial_slot(device, admission)
    _arm_initial_selector(device, admission)

    # Re-read both root receipts immediately before the first anchor rename.
    # A changed baseline, foreign root file, or unexpected partial handoff is
    # rejected before it can be overwritten.
    current, pending, _trusted_transition = _root_snapshot(device, admission)
    if _matches_all(current, len(admission.bootstrap_pairs)):
        _stage_initial_slot(device, admission)
        _arm_initial_selector(device, admission)
        _ensure_phase_marker(device, admission, "root")
        return _receipt(admission, True)
    _commit_root(device, admission, pending)

    verified = _hash(device, admission.bootstrap_pairs)
    if not _matches_all(verified, len(admission.bootstrap_pairs)):
        raise AdoptionError("anchor verification failed")
    _stage_initial_slot(device, admission)
    _arm_initial_selector(device, admission)
    _ensure_phase_marker(device, admission, "root")
    return _receipt(admission, True)


def adopt_device(device, plan, baseline_hashes=None):
    """Compatibility entry point returning whether trusted adoption changed it."""
    return adopt_prepared_device(
        device,
        prepare_adoption(plan, baseline_hashes=baseline_hashes),
    ).changed
