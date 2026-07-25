"""Build deterministic, immutable SCI-CALC release plans."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


SCHEMA_VERSION = 1
PRODUCT = "sci-calc"
SOURCE_MODE = "source"
MPY_MODE = "mpy"
SOURCE_ABI_TAG = "source"
MPY_ABI_TAG = "micropython-v1.29.0-preview:mpy-v6.3:xtensawin"
_BOOTSTRAP_PATHS = {
    "boot.py": ("bootstrap:boot", "boot.py"),
    "internal_main.py": ("bootstrap:main", "main.py"),
    "sdcard.py": ("bootstrap:sdcard", "sdcard.py"),
}
_INTERNAL_DISPLAY_PATHS = frozenset((
    "display/mono_palette.py",
    "display/ssd1322.py",
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
_VERSION_RE = re.compile(
    rb"""^VERSION\s*=\s*["']([^"']+)["']\s*$""",
    re.MULTILINE,
)


def _normalize_path(path):
    normalized = str(path).replace("\\", "/")
    parts = normalized.split("/")
    if (not normalized or normalized.startswith("/")
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
                return match.group(1).decode("ascii")
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


def _source_asset(path, content, mode, build_files):
    if not path.endswith(".py"):
        raise ValueError("unclassified source file: " + path)
    bootstrap = _BOOTSTRAP_PATHS.get(path)
    if bootstrap is not None:
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

    if path == "recovery.py":
        return (_asset(
            "internal:recovery",
            path,
            "source/" + path,
            content,
            "internal",
            path,
            SOURCE_MODE,
            "managed_release",
        ),)

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

    sd_or_bootstrap = _source_asset(path, content, mode, build_files)
    if path not in _INTERNAL_DISPLAY_PATHS:
        return (sd_or_bootstrap,)
    logical_path = path[:-3]
    return (
        _asset(
            "internal:" + logical_path,
            path,
            "source/" + path,
            content,
            "internal",
            path,
            SOURCE_MODE,
            "managed_release",
        ),
        sd_or_bootstrap,
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


def _validate_release_assets(assets):
    keys = set()
    paths = set()
    for asset in assets:
        if asset.key in keys:
            raise ValueError("release asset key collision: " + asset.key)
        keys.add(asset.key)
        location = (asset.zone, asset.relative_path.casefold())
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


def _validated_manifest(manifest_bytes):
    try:
        manifest = json.loads(bytes(manifest_bytes).decode("ascii"))
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
            or manifest["schema"] != SCHEMA_VERSION
            or manifest["mode"] not in (SOURCE_MODE, MPY_MODE)
            or not isinstance(manifest["app_version"], str)):
        raise ValueError("invalid release manifest identity")
    expected_abi = (
        MPY_ABI_TAG if manifest["mode"] == MPY_MODE else SOURCE_ABI_TAG)
    if manifest["abi_tag"] != expected_abi:
        raise ValueError("invalid release manifest ABI")
    if _canonical_json(manifest) != bytes(manifest_bytes):
        raise ValueError("release manifest is not canonical")

    release_id = manifest["release_id"]
    payload = dict(manifest)
    del payload["release_id"]
    expected_id = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if release_id != expected_id:
        raise ValueError("release manifest digest mismatch")

    record_keys = {
        "format", "key", "path", "role", "sha256", "size", "zone"}
    seen_keys = set()
    seen_paths = set()
    for collection_name, allowed_role in (
            ("assets", ("bootstrap_fixed", "managed_release")),
            ("seeds", ("seed_if_absent",))):
        records = manifest[collection_name]
        if not isinstance(records, list):
            raise ValueError("invalid release manifest records")
        for record in records:
            if not isinstance(record, dict) or set(record) != record_keys:
                raise ValueError("invalid release manifest asset")
            if (record["role"] not in allowed_role
                    or record["zone"] not in ("internal", "sd")
                    or not isinstance(record["key"], str)
                    or not isinstance(record["size"], int)
                    or record["size"] < 0
                    or not isinstance(record["sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None):
                raise ValueError("invalid release manifest asset")
            path = _normalize_path(record["path"])
            if path != record["path"]:
                raise ValueError("invalid release manifest path")
            folded_path = (record["zone"], path.casefold())
            if record["key"] in seen_keys or folded_path in seen_paths:
                raise ValueError("release manifest asset collision")
            seen_keys.add(record["key"])
            seen_paths.add(folded_path)
    return manifest


def cleanup_candidates(
        previous_manifest_bytes, expected_manifest_sha256, current_plan):
    """Return only previously-owned managed paths absent from the next plan."""
    actual_manifest_sha256 = hashlib.sha256(
        bytes(previous_manifest_bytes)).hexdigest()
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise ValueError("trusted release manifest hash mismatch")
    previous = _validated_manifest(previous_manifest_bytes)
    protected = {("sd", "settings.json"), ("sd", "vars.json")}
    previous_paths = set()
    for asset in previous["assets"]:
        location = (asset["zone"], asset["path"])
        folded_location = (location[0], location[1].casefold())
        if folded_location in protected:
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
