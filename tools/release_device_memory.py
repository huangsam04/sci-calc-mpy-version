"""In-memory release-device adapter for host transaction tests."""

from dataclasses import dataclass, field
import hashlib


_DEPLOYED_ROLES = ("bootstrap_fixed", "managed_release")


@dataclass(slots=True)
class InMemoryReleaseState:
    files: dict
    confirmed_manifests: dict
    confirmed_release_id: str
    boot_release_id: str
    staged_releases: dict = field(default_factory=dict)
    sessions_started: int = 0
    resets: int = 0
    sessions_closed: int = 0
    session_open: bool = False


@dataclass(frozen=True, slots=True)
class _StagedRelease:
    manifest_bytes: bytes
    manifest_sha256: str
    assets: tuple


class _InMemoryReleaseSession:
    __slots__ = ("_closed", "_reset", "_state")

    def __init__(self, state):
        self._state = state
        self._closed = False
        self._reset = False

    def _require_open(self):
        if self._closed:
            raise RuntimeError("release session is closed")

    def read_confirmed_manifest(self):
        self._require_open()
        return self._state.confirmed_manifests[
            self._state.confirmed_release_id
        ]

    def stage(self, plan):
        self._require_open()
        manifest_bytes = bytes(plan.manifest_bytes)
        actual_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_sha256 != plan.manifest_sha256:
            raise ValueError("release plan manifest digest mismatch")
        assets = tuple(
            (
                asset.role,
                asset.zone,
                asset.relative_path,
                bytes(asset.payload),
            )
            for asset in plan.assets
            if asset.role != "host_only"
        )
        self._state.staged_releases[plan.release_id] = _StagedRelease(
            manifest_bytes=manifest_bytes,
            manifest_sha256=actual_sha256,
            assets=assets,
        )

    def activate(self, release_id, cleanup):
        self._require_open()
        staged = self._state.staged_releases[release_id]
        files = dict(self._state.files)
        for location in cleanup:
            files.pop(location, None)
        for role, zone, path, payload in staged.assets:
            location = (zone, path)
            if role in _DEPLOYED_ROLES:
                files[location] = payload
            elif role == "seed_if_absent":
                files.setdefault(location, payload)

        self._state.files.clear()
        self._state.files.update(files)
        self._state.confirmed_manifests.clear()
        self._state.confirmed_manifests[release_id] = (
            staged.manifest_bytes,
            staged.manifest_sha256,
        )
        self._state.confirmed_release_id = release_id
        self._state.boot_release_id = release_id
        self._state.staged_releases.clear()

    def _reset_device(self):
        self._require_open()
        if self._reset:
            raise RuntimeError("release session reset more than once")
        self._reset = True
        self._state.resets += 1

    def _close(self):
        if self._closed:
            raise RuntimeError("release session closed more than once")
        self._closed = True
        self._state.session_open = False
        self._state.sessions_closed += 1


class InMemoryReleaseAdapter:
    __slots__ = ("_state",)

    def __init__(self, state):
        self._state = state

    def run_session(self, operation):
        if self._state.session_open:
            raise RuntimeError("release session is already open")
        self._state.session_open = True
        self._state.sessions_started += 1
        session = _InMemoryReleaseSession(self._state)
        try:
            return operation(session)
        finally:
            try:
                session._reset_device()
            finally:
                session._close()
