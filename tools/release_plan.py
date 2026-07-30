"""Build deterministic, immutable SCI-CALC release plans."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import bootenv

from tools.release_protocol import OWNER_MARKER_NAME


SCHEMA_VERSION = 1
PRODUCT = "sci-calc"
SOURCE_MODE = "source"
MPY_MODE = "mpy"
SOURCE_ABI_TAG = "source"
MPY_ABI_TAG = "micropython-v1.29.0-preview:mpy-v6.3:xtensawin"
_BOOTSTRAP_PATHS = {
    "boot.py": ("bootstrap:boot", "boot.py"),
    "bootenv.py": ("bootstrap:bootenv", "bootenv.py"),
    "bootlog.py": ("bootstrap:bootlog", "bootlog.py"),
    "bootsel.py": ("bootstrap:bootsel", "bootsel.py"),
    "bootsupervisor.py": ("bootstrap:bootsupervisor", "bootsupervisor.py"),
    "internal_main.py": ("bootstrap:main", "main.py"),
    "recovery.py": ("bootstrap:recovery", "recovery.py"),
    "sdcard.py": ("bootstrap:sdcard", "sdcard.py"),
    "display/mono_palette.py": (
        "bootstrap:display.mono_palette", "display/mono_palette.py"),
    "display/ssd1322.py": (
        "bootstrap:display.ssd1322", "display/ssd1322.py"),
}
_INTERNAL_DISPLAY_PATHS = frozenset((
    "display/mono_palette.py",
    "display/ssd1322.py",
))
_FROZEN_PACKAGE_PREFIXES = (
    "display/",
    "input/",
    "ui/",
    "utils/",
)
_FROZEN_MODULE_PATHS = frozenset((
    "calc/__init__.py",
    "calc/bundled_plugins.py",
    "calc/functions.py",
    "calc/limits.py",
    "calc/loader.py",
    "calc/number.py",
    "calc/parser.py",
    "calc/plugin_reload.py",
    "functions/__init__.py",
    "functions/basic.py",
    "functions/solve.py",
    "functions/trig.py",
    "screens/__init__.py",
    "screens/about.py",
    "screens/calculator.py",
    "screens/function_panel.py",
    "screens/function_picker.py",
    "screens/letter_panel.py",
    "screens/main_menu.py",
    "screens/plot.py",
    "screens/settings.py",
    "screens/stopwatch.py",
    "screens/variable_panel.py",
))
_FONT_OUTPUTS = {
    "fonts/Bally7x9.c": "fonts/Bally7x9.xglcd",
    "fonts/FixedFont5x8.c": "fonts/FixedFont5x8.xglcd",
    "fonts/Neato5x7.c": "fonts/Neato5x7.xglcd",
}
_SEED_PATHS = {
    "settings.json": "seed:settings",
    "vars.json": "seed:vars",
}
_PROTECTED_USER_PATH_ROOTS = frozenset((
    "settings.json",
    "vars.json",
    "add-ons",
    ".sci-calc",
))
_SLOT_METADATA_PATHS = frozenset((
    bootenv.MANIFEST_NAME.casefold(),
    OWNER_MARKER_NAME.casefold(),
))
_VERSION_RE = re.compile(
    rb"""^VERSION\s*=\s*["']([^"']+)["']\s*$""",
    re.MULTILINE,
)


# A release manifest crosses the device ownership boundary.  Keep its host
# parser deliberately small enough that hostile prior-release state cannot
# make validation allocate proportional to an untrusted document.
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_MANIFEST_ASSET_RECORDS = 128
_MAX_MANIFEST_SEED_RECORDS = 8
_MAX_MANIFEST_ROOT_FIELDS = 8
_MAX_MANIFEST_RECORD_FIELDS = 7
_MAX_MANIFEST_RECORD_BYTES = 1024
_MAX_MANIFEST_STRING_BYTES = 512
# VERIFY_SLOT_CODE retains header strings in a fixed 96-byte field on device.
# Keep host admission no wider than that device-side allocation.
_MAX_MANIFEST_ROOT_STRING_BYTES = 96
_MAX_MANIFEST_KEY_BYTES = 256
_MAX_MANIFEST_PATH_BYTES = 255
_MAX_MANIFEST_FIELD_STRING_BYTES = 32
_MAX_MANIFEST_NUMBER_BYTES = 20
_MAX_MANIFEST_NESTING = 4
_MAX_MANIFEST_TOKENS = 2304
_MAX_RELEASE_PLAN_ASSETS = (
    _MAX_MANIFEST_ASSET_RECORDS + _MAX_MANIFEST_SEED_RECORDS + 8)

_COLLECTION_NONE = 0
_COLLECTION_ASSETS = 1
_COLLECTION_SEEDS = 2
_JSON_WHITESPACE = b" \t\r\n"
_JSON_DELIMITERS = b" \t\r\n,]}"
_HEX_DIGITS = b"0123456789abcdefABCDEF"


def is_frozen_module(path):
    """True when *path* is supplied by the required SCI-CALC firmware."""
    return (path in _FROZEN_MODULE_PATHS
            or (path.endswith(".py")
                and path.startswith(_FROZEN_PACKAGE_PREFIXES)))


def is_compiled_in_mpy(path):
    """True when the mpy plan expects a compiled output for *path*."""
    if not path.endswith(".py"):
        return False
    if is_frozen_module(path):
        return False
    if path in _INTERNAL_DISPLAY_PATHS:
        return True
    if path in _BOOTSTRAP_PATHS or path == "launch.py":
        return False
    if path.startswith("functions/") or path == "runtime_scenarios_host.py":
        return False
    if path in _SEED_PATHS or path in _FONT_OUTPUTS:
        return False
    return True


def _normalize_path(path):
    normalized = str(path).replace("\\", "/")
    parts = normalized.split("/")
    if (not normalized or normalized.startswith("/")
            or len(normalized) > _MAX_MANIFEST_PATH_BYTES
            or not normalized.isascii()
            or any(ord(char) < 32 or ord(char) > 126
                   for char in normalized)
            or any(part in ("", ".", "..") for part in parts)
            or any(":" in part or "\x00" in part for part in parts)):
        raise ValueError("invalid release path: " + normalized)
    return "/".join(parts)


def _snapshot_files(files):
    entries = []
    folded_paths = set()
    for path, content in files:
        normalized = _normalize_path(path)
        folded = normalized.casefold()
        if folded in folded_paths:
            raise ValueError("release path collision: " + normalized)
        folded_paths.add(folded)
        entries.append((normalized, bytes(content)))
    return tuple(sorted(entries))


@dataclass(frozen=True, slots=True)
class ReleaseTreeSnapshot:
    source_files: tuple
    build_files: tuple

    @classmethod
    def from_files(cls, files, build_files=()):
        return cls(
            _snapshot_files(files),
            _snapshot_files(build_files),
        )


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    key: str
    source_path: str
    local_path: str
    zone: str
    relative_path: str
    kind: str
    role: str
    sha256: str
    size: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    schema: int
    app_version: str
    mode: str
    abi_tag: str
    release_id: str
    assets: tuple
    manifest_bytes: bytes
    manifest_sha256: str


def _read_tree(root, required):
    root = Path(root)
    if not root.exists():
        if required:
            raise ValueError("release tree does not exist: " + str(root))
        return ()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("release tree must be a real directory: " + str(root))

    files = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("release tree symlink is forbidden: " + str(path))
        if path.is_file():
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            files.append((relative.as_posix(), path.read_bytes()))
    return tuple(files)


def snapshot_release_tree(source_root, build_root):
    """Capture exact source/build bytes before any device session starts."""
    return ReleaseTreeSnapshot.from_files(
        _read_tree(source_root, required=True),
        build_files=_read_tree(build_root, required=False),
    )


def _app_version(snapshot):
    for path, content in snapshot.source_files:
        if path == "version.py":
            match = _VERSION_RE.search(content)
            if match is not None:
                try:
                    version = match.group(1).decode("ascii")
                except UnicodeDecodeError as error:
                    raise ValueError(
                        "version.py must define a device-compatible VERSION"
                    ) from error
                if (len(version) > _MAX_MANIFEST_ROOT_STRING_BYTES
                        or any(ord(char) < 32 or ord(char) > 126
                               for char in version)):
                    raise ValueError(
                        "version.py must define a device-compatible VERSION")
                return version
    raise ValueError("version.py must define VERSION")


def _asset(key, source_path, local_path, content, zone, relative_path, kind,
           role):
    payload = bytes(content)
    return ReleaseAsset(
        key=key,
        source_path=source_path,
        local_path=local_path,
        zone=zone,
        relative_path=relative_path,
        kind=kind,
        role=role,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        payload=payload,
    )


def _bootstrap_asset(path, content):
    bootstrap = _BOOTSTRAP_PATHS[path]
    return _asset(
        bootstrap[0],
        path,
        "source/" + path,
        content,
        "internal",
        bootstrap[1],
        SOURCE_MODE,
        "bootstrap_fixed",
    )


def _sd_asset(path, content, mode, build_files):
    logical_path = path[:-3]
    if (mode == SOURCE_MODE or path == "launch.py"
            or path.startswith("functions/")):
        return _asset(
            "sd:" + logical_path,
            path,
            "source/" + path,
            content,
            "sd",
            path,
            SOURCE_MODE,
            "managed_release",
        )

    compiled_path = logical_path + ".mpy"
    try:
        compiled = build_files[compiled_path]
    except KeyError:
        raise ValueError("missing compiled runtime module: " + compiled_path)
    if not compiled:
        raise ValueError("empty compiled runtime module: " + compiled_path)
    return _asset(
        "sd:" + logical_path,
        path,
        "build/" + compiled_path,
        compiled,
        "sd",
        compiled_path,
        MPY_MODE,
        "managed_release",
    )


def _source_asset(path, content, mode, build_files):
    if not path.endswith(".py"):
        raise ValueError("unclassified source file: " + path)
    if path in _BOOTSTRAP_PATHS:
        return _bootstrap_asset(path, content)
    return _sd_asset(path, content, mode, build_files)


def _source_assets(path, content, mode, build_files):
    if path == "runtime_scenarios_host.py":
        return (_asset(
            "host:runtime_scenarios_host",
            path,
            "source/" + path,
            content,
            "host",
            path,
            "host_source",
            "host_only",
        ),)

    if is_frozen_module(path):
        if path in _BOOTSTRAP_PATHS:
            return (_bootstrap_asset(path, content),)
        return ()

    seed_key = _SEED_PATHS.get(path)
    if seed_key is not None:
        return (_asset(
            seed_key,
            path,
            "source/" + path,
            content,
            "sd",
            path,
            "seed",
            "seed_if_absent",
        ),)

    font_output = _FONT_OUTPUTS.get(path)
    if font_output is not None:
        try:
            generated = build_files[font_output]
        except KeyError:
            raise ValueError("missing generated font asset: " + font_output)
        if not generated:
            raise ValueError("empty generated font asset: " + font_output)
        logical_path = path[:-2]
        return (
            _asset(
                "host:" + logical_path,
                path,
                "source/" + path,
                content,
                "host",
                path,
                "build_input",
                "host_only",
            ),
            _asset(
                "sd:" + logical_path,
                path,
                "build/" + font_output,
                generated,
                "sd",
                font_output,
                "font",
                "managed_release",
            ),
        )

    if path not in _INTERNAL_DISPLAY_PATHS:
        return (_source_asset(path, content, mode, build_files),)
    return (
        _bootstrap_asset(path, content),
        _sd_asset(path, content, mode, build_files),
    )


def _asset_record(asset):
    return {
        "format": asset.kind,
        "key": asset.key,
        "path": asset.relative_path,
        "role": asset.role,
        "sha256": asset.sha256,
        "size": asset.size,
        "zone": asset.zone,
    }


def _is_protected_user_path(zone, path):
    """Return whether an SD-root path belongs exclusively to the user."""
    if zone != "sd":
        return False
    folded = _normalize_path(path).casefold()
    for root in _PROTECTED_USER_PATH_ROOTS:
        if folded == root or folded.startswith(root + "/"):
            return True
    return False


def _is_reserved_slot_metadata_path(role, zone, path):
    return (
        role == "managed_release"
        and zone == "sd"
        and path.casefold() in _SLOT_METADATA_PATHS
    )


def _validate_release_assets(assets):
    if len(assets) > _MAX_RELEASE_PLAN_ASSETS:
        raise ValueError("release plan asset count exceeds limit")
    keys = set()
    paths = set()
    for asset in assets:
        normalized_path = _normalize_path(asset.relative_path)
        if normalized_path != asset.relative_path:
            raise ValueError("invalid release asset path: " + normalized_path)
        if (asset.role in ("bootstrap_fixed", "managed_release")
                and _is_protected_user_path(asset.zone, normalized_path)):
            raise ValueError("protected user path in release asset")
        if _is_reserved_slot_metadata_path(
                asset.role, asset.zone, normalized_path):
            raise ValueError("reserved slot metadata path in release asset")
        if asset.key in keys:
            raise ValueError("release asset key collision: " + asset.key)
        keys.add(asset.key)
        location = (asset.zone, normalized_path.casefold())
        if location in paths:
            raise ValueError(
                "release asset path collision: "
                + asset.zone + ":" + asset.relative_path)
        paths.add(location)


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


class _ManifestPreflight:
    """Validate manifest shape limits without materializing JSON objects."""

    __slots__ = ("_data", "_index", "_length", "_tokens")

    def __init__(self, data):
        self._data = data
        self._index = 0
        self._length = len(data)
        self._tokens = 0

    def validate(self):
        self._skip_whitespace()
        self._parse_object(1, root=True)
        self._skip_whitespace()
        if self._index != self._length:
            self._invalid()

    def _parse_object(self, depth, root):
        self._check_depth(depth)
        if self._current() != ord("{"):
            self._invalid()
        start = self._index
        self._consume_token()
        self._index += 1
        self._skip_whitespace()
        if self._current() == ord("}"):
            self._index += 1
            self._check_record_size(start, root)
            return

        field_count = 0
        field_limit = (
            _MAX_MANIFEST_ROOT_FIELDS if root
            else _MAX_MANIFEST_RECORD_FIELDS)
        while True:
            field_count += 1
            if field_count > field_limit:
                self._limit("field count")
            key_start, key_end = self._parse_string(
                _MAX_MANIFEST_STRING_BYTES)
            self._skip_whitespace()
            self._expect(ord(":"))
            self._skip_whitespace()
            if root:
                collection = self._root_collection(key_start, key_end)
                string_limit = self._root_string_limit(key_start, key_end)
            else:
                collection = _COLLECTION_NONE
                string_limit = self._record_string_limit(key_start, key_end)
            self._parse_value(depth, collection, string_limit)
            self._skip_whitespace()
            current = self._current()
            if current == ord("}"):
                self._index += 1
                self._check_record_size(start, root)
                return
            self._expect(ord(","))
            self._skip_whitespace()

    def _parse_array(self, depth, collection):
        self._check_depth(depth)
        if self._current() != ord("["):
            self._invalid()
        self._consume_token()
        self._index += 1
        self._skip_whitespace()
        if self._current() == ord("]"):
            self._index += 1
            return

        record_count = 0
        record_limit = self._record_limit(collection)
        while True:
            record_count += 1
            if record_count > record_limit:
                if collection == _COLLECTION_ASSETS:
                    self._limit("asset record count")
                if collection == _COLLECTION_SEEDS:
                    self._limit("seed record count")
                self._limit("array item count")
            self._parse_collection_item(depth)
            self._skip_whitespace()
            current = self._current()
            if current == ord("]"):
                self._index += 1
                return
            self._expect(ord(","))
            self._skip_whitespace()

    def _parse_collection_item(self, depth):
        self._skip_whitespace()
        if self._current() == ord("{"):
            self._parse_object(
                depth + 1,
                root=False,
            )
            return
        self._parse_value(
            depth,
            _COLLECTION_NONE,
            _MAX_MANIFEST_STRING_BYTES,
        )

    def _parse_value(self, depth, collection, string_limit):
        self._skip_whitespace()
        current = self._current()
        if current == ord('"'):
            self._parse_string(string_limit)
            return
        if current == ord("{"):
            self._parse_object(
                depth + 1,
                root=False,
            )
            return
        if current == ord("["):
            self._parse_array(depth + 1, collection)
            return
        self._parse_atom()

    def _parse_string(self, limit):
        if self._current() != ord('"'):
            self._invalid()
        self._consume_token()
        self._index += 1
        start = self._index
        raw_length = 0
        while self._index < self._length:
            current = self._data[self._index]
            if current == ord('"'):
                end = self._index
                self._index += 1
                return start, end
            if current < 0x20 or current > 0x7f:
                self._invalid()
            if current == ord("\\"):
                raw_length += self._parse_escape()
            else:
                self._index += 1
                raw_length += 1
            if raw_length > limit:
                self._limit("string byte")
        self._invalid()

    def _parse_escape(self):
        if self._index + 1 >= self._length:
            self._invalid()
        escaped = self._data[self._index + 1]
        if escaped == ord("u"):
            if self._index + 5 >= self._length:
                self._invalid()
            for position in range(self._index + 2, self._index + 6):
                if self._data[position] not in _HEX_DIGITS:
                    self._invalid()
            self._index += 6
            return 6
        if escaped not in b'"\\/bfnrt':
            self._invalid()
        self._index += 2
        return 2

    def _parse_atom(self):
        self._consume_token()
        start = self._index
        while self._index < self._length:
            current = self._data[self._index]
            if current in _JSON_DELIMITERS:
                break
            if current < 0x20 or current > 0x7f:
                self._invalid()
            self._index += 1
            if self._index - start > _MAX_MANIFEST_NUMBER_BYTES:
                self._limit("numeric byte")
        if self._index == start:
            self._invalid()

    def _root_collection(self, start, end):
        if self._equals(start, end, b"assets"):
            return _COLLECTION_ASSETS
        if self._equals(start, end, b"seeds"):
            return _COLLECTION_SEEDS
        return _COLLECTION_NONE

    def _root_string_limit(self, start, end):
        if (self._equals(start, end, b"abi_tag")
                or self._equals(start, end, b"app_version")):
            return _MAX_MANIFEST_ROOT_STRING_BYTES
        if self._equals(start, end, b"release_id"):
            return 64
        if self._equals(start, end, b"product"):
            return _MAX_MANIFEST_FIELD_STRING_BYTES
        if self._equals(start, end, b"mode"):
            return _MAX_MANIFEST_FIELD_STRING_BYTES
        return _MAX_MANIFEST_STRING_BYTES

    def _record_string_limit(self, start, end):
        if self._equals(start, end, b"path"):
            return _MAX_MANIFEST_PATH_BYTES
        if self._equals(start, end, b"key"):
            return _MAX_MANIFEST_KEY_BYTES
        if (self._equals(start, end, b"format")
                or self._equals(start, end, b"role")
                or self._equals(start, end, b"zone")):
            return _MAX_MANIFEST_FIELD_STRING_BYTES
        if self._equals(start, end, b"sha256"):
            return 64
        return _MAX_MANIFEST_STRING_BYTES

    def _record_limit(self, collection):
        if collection == _COLLECTION_ASSETS:
            return _MAX_MANIFEST_ASSET_RECORDS
        if collection == _COLLECTION_SEEDS:
            return _MAX_MANIFEST_SEED_RECORDS
        return _MAX_MANIFEST_ASSET_RECORDS

    def _check_record_size(self, start, root):
        if not root and self._index - start > _MAX_MANIFEST_RECORD_BYTES:
            self._limit("record byte")

    def _check_depth(self, depth):
        if depth > _MAX_MANIFEST_NESTING:
            self._limit("nesting")

    def _consume_token(self):
        self._tokens += 1
        if self._tokens > _MAX_MANIFEST_TOKENS:
            self._limit("token count")

    def _skip_whitespace(self):
        while (self._index < self._length
               and self._data[self._index] in _JSON_WHITESPACE):
            self._index += 1

    def _expect(self, expected):
        if self._current() != expected:
            self._invalid()
        self._index += 1

    def _current(self):
        if self._index >= self._length:
            self._invalid()
        return self._data[self._index]

    def _equals(self, start, end, literal):
        return (end - start == len(literal)
                and self._data.startswith(literal, start))

    @staticmethod
    def _invalid():
        raise ValueError("invalid release manifest")

    @staticmethod
    def _limit(kind):
        raise ValueError("release manifest " + kind + " exceeds limit")


def _bounded_manifest_bytes(manifest_bytes):
    if type(manifest_bytes) is not bytes:
        raise ValueError("invalid release manifest")
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise ValueError("release manifest byte limit exceeds limit")
    _ManifestPreflight(manifest_bytes).validate()
    return manifest_bytes


def _validated_manifest(manifest_bytes):
    manifest_bytes = _bounded_manifest_bytes(manifest_bytes)
    try:
        manifest = json.loads(manifest_bytes.decode("ascii"))
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("invalid release manifest") from error
    expected_keys = {
        "abi_tag",
        "app_version",
        "assets",
        "mode",
        "product",
        "release_id",
        "schema",
        "seeds",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        raise ValueError("invalid release manifest schema")
    if (manifest["product"] != PRODUCT
            or type(manifest["schema"]) is not int
            or manifest["schema"] != SCHEMA_VERSION
            or manifest["mode"] not in (SOURCE_MODE, MPY_MODE)
            or not isinstance(manifest["app_version"], str)
            or not manifest["app_version"].isascii()
            or any(ord(char) < 32 or ord(char) > 126
                   for char in manifest["app_version"])
            or len(manifest["app_version"])
            > _MAX_MANIFEST_ROOT_STRING_BYTES):
        raise ValueError("invalid release manifest identity")
    expected_abi = (
        MPY_ABI_TAG if manifest["mode"] == MPY_MODE else SOURCE_ABI_TAG)
    if (manifest["abi_tag"] != expected_abi
            or len(manifest["abi_tag"])
            > _MAX_MANIFEST_ROOT_STRING_BYTES):
        raise ValueError("invalid release manifest ABI")
    if _canonical_json(manifest) != manifest_bytes:
        raise ValueError("release manifest is not canonical")

    release_id = manifest["release_id"]
    if not isinstance(release_id, str) or len(release_id) > 64:
        raise ValueError("invalid release manifest identity")
    payload = dict(manifest)
    del payload["release_id"]
    expected_id = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if release_id != expected_id:
        raise ValueError("release manifest digest mismatch")

    record_keys = {
        "format", "key", "path", "role", "sha256", "size", "zone"}
    seen_keys = set()
    seen_paths = set()
    for collection_name, allowed_role, allowed_formats in (
            ("assets", ("bootstrap_fixed", "managed_release"),
             (SOURCE_MODE, MPY_MODE, "font")),
            ("seeds", ("seed_if_absent",), ("seed",))):
        records = manifest[collection_name]
        if not isinstance(records, list):
            raise ValueError("invalid release manifest records")
        record_limit = (
            _MAX_MANIFEST_ASSET_RECORDS
            if collection_name == "assets"
            else _MAX_MANIFEST_SEED_RECORDS)
        if len(records) > record_limit:
            raise ValueError("release manifest record count exceeds limit")
        for record in records:
            if not isinstance(record, dict) or set(record) != record_keys:
                raise ValueError("invalid release manifest asset")
            if (not isinstance(record["format"], str)
                    or record["format"] not in allowed_formats
                    or record["role"] not in allowed_role
                    or record["zone"] not in ("internal", "sd")
                    or not isinstance(record["key"], str)
                    or not isinstance(record["path"], str)
                    or type(record["size"]) is not int
                    or record["size"] < 0
                    or not isinstance(record["sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None):
                raise ValueError("invalid release manifest asset")
            if (len(record["format"])
                    > _MAX_MANIFEST_FIELD_STRING_BYTES
                    or len(record["key"]) > _MAX_MANIFEST_KEY_BYTES
                    or len(record["path"]) > _MAX_MANIFEST_PATH_BYTES
                    or len(record["role"])
                    > _MAX_MANIFEST_FIELD_STRING_BYTES
                    or len(record["zone"])
                    > _MAX_MANIFEST_FIELD_STRING_BYTES):
                raise ValueError("release manifest asset exceeds string limit")
            path = _normalize_path(record["path"])
            if path != record["path"]:
                raise ValueError("invalid release manifest path")
            if _is_reserved_slot_metadata_path(
                    record["role"], record["zone"], path):
                raise ValueError(
                    "reserved slot metadata path in release manifest")
            folded_path = (record["zone"], path.casefold())
            if record["key"] in seen_keys or folded_path in seen_paths:
                raise ValueError("release manifest asset collision")
            seen_keys.add(record["key"])
            seen_paths.add(folded_path)
    return manifest


def validate_release_plan(plan):
    """Reject any plan object that diverges from its canonical manifest."""
    if (type(plan) is not ReleasePlan
            or type(plan.assets) is not tuple
            or type(plan.manifest_bytes) is not bytes):
        raise ValueError("release plan must be deeply immutable")
    if len(plan.assets) > _MAX_RELEASE_PLAN_ASSETS:
        raise ValueError("release plan asset count exceeds limit")
    if any(
            type(asset) is not ReleaseAsset
            or type(asset.payload) is not bytes
            for asset in plan.assets):
        raise ValueError("release plan must be deeply immutable")
    manifest_bytes = _bounded_manifest_bytes(plan.manifest_bytes)
    actual_manifest_sha256 = hashlib.sha256(
        manifest_bytes).hexdigest()
    if actual_manifest_sha256 != plan.manifest_sha256:
        raise ValueError("release plan manifest digest mismatch")
    manifest = _validated_manifest(manifest_bytes)
    if (plan.schema != manifest["schema"]
            or plan.app_version != manifest["app_version"]
            or plan.mode != manifest["mode"]
            or plan.abi_tag != manifest["abi_tag"]
            or plan.release_id != manifest["release_id"]):
        raise ValueError("release plan identity does not match manifest")

    _validate_release_assets(plan.assets)
    allowed_roles = {
        "bootstrap_fixed",
        "host_only",
        "managed_release",
        "seed_if_absent",
    }
    for asset in plan.assets:
        if asset.role not in allowed_roles:
            raise ValueError("invalid release plan asset role")
        payload = asset.payload
        if (len(payload) != asset.size
                or hashlib.sha256(payload).hexdigest() != asset.sha256):
            raise ValueError(
                "release plan asset digest mismatch: " + asset.key)

    expected_assets = [
        _asset_record(asset)
        for asset in plan.assets
        if asset.role in ("bootstrap_fixed", "managed_release")
    ]
    expected_seeds = [
        _asset_record(asset)
        for asset in plan.assets
        if asset.role == "seed_if_absent"
    ]
    if (manifest["assets"] != expected_assets
            or manifest["seeds"] != expected_seeds):
        raise ValueError("release plan assets do not match manifest")
    return manifest


def cleanup_candidates(
        previous_manifest_bytes, expected_manifest_sha256, current_plan):
    """Return only previously-owned managed paths absent from the next plan."""
    previous_manifest_bytes = _bounded_manifest_bytes(
        previous_manifest_bytes)
    actual_manifest_sha256 = hashlib.sha256(
        previous_manifest_bytes).hexdigest()
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise ValueError("trusted release manifest hash mismatch")
    previous = _validated_manifest(previous_manifest_bytes)
    validate_release_plan(current_plan)
    previous_paths = set()
    for asset in previous["assets"]:
        location = (asset["zone"], asset["path"])
        if _is_protected_user_path(*location):
            raise ValueError("protected user path in managed manifest")
        if asset["role"] == "managed_release":
            previous_paths.add(location)
    current_owned_paths = {
        (asset.zone, asset.relative_path.casefold())
        for asset in current_plan.assets
        if asset.role in ("bootstrap_fixed", "managed_release")
    }
    return tuple(sorted(
        location
        for location in previous_paths
        if (location[0], location[1].casefold()) not in current_owned_paths
    ))


def plan_release(snapshot, mode):
    """Return a deterministic plan without performing filesystem or device I/O."""
    if mode not in (SOURCE_MODE, MPY_MODE):
        raise ValueError("unsupported release mode: " + str(mode))

    build_files = dict(snapshot.build_files)
    assets = tuple(sorted(
        (
            asset
            for path, content in snapshot.source_files
            for asset in _source_assets(path, content, mode, build_files)
        ),
        key=lambda asset: asset.key,
    ))
    _validate_release_assets(assets)
    owned_assets = tuple(
        asset
        for asset in assets
        if asset.role in ("bootstrap_fixed", "managed_release")
    )
    seed_assets = tuple(
        asset for asset in assets if asset.role == "seed_if_absent")
    app_version = _app_version(snapshot)
    abi_tag = MPY_ABI_TAG if mode == MPY_MODE else SOURCE_ABI_TAG
    release_payload = {
        "abi_tag": abi_tag,
        "app_version": app_version,
        "assets": [_asset_record(asset) for asset in owned_assets],
        "mode": mode,
        "product": PRODUCT,
        "schema": SCHEMA_VERSION,
        "seeds": [_asset_record(asset) for asset in seed_assets],
    }
    release_id = hashlib.sha256(_canonical_json(release_payload)).hexdigest()
    manifest = dict(release_payload)
    manifest["release_id"] = release_id
    manifest_bytes = _canonical_json(manifest)
    return ReleasePlan(
        schema=SCHEMA_VERSION,
        app_version=app_version,
        mode=mode,
        abi_tag=abi_tag,
        release_id=release_id,
        assets=assets,
        manifest_bytes=manifest_bytes,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
