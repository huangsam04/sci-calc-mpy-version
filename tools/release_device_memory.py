"""In-memory A/B slot release adapter for host transaction tests."""

from dataclasses import dataclass, field, replace
import hashlib
import json

from tools.release_protocol import (
    ColdBootObservation,
    ReleaseSmokeResult,
    SelectionTicket,
    SelectorRecord,
    SlotImage,
    SlotRef,
    run_guarded_session,
)


_SLOT_NAMES = ("A", "B")


def _fold_location(location):
    return location[0], location[1].casefold()


def _matching_location(mapping, location):
    folded = _fold_location(location)
    for existing in mapping:
        if _fold_location(existing) == folded:
            return existing
    return None


def _case_put(mapping, location, payload):
    existing = _matching_location(mapping, location)
    if existing is not None and existing != location:
        del mapping[existing]
    mapping[location] = payload


def _case_setdefault(mapping, location, payload):
    if _matching_location(mapping, location) is None:
        mapping[location] = payload


def _case_remove(mapping, location):
    existing = _matching_location(mapping, location)
    if existing is not None:
        del mapping[existing]


def _validate_selector_state(state):
    selector = state.selector
    if not isinstance(selector, SelectorRecord):
        raise ValueError("invalid selector record")
    if (type(selector.generation) is not int
            or selector.generation < 0):
        raise ValueError("invalid selector generation")
    if type(selector.retired) is not tuple:
        raise ValueError("invalid selector retired slots")
    if type(selector.confirmation_pending) is not bool:
        raise ValueError("invalid selector confirmation state")

    occupied = {}
    for role, ref in (
            ("confirmed", selector.confirmed),
            ("trial", selector.trial)):
        if ref is None:
            continue
        if not isinstance(ref, SlotRef) or ref.name not in _SLOT_NAMES:
            raise ValueError("invalid selector " + role + " slot")
        if ref.name in occupied:
            raise ValueError("selector slot roles overlap")
        occupied[ref.name] = role
        image = state.slot_images.get(ref.name)
        if (not isinstance(image, SlotImage)
                or image.slot_ref != ref):
            raise ValueError(
                "selector " + role + " slot image mismatch")

    retired_names = set()
    for ref in selector.retired:
        if not isinstance(ref, SlotRef) or ref.name not in _SLOT_NAMES:
            raise ValueError("invalid selector retired slot")
        if ref.name in occupied or ref.name in retired_names:
            raise ValueError("selector slot roles overlap")
        retired_names.add(ref.name)
        image = state.slot_images.get(ref.name)
        if image is not None and (
                not isinstance(image, SlotImage)
                or image.slot_ref != ref):
            raise ValueError("selector retired slot image mismatch")
        if selector.confirmation_pending and image is None:
            raise ValueError("selector rollback slot image is missing")
    if len(retired_names) > 1:
        raise ValueError("selector has too many retired slots")
    if (selector.trial is None
            and (selector.trial_generation is not None
                 or selector.trial_consumed is not False)):
        raise ValueError("selector has trial metadata without a trial")
    if (selector.trial is not None
            and (type(selector.trial_generation) is not int
                 or selector.trial_generation <= 0
                 or selector.trial_generation > selector.generation
                 or type(selector.trial_consumed) is not bool)):
        raise ValueError("invalid selector trial metadata")
    if selector.trial is not None:
        if (selector.trial_consumed
                and selector.trial_generation >= selector.generation):
            raise ValueError("invalid selector consumed trial generation")
        if (not selector.trial_consumed
                and selector.trial_generation != selector.generation):
            raise ValueError(
                "invalid selector unconsumed trial generation")
    if (selector.confirmation_pending
            and (selector.confirmed is None
                 or selector.trial is not None)):
        raise ValueError("invalid selector pending confirmation")
    return selector


def _managed_assets(plan):
    return tuple(
        (
            asset.role,
            asset.zone,
            asset.relative_path,
            bytes(asset.payload),
        )
        for asset in plan.assets
        if asset.role == "managed_release"
    )


def _slot_image(plan, slot_name):
    ref = SlotRef(slot_name, plan.release_id, plan.manifest_sha256)
    return SlotImage(
        slot_ref=ref,
        manifest_bytes=bytes(plan.manifest_bytes),
        assets=_managed_assets(plan),
    )


def _validated_slot_manifest(image):
    if (not isinstance(image, SlotImage)
            or image.slot_ref.name not in _SLOT_NAMES
            or type(image.manifest_bytes) is not bytes
            or type(image.assets) is not tuple):
        raise ValueError("invalid slot image")
    actual_manifest_sha256 = hashlib.sha256(
        image.manifest_bytes).hexdigest()
    if actual_manifest_sha256 != image.slot_ref.manifest_sha256:
        raise ValueError("confirmed slot manifest hash mismatch")
    try:
        manifest = json.loads(image.manifest_bytes)
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("invalid slot manifest") from error
    if (not isinstance(manifest, dict)
            or not isinstance(manifest.get("assets"), list)):
        raise ValueError("invalid slot manifest")
    if manifest.get("release_id") != image.slot_ref.release_id:
        raise ValueError("slot release identity mismatch")
    expected = []
    for record in manifest["assets"]:
        if not isinstance(record, dict):
            raise ValueError("invalid slot manifest asset")
        if record.get("role") == "managed_release":
            expected.append(record)
    expected = tuple(expected)
    if len(expected) != len(image.assets):
        raise ValueError("slot asset inventory mismatch")
    for record, actual in zip(expected, image.assets):
        if (not isinstance(actual, tuple)
                or len(actual) != 4
                or actual[0] != "managed_release"
                or actual[0] != record.get("role")
                or actual[1] != record.get("zone")
                or actual[2] != record.get("path")
                or type(actual[3]) is not bytes
                or len(actual[3]) != record.get("size")
                or hashlib.sha256(actual[3]).hexdigest()
                != record.get("sha256")):
            raise ValueError("slot asset bytes do not match manifest")
    return manifest


def _smoke_from_image(image, identity):
    manifest = _validated_slot_manifest(image)
    return ReleaseSmokeResult(
        release_id=image.slot_ref.release_id,
        app_version=manifest["app_version"],
        mode=manifest["mode"],
        abi_tag=manifest["abi_tag"],
        resident_runtime=True,
        root_visible=True,
        buffers=(("main", 8192, identity),),
    )


@dataclass(frozen=True, slots=True)
class _StagedRelease:
    slot_name: str
    manifest_bytes: bytes
    manifest_sha256: str
    assets: tuple


@dataclass(slots=True)
class InMemoryReleaseState:
    shared_files: dict
    bootstrap_files: dict
    slot_images: dict
    selector: SelectorRecord
    slot_drafts: dict = field(default_factory=dict)
    last_boot_observation: object = None
    rollback_attempts: int = 0
    sessions_started: int = 0
    device_write_attempts: int = 0
    reset_attempts: int = 0
    resets: int = 0
    close_attempts: int = 0
    sessions_closed: int = 0
    session_open: bool = False
    boot_count: int = 0
    event_log: list = field(default_factory=list)

    @classmethod
    def empty(cls, extra_files=()):
        shared = {}
        for location, payload in extra_files:
            _case_put(shared, location, bytes(payload))
        return cls(
            shared_files=shared,
            bootstrap_files={},
            slot_images={},
            selector=SelectorRecord(
                generation=0,
                confirmed=None,
                trial=None,
                trial_generation=None,
                trial_consumed=False,
                retired=(),
            ),
        )

    @classmethod
    def with_confirmed(
            cls, plan, extra_files=(), missing_paths=()):
        shared = {}
        bootstrap = {}
        for asset in plan.assets:
            location = (asset.zone, asset.relative_path)
            if asset.role == "seed_if_absent":
                _case_put(shared, location, bytes(asset.payload))
            elif asset.role == "bootstrap_fixed":
                _case_put(bootstrap, location, bytes(asset.payload))
        for location, payload in extra_files:
            _case_put(shared, location, bytes(payload))
        for location in missing_paths:
            _case_remove(shared, location)

        image = _slot_image(plan, "A")
        selector = SelectorRecord(
            generation=1,
            confirmed=image.slot_ref,
            trial=None,
            trial_generation=None,
            trial_consumed=False,
            retired=(),
        )
        state = cls(
            shared_files=shared,
            bootstrap_files=bootstrap,
            slot_images={"A": image},
            selector=selector,
            boot_count=1,
        )
        state.last_boot_observation = ColdBootObservation(
            selector_generation=selector.generation,
            selection_generation=None,
            boot_id=1,
            selected=image.slot_ref,
            smoke=_smoke_from_image(image, 1),
        )
        return state

    @property
    def confirmed_release_id(self):
        ref = self.selector.confirmed
        return None if ref is None else ref.release_id

    @property
    def trial_release_id(self):
        ref = self.selector.trial
        return None if ref is None else ref.release_id

    @property
    def boot_release_id(self):
        observation = self.last_boot_observation
        if observation is None or observation.selected is None:
            return None
        return observation.selected.release_id

    @property
    def staged_releases(self):
        return self.slot_drafts

    @property
    def confirmed_manifests(self):
        ref = self.selector.confirmed
        if ref is None:
            return {}
        image = self.slot_images.get(ref.name)
        if image is None or image.slot_ref != ref:
            return {}
        return {
            ref.release_id: (
                image.manifest_bytes,
                ref.manifest_sha256,
            ),
        }

    def active_slot_files(self):
        observation = self.last_boot_observation
        if observation is None or observation.selected is None:
            return {}
        return self.slot_files(observation.selected)

    def slot_files(self, slot_ref):
        image = self.slot_images.get(slot_ref.name)
        if image is None or image.slot_ref != slot_ref:
            return {}
        return {
            (zone, path): payload
            for _role, zone, path, payload in image.assets
        }

    def active_files(self):
        files = dict(self.shared_files)
        files.update(self.bootstrap_files)
        files.update(self.active_slot_files())
        return files


class _FailureController:
    __slots__ = ("_counts", "_failures")

    def __init__(self, failures):
        configured = {}
        for event, occurrence, error in failures:
            key = (str(event), int(occurrence))
            if key in configured or key[1] <= 0:
                raise ValueError("invalid in-memory release failure script")
            configured[key] = error
        self._failures = configured
        self._counts = {}

    def raise_if_configured(self, event):
        occurrence = self._counts.get(event, 0) + 1
        self._counts[event] = occurrence
        error = self._failures.get((event, occurrence))
        if error is not None:
            raise error


class _InMemoryReleaseSession:
    __slots__ = (
        "_closed",
        "_failures",
        "_reset",
        "_session_number",
        "_smoke_result",
        "_staged_mutator",
        "_state",
    )

    def __init__(
            self, state, failures, smoke_result, staged_mutator,
            session_number):
        self._state = state
        self._failures = failures
        self._smoke_result = smoke_result
        self._staged_mutator = staged_mutator
        self._session_number = session_number
        self._closed = False
        self._reset = False

    def _event(self, name):
        self._state.event_log.append(
            "session:" + str(self._session_number) + ":" + name)

    def _require_open(self):
        if self._closed:
            raise RuntimeError("release session is closed")

    def _candidate_slot(self):
        confirmed = self._state.selector.confirmed
        if confirmed is None:
            return "A"
        return "B" if confirmed.name == "A" else "A"

    def _validated_confirmed_image(self):
        selector = _validate_selector_state(self._state)
        ref = selector.confirmed
        if ref is None:
            return None
        image = self._state.slot_images.get(ref.name)
        if image is None or image.slot_ref != ref:
            raise ValueError("confirmed slot image is missing")
        _validated_slot_manifest(image)
        return image

    def resume_confirmed(self, plan):
        self._require_open()
        image = self._validated_confirmed_image()
        if image is None or image.slot_ref.release_id != plan.release_id:
            return None
        if (image.manifest_bytes != plan.manifest_bytes
                or image.slot_ref.manifest_sha256
                != plan.manifest_sha256):
            raise ValueError(
                "confirmed release identity conflicts with local plan")
        return SelectionTicket(
            selector_generation=self._state.selector.generation,
            slot_ref=image.slot_ref,
            already_confirmed=True,
        )

    def resume_trial(self, plan):
        self._require_open()
        selector = _validate_selector_state(self._state)
        ref = selector.trial
        if ref is None:
            return None
        image = self._state.slot_images[ref.name]
        if (ref.release_id != plan.release_id
                or ref.manifest_sha256 != plan.manifest_sha256
                or image.manifest_bytes != plan.manifest_bytes
                or image.assets != _managed_assets(plan)):
            self.reject_trial(
                SelectionTicket(selector.trial_generation, ref))
            return None
        _validated_slot_manifest(image)
        generation = selector.trial_generation
        if selector.trial_consumed:
            generation = selector.generation + 1
            self._state.selector = replace(
                selector,
                generation=generation,
                trial_generation=generation,
                trial_consumed=False,
            )
            self._event("rearm_trial")
        return SelectionTicket(generation, ref)

    def resume_cleanup(self):
        self._require_open()
        selector = _validate_selector_state(self._state)
        if selector.confirmation_pending:
            raise ValueError(
                "pending confirmation must be resolved before staging")
        if selector.trial is not None:
            raise ValueError(
                "pending trial must be resolved before staging")
        if not selector.retired:
            return
        self._event("resume_cleanup")
        self._failures.raise_if_configured("resume_cleanup")
        for retired in selector.retired:
            image = self._state.slot_images.get(retired.name)
            if image is not None and image.slot_ref == retired:
                del self._state.slot_images[retired.name]
        self._state.selector = replace(
            selector,
            generation=selector.generation + 1,
            retired=(),
        )

    def validate_bootstrap(self, plan):
        self._require_open()
        for asset in plan.assets:
            if asset.role != "bootstrap_fixed":
                continue
            location = (asset.zone, asset.relative_path)
            existing = _matching_location(
                self._state.bootstrap_files, location)
            if existing is None:
                raise ValueError(
                    "stable bootstrap anchor is not provisioned: "
                    + asset.key)
            if self._state.bootstrap_files[existing] != asset.payload:
                raise ValueError(
                    "stable bootstrap anchor hash mismatch: " + asset.key)

    def stage(self, plan):
        self._require_open()
        selector = self._state.selector
        if selector.trial is not None:
            raise ValueError("another trial selection is still pending")
        slot_name = self._candidate_slot()
        if any(ref.name == slot_name for ref in selector.retired):
            raise ValueError(
                "previous retired slot must be finalized before staging")

        draft = _StagedRelease(
            slot_name=slot_name,
            manifest_bytes=bytes(plan.manifest_bytes),
            manifest_sha256=plan.manifest_sha256,
            assets=(),
        )
        self._state.slot_drafts[plan.release_id] = draft
        assets = []
        for record in _managed_assets(plan):
            self._state.device_write_attempts += 1
            self._failures.raise_if_configured("stage_write")
            assets.append(record)
            draft = replace(draft, assets=tuple(assets))
            self._state.slot_drafts[plan.release_id] = draft

    def verify(self, plan):
        self._require_open()
        self._failures.raise_if_configured("verify")
        draft = self._state.slot_drafts[plan.release_id]
        if self._staged_mutator is not None:
            draft = self._staged_mutator(draft)
            self._state.slot_drafts[plan.release_id] = draft
        if (draft.manifest_bytes != plan.manifest_bytes
                or draft.manifest_sha256 != plan.manifest_sha256
                or draft.assets != _managed_assets(plan)):
            raise ValueError("staged release hash verification failed")

    def select_trial(self, plan):
        self._require_open()
        _validate_selector_state(self._state)
        draft = self._state.slot_drafts[plan.release_id]
        self._failures.raise_if_configured("activate_trial")
        image = SlotImage(
            slot_ref=SlotRef(
                draft.slot_name,
                plan.release_id,
                plan.manifest_sha256,
            ),
            manifest_bytes=draft.manifest_bytes,
            assets=draft.assets,
        )
        generation = self._state.selector.generation + 1
        self._state.slot_images[draft.slot_name] = image
        self._state.selector = replace(
            self._state.selector,
            generation=generation,
            trial=image.slot_ref,
            trial_generation=generation,
            trial_consumed=False,
        )
        del self._state.slot_drafts[plan.release_id]
        self._event("select_trial")
        self._failures.raise_if_configured("activate_trial_after")
        return SelectionTicket(generation, image.slot_ref)

    def reconcile_trial_selection(self, plan):
        self._require_open()
        self._failures.raise_if_configured("reconcile_trial_selection")
        selector = self._state.selector
        ref = selector.trial
        if ref is None:
            return None
        image = self._state.slot_images.get(ref.name)
        if (ref.release_id != plan.release_id
                or ref.manifest_sha256 != plan.manifest_sha256
                or image is None
                or image.slot_ref != ref
                or image.manifest_bytes != plan.manifest_bytes
                or selector.trial_generation is None):
            raise ValueError("trial selector readback is inconsistent")
        _validated_slot_manifest(image)
        return SelectionTicket(selector.trial_generation, ref)

    def abort_staging(self, release_id):
        self._require_open()
        self._failures.raise_if_configured("abort_staging")
        self._state.slot_drafts.pop(release_id, None)

    def read_boot_observation(self, ticket, trial):
        self._require_open()
        self._event("read_boot_observation")
        self._failures.raise_if_configured(
            "smoke_trial" if trial else "smoke_confirmed")
        return self._state.last_boot_observation

    def confirm_trial(self, ticket):
        self._require_open()
        selector = _validate_selector_state(self._state)
        if (selector.trial != ticket.slot_ref
                or selector.trial_generation
                != ticket.selector_generation
                or selector.trial_consumed is not True):
            raise ValueError("trial selector ticket is not confirmable")
        self._event("confirm_trial")
        self._failures.raise_if_configured("promote_before")
        retired = selector.retired
        if selector.confirmed is not None:
            retired = retired + (selector.confirmed,)
        self._state.selector = SelectorRecord(
            generation=selector.generation + 1,
            confirmed=ticket.slot_ref,
            trial=None,
            trial_generation=None,
            trial_consumed=False,
            retired=retired,
            confirmation_pending=True,
        )
        self._failures.raise_if_configured("promote_after")

    def is_release_confirmed(self, ticket):
        self._require_open()
        self._failures.raise_if_configured("reconcile_confirm")
        selector = self._state.selector
        if (selector.confirmed != ticket.slot_ref
                or selector.trial is not None
                or selector.trial_generation is not None
                or selector.trial_consumed):
            return False
        image = self._state.slot_images.get(ticket.slot_ref.name)
        if (image is None
                or image.slot_ref != ticket.slot_ref
                or hashlib.sha256(image.manifest_bytes).hexdigest()
                != ticket.slot_ref.manifest_sha256):
            return False
        try:
            _validated_slot_manifest(image)
        except ValueError:
            return False
        for retired in selector.retired:
            retired_image = self._state.slot_images.get(retired.name)
            if retired.name == ticket.slot_ref.name:
                return False
            if retired_image is None or retired_image.slot_ref != retired:
                return False
            try:
                _validated_slot_manifest(retired_image)
            except ValueError:
                return False
        return True

    def reject_trial(self, ticket):
        self._require_open()
        self._state.rollback_attempts += 1
        self._failures.raise_if_configured("rollback_trial")
        selector = self._state.selector
        if selector.trial == ticket.slot_ref:
            self._state.selector = replace(
                selector,
                generation=selector.generation + 1,
                trial=None,
                trial_generation=None,
                trial_consumed=False,
            )

    def rollback_confirmation(self, ticket):
        self._require_open()
        selector = _validate_selector_state(self._state)
        if (selector.confirmed != ticket.slot_ref
                or not selector.confirmation_pending):
            return False
        self._failures.raise_if_configured(
            "rollback_confirmation_before")
        fallback = None
        if selector.retired:
            fallback = selector.retired[0]
            fallback_image = self._state.slot_images.get(fallback.name)
            if (fallback_image is None
                    or fallback_image.slot_ref != fallback):
                raise ValueError(
                    "selector rollback slot image is missing")
            _validated_slot_manifest(fallback_image)
        failed = selector.confirmed
        reverted = SelectorRecord(
            generation=selector.generation + 1,
            confirmed=fallback,
            trial=None,
            trial_generation=None,
            trial_consumed=False,
            retired=(failed,),
            confirmation_pending=False,
        )
        self._state.selector = reverted
        self._event("rollback_confirmation")
        self._failures.raise_if_configured(
            "rollback_confirmation_after")
        image = self._state.slot_images.get(failed.name)
        if image is not None and image.slot_ref == failed:
            del self._state.slot_images[failed.name]
        self._state.selector = replace(
            reverted,
            generation=reverted.generation + 1,
            retired=(),
        )
        return True

    def finalize_release(self, ticket, plan):
        self._require_open()
        selector = _validate_selector_state(self._state)
        if selector.confirmed != ticket.slot_ref:
            raise ValueError("release is not confirmed for finalization")
        self._event("cleanup")
        self._failures.raise_if_configured("cleanup")
        if selector.confirmation_pending:
            selector = replace(
                selector,
                generation=selector.generation + 1,
                confirmation_pending=False,
            )
            self._state.selector = selector
        for retired in selector.retired:
            self._failures.raise_if_configured("cleanup_delete")
            image = self._state.slot_images.get(retired.name)
            if image is not None and image.slot_ref == retired:
                del self._state.slot_images[retired.name]
            self._failures.raise_if_configured("cleanup_delete_after")
        for asset in plan.assets:
            if asset.role == "seed_if_absent":
                _case_setdefault(
                    self._state.shared_files,
                    (asset.zone, asset.relative_path),
                    bytes(asset.payload),
                )
        self._state.selector = replace(
            selector,
            generation=selector.generation + 1,
            retired=(),
            confirmation_pending=False,
        )
        self._state.slot_drafts.pop(plan.release_id, None)
        self._failures.raise_if_configured("cleanup_after")

    def _cold_boot(self):
        selector = _validate_selector_state(self._state)
        selected = selector.confirmed
        selection_generation = None
        if selector.trial is not None:
            if not selector.trial_consumed:
                selected = selector.trial
                selection_generation = selector.trial_generation
                selector = replace(
                    selector,
                    generation=selector.generation + 1,
                    trial_consumed=True,
                )
                self._state.selector = selector
            else:
                selected = selector.confirmed

        self._state.boot_count += 1
        smoke = None
        if selected is not None:
            image = self._state.slot_images.get(selected.name)
            if image is not None and image.slot_ref == selected:
                if callable(self._smoke_result):
                    smoke = self._smoke_result(
                        self._state, selected, self._state.boot_count)
                elif self._smoke_result is not None:
                    smoke = self._smoke_result
                else:
                    smoke = _smoke_from_image(
                        image, self._state.boot_count)
        self._state.last_boot_observation = ColdBootObservation(
            selector_generation=self._state.selector.generation,
            selection_generation=selection_generation,
            boot_id=self._state.boot_count,
            selected=selected,
            smoke=smoke,
        )

    def _reset_device(self):
        self._require_open()
        if self._reset:
            raise RuntimeError("release session reset more than once")
        self._reset = True
        self._state.reset_attempts += 1
        self._event("reset")
        self._failures.raise_if_configured("reset")
        self._state.resets += 1
        self._cold_boot()

    def _close(self):
        if self._closed:
            raise RuntimeError("release session closed more than once")
        self._closed = True
        self._state.close_attempts += 1
        self._state.session_open = False
        self._state.sessions_closed += 1
        self._event("close")
        self._failures.raise_if_configured("close")


class InMemoryReleaseAdapter:
    __slots__ = ("_failures", "_smoke_result", "_staged_mutator", "_state")

    def __init__(
            self, state, failures=(), smoke_result=None,
            staged_mutator=None):
        self._state = state
        self._failures = _FailureController(failures)
        self._smoke_result = smoke_result
        self._staged_mutator = staged_mutator

    def run_session(self, operation):
        if self._state.session_open:
            raise RuntimeError("release session is already open")
        self._state.session_open = True
        self._state.sessions_started += 1
        session_number = self._state.sessions_started
        self._state.event_log.append(
            "session:" + str(session_number) + ":start")
        session = _InMemoryReleaseSession(
            self._state,
            self._failures,
            self._smoke_result,
            self._staged_mutator,
            session_number,
        )

        return run_guarded_session(
            lambda: operation(session),
            session._reset_device,
            session._close,
        )
