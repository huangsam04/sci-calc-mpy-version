from dataclasses import replace

import pytest

from tools.release_apply import (
    ReleaseFailure,
    ReleaseSmokeResult,
    SelectionTicket,
    SlotRef,
    apply_release,
)
from tools.release_device_memory import (
    InMemoryReleaseAdapter,
    InMemoryReleaseState,
)
from tools.release_plan import ReleaseTreeSnapshot, plan_release


def _plan(version, legacy=False, mode="source", bootstrap=None):
    files = [
        ("main.py", ("# main " + version + "\n").encode("ascii")),
        ("settings.json", b'{"brightness":20}\n'),
        ("vars.json", b'{"seed":0}\n'),
        ("version.py", ('VERSION = "' + version + '"\n').encode("ascii")),
    ]
    if legacy:
        files.append(("legacy.py", b"# old managed module\n"))
    else:
        files.append(("catalog.py", b"# new managed module\n"))
    if bootstrap is not None:
        files.append(("boot.py", bytes(bootstrap)))
    build_files = ()
    if mode == "mpy":
        build_files = tuple(
            (path[:-3] + ".mpy", b"M\x06" + content)
            for path, content in files
            if path.endswith(".py")
        )
    return plan_release(
        ReleaseTreeSnapshot.from_files(files, build_files=build_files),
        mode=mode,
    )


def _device_files(plan):
    return {
        (asset.zone, asset.relative_path): asset.payload
        for asset in plan.assets
        if asset.role in (
            "bootstrap_fixed",
            "managed_release",
            "seed_if_absent",
        )
    }


def _smoke_result(plan, **changes):
    return replace(
        ReleaseSmokeResult(
            release_id=plan.release_id,
            app_version=plan.app_version,
            mode=plan.mode,
            abi_tag=plan.abi_tag,
            resident_runtime=True,
            root_visible=True,
            buffers=(("main", 8192, 12345),),
        ),
        **changes,
    )


def _state_with_old_release(old_plan, extra_files=(), missing_paths=()):
    return InMemoryReleaseState.with_confirmed(
        old_plan,
        extra_files=extra_files,
        missing_paths=missing_paths,
    )


def _assert_one_closed_reset_session(state):
    assert state.sessions_started >= 1
    assert state.reset_attempts == state.sessions_started
    assert state.resets == state.sessions_started
    assert state.close_attempts == state.sessions_started
    assert state.sessions_closed == state.sessions_started
    assert state.session_open is False


def _retired_release_ids(state):
    return tuple(ref.release_id for ref in state.selector.retired)


def test_apply_release_promotes_one_release_and_preserves_unmanaged_bytes():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    sentinels = {
        ("sd", "settings.json"): b'{"brightness":73,"user":true}\n',
        ("sd", "vars.json"): b'{"answer":42}\n',
        ("sd", "Add-ons/user_pack.py"): b"# user add-on\n",
        ("sd", "notes/private.bin"): b"\x00USER\xff",
        ("internal", "board-calibration.bin"): b"CALIBRATION",
    }
    state = _state_with_old_release(old_plan, sentinels.items())
    adapter = InMemoryReleaseAdapter(state)

    result = apply_release(new_plan, adapter)

    assert result == new_plan.release_id
    assert state.confirmed_manifests == {
        new_plan.release_id: (
            new_plan.manifest_bytes,
            new_plan.manifest_sha256,
        ),
    }
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.staged_releases == {}
    active_files = state.active_files()
    assert ("sd", "legacy.py") not in active_files
    for asset in new_plan.assets:
        if asset.role in ("bootstrap_fixed", "managed_release"):
            assert active_files[
                (asset.zone, asset.relative_path)] == asset.payload
    assert {
        location: state.shared_files[location]
        for location in sentinels
    } == sentinels
    _assert_one_closed_reset_session(state)


def test_trial_smoke_requires_a_reset_and_a_fresh_session():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert state.event_log == [
        "session:1:start",
        "session:1:select_trial",
        "session:1:reset",
        "session:1:close",
        "session:2:start",
        "session:2:read_boot_observation",
        "session:2:confirm_trial",
        "session:2:reset",
        "session:2:close",
        "session:3:start",
        "session:3:read_boot_observation",
        "session:3:cleanup",
        "session:3:reset",
        "session:3:close",
    ]
    assert state.sessions_started == 3
    assert state.reset_attempts == 3
    assert state.close_attempts == 3
    assert state.session_open is False


def test_apply_release_creates_seeds_only_when_user_files_are_absent():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    seed_state = _state_with_old_release(
        old_plan,
        missing_paths=(
            ("sd", "settings.json"),
            ("sd", "vars.json"),
        ),
    )
    seed_adapter = InMemoryReleaseAdapter(seed_state)

    apply_release(new_plan, seed_adapter)

    expected_seeds = {
        (asset.zone, asset.relative_path): asset.payload
        for asset in new_plan.assets
        if asset.role == "seed_if_absent"
    }
    assert {
        location: seed_state.shared_files[location]
        for location in expected_seeds
    } == expected_seeds
    _assert_one_closed_reset_session(seed_state)


@pytest.mark.parametrize(
    ("location", "payload"),
    (
        (("sd", "SETTINGS.JSON"), b'{"brightness":88}\n'),
        (("sd", "Vars.Json"), b'{"answer":42}\n'),
    ),
)
def test_seed_creation_is_case_insensitive_and_preserves_user_bytes(
        location, payload):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(
        old_plan,
        extra_files=((location, payload),),
    )

    apply_release(new_plan, InMemoryReleaseAdapter(state))

    matching = {
        existing: value
        for existing, value in state.shared_files.items()
        if (existing[0], existing[1].casefold())
        == (location[0], location[1].casefold())
    }
    assert matching == {location: payload}
    _assert_one_closed_reset_session(state)


@pytest.mark.parametrize("mode", ("source", "mpy"))
def test_apply_release_supports_a_first_install_without_confirmed_manifest(
        mode):
    plan = _plan("1.4.0", mode=mode)
    state = InMemoryReleaseState.empty()

    result = apply_release(plan, InMemoryReleaseAdapter(state))

    assert result == plan.release_id
    assert state.confirmed_release_id == plan.release_id
    assert state.boot_release_id == plan.release_id
    assert state.confirmed_manifests == {
        plan.release_id: (
            plan.manifest_bytes,
            plan.manifest_sha256,
        ),
    }
    assert state.active_files() == _device_files(plan)
    _assert_one_closed_reset_session(state)


def test_regular_release_only_verifies_the_stable_bootstrap_anchor():
    anchor = b"# immutable supervisor entry\n"
    old_plan = _plan(
        "1.3.0", legacy=True, bootstrap=anchor)
    new_plan = _plan("1.4.0", bootstrap=anchor)
    state = _state_with_old_release(old_plan)

    apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert state.bootstrap_files[("internal", "boot.py")] == anchor
    assert state.confirmed_release_id == new_plan.release_id
    _assert_one_closed_reset_session(state)


@pytest.mark.parametrize("provisioned", (True, False))
def test_regular_release_refuses_to_write_a_changed_or_missing_bootstrap(
        provisioned):
    old_anchor = b"# stable supervisor v1\n"
    new_anchor = b"# unprovisioned supervisor v2\n"
    old_plan = _plan(
        "1.3.0", legacy=True, bootstrap=old_anchor)
    new_plan = _plan("1.4.0", bootstrap=new_anchor)
    state = (
        _state_with_old_release(old_plan)
        if provisioned
        else InMemoryReleaseState.empty()
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert caught.value.phase == "bootstrap"
    assert "stable bootstrap anchor" in str(caught.value.primary)
    assert state.device_write_attempts == 0
    assert state.confirmed_release_id == (
        old_plan.release_id if provisioned else None)
    _assert_one_closed_reset_session(state)


def test_first_slot_install_preserves_every_preexisting_shared_file():
    plan = _plan("1.4.0")
    sentinels = {
        ("sd", "settings.json"): b'{"brightness":77}\n',
        ("sd", "vars.json"): b'{"answer":42}\n',
        ("sd", "functions/user.py"): b"# user add-on\n",
        ("sd", "main.py"): b"# legacy root application\n",
        ("sd", "unknown.bin"): b"UNKNOWN",
    }
    state = InMemoryReleaseState.empty(sentinels.items())

    apply_release(plan, InMemoryReleaseAdapter(state))

    assert {
        location: state.shared_files[location]
        for location in sentinels
    } == sentinels
    assert state.confirmed_release_id == plan.release_id
    assert state.boot_release_id == plan.release_id
    assert state.active_slot_files()[("sd", "catalog.py")] == next(
        asset.payload
        for asset in plan.assets
        if (asset.zone, asset.relative_path) == ("sd", "catalog.py")
    )
    _assert_one_closed_reset_session(state)


@pytest.mark.parametrize(
    ("old_mode", "new_mode"),
    (("source", "mpy"), ("mpy", "source")),
)
def test_release_mode_switch_leaves_no_shadow_python_extension(
        old_mode, new_mode):
    old_plan = _plan("1.3.0", legacy=True, mode=old_mode)
    new_plan = _plan("1.4.0", mode=new_mode)
    state = _state_with_old_release(old_plan)

    apply_release(new_plan, InMemoryReleaseAdapter(state))

    active_sd_paths = {
        path.casefold()
        for zone, path in state.active_slot_files()
        if zone == "sd"
    }
    for asset in new_plan.assets:
        if (asset.zone != "sd"
                or asset.role != "managed_release"
                or not asset.relative_path.endswith((".py", ".mpy"))):
            continue
        base = asset.relative_path.rsplit(".", 1)[0].casefold()
        expected = (
            base + ".mpy" if new_mode == "mpy" else base + ".py")
        shadow = (
            base + ".py" if new_mode == "mpy" else base + ".mpy")
        assert expected in active_sd_paths
        assert shadow not in active_sd_paths
    _assert_one_closed_reset_session(state)


@pytest.mark.parametrize(
    ("old_mode", "new_mode"),
    (("source", "mpy"), ("mpy", "source")),
)
def test_mode_switch_finalize_failure_cannot_boot_a_shadow_extension(
        old_mode, new_mode):
    old_plan = _plan("1.3.0", legacy=True, mode=old_mode)
    new_plan = _plan("1.4.0", mode=new_mode)
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(("cleanup", 1, OSError("erase failed")),),
            ),
        )

    assert caught.value.phase == "cleanup"
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    active_paths = {
        path.casefold()
        for zone, path in state.active_slot_files()
        if zone == "sd"
    }
    for asset in new_plan.assets:
        if (asset.zone != "sd"
                or asset.role != "managed_release"
                or not asset.relative_path.endswith((".py", ".mpy"))):
            continue
        base = asset.relative_path.rsplit(".", 1)[0].casefold()
        expected = (
            base + ".mpy" if new_mode == "mpy" else base + ".py")
        shadow = (
            base + ".py" if new_mode == "mpy" else base + ".mpy")
        assert expected in active_paths
        assert shadow not in active_paths
    assert _retired_release_ids(state) == (old_plan.release_id,)
    assert len(state.slot_images) == 2
    _assert_one_closed_reset_session(state)


def test_apply_release_rejects_tampered_payload_before_a_device_session():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    catalog_asset = next(
        asset for asset in new_plan.assets if asset.key == "sd:catalog")
    tampered_asset = replace(
        catalog_asset, payload=b"# tampered after plan\n")
    tampered_plan = replace(
        new_plan,
        assets=tuple(
            tampered_asset if asset is catalog_asset else asset
            for asset in new_plan.assets
        ),
    )
    state = _state_with_old_release(old_plan)
    adapter = InMemoryReleaseAdapter(state)

    with pytest.raises(ValueError, match="release plan asset digest mismatch"):
        apply_release(tampered_plan, adapter)

    assert state.sessions_started == 0
    assert state.device_write_attempts == 0
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id


def test_apply_release_rejects_asset_manifest_divergence_before_device_session():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    divergent_plan = replace(
        new_plan,
        assets=tuple(
            asset
            for asset in new_plan.assets
            if asset.key != "sd:catalog"
        ),
    )
    state = _state_with_old_release(old_plan)

    with pytest.raises(ValueError, match="assets do not match manifest"):
        apply_release(divergent_plan, InMemoryReleaseAdapter(state))

    assert state.sessions_started == 0
    assert state.device_write_attempts == 0
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id


def test_apply_release_rejects_a_ticket_for_a_different_release():
    new_plan = _plan("1.4.0")

    class BogusTicketAdapter:
        def run_session(self, operation):
            return SelectionTicket(
                selector_generation=7,
                slot_ref=SlotRef("A", "9.9.9", "f" * 64),
            )

    with pytest.raises(
            ValueError, match="selection ticket release identity mismatch"):
        apply_release(new_plan, BogusTicketAdapter())


@pytest.mark.parametrize("mutation", ("asset-list", "mutable-payload"))
def test_apply_release_requires_a_deeply_immutable_plan(mutation):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    if mutation == "asset-list":
        mutable_plan = replace(new_plan, assets=list(new_plan.assets))
    else:
        first = new_plan.assets[0]
        mutable_asset = replace(first, payload=bytearray(first.payload))
        mutable_plan = replace(
            new_plan,
            assets=(mutable_asset,) + new_plan.assets[1:],
        )
    state = _state_with_old_release(old_plan)

    with pytest.raises(ValueError, match="immutable"):
        apply_release(mutable_plan, InMemoryReleaseAdapter(state))

    assert state.sessions_started == 0
    assert state.device_write_attempts == 0
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id


def test_corrupt_confirmed_slot_manifest_fails_before_candidate_writes():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    old_ref = state.selector.confirmed
    state.slot_images[old_ref.name] = replace(
        state.slot_images[old_ref.name],
        manifest_bytes=old_plan.manifest_bytes + b" ",
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert caught.value.phase == "resume_confirmed"
    assert "manifest hash" in str(caught.value.primary)
    assert state.device_write_attempts == 0
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    _assert_one_closed_reset_session(state)


def test_corrupt_confirmed_slot_payload_cannot_be_self_certified_by_manifest():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure):
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(("cleanup", 1, OSError("cleanup failed")),),
            ),
        )

    confirmed_ref = state.selector.confirmed
    image = state.slot_images[confirmed_ref.name]
    first = image.assets[0]
    state.slot_images[confirmed_ref.name] = replace(
        image,
        assets=((first[:-1] + (first[-1] + b"corrupt",)),)
        + image.assets[1:],
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert caught.value.phase == "resume_confirmed"
    assert "slot asset" in str(caught.value.primary)
    assert state.selector.retired != ()
    assert state.confirmed_release_id == new_plan.release_id
    _assert_one_closed_reset_session(state)


def test_selector_cannot_retire_the_currently_confirmed_slot():
    plan = _plan("1.4.0")
    state = InMemoryReleaseState.with_confirmed(plan)
    confirmed_ref = state.selector.confirmed
    state.selector = replace(
        state.selector,
        retired=(confirmed_ref,),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(plan, InMemoryReleaseAdapter(state))

    assert caught.value.phase == "resume_confirmed"
    assert "selector" in str(caught.value.primary)
    assert state.selector.confirmed == confirmed_ref
    assert confirmed_ref.name in state.slot_images
    assert state.boot_release_id == plan.release_id
    _assert_one_closed_reset_session(state)


def test_selector_cannot_use_the_confirmed_slot_as_a_trial():
    plan = _plan("1.4.0")
    state = InMemoryReleaseState.with_confirmed(plan)
    confirmed_ref = state.selector.confirmed
    state.selector = replace(
        state.selector,
        trial=confirmed_ref,
        trial_generation=state.selector.generation,
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(plan, InMemoryReleaseAdapter(state))

    assert caught.value.phase == "resume_confirmed"
    assert "selector" in str(caught.value.primary)
    assert state.selector.confirmed == confirmed_ref
    assert confirmed_ref.name in state.slot_images
    assert state.boot_release_id == plan.release_id
    _assert_one_closed_reset_session(state)


def test_selector_without_a_trial_cannot_keep_trial_metadata():
    plan = _plan("1.4.0")
    state = InMemoryReleaseState.with_confirmed(plan)
    confirmed_ref = state.selector.confirmed
    state.selector = replace(
        state.selector,
        trial_generation=state.selector.generation,
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(plan, InMemoryReleaseAdapter(state))

    assert caught.value.phase == "resume_confirmed"
    assert "selector" in str(caught.value.primary)
    assert state.selector.confirmed == confirmed_ref
    assert confirmed_ref.name in state.slot_images
    assert state.boot_release_id == plan.release_id
    _assert_one_closed_reset_session(state)


def test_selector_trial_requires_a_valid_selection_generation():
    plan = _plan("1.4.0")
    state = InMemoryReleaseState.with_confirmed(plan)
    confirmed_ref = state.selector.confirmed
    state.selector = replace(
        state.selector,
        trial=replace(confirmed_ref, name="B"),
        trial_generation=None,
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(plan, InMemoryReleaseAdapter(state))

    assert caught.value.phase == "resume_confirmed"
    assert "selector" in str(caught.value.primary)
    assert state.selector.confirmed == confirmed_ref
    assert confirmed_ref.name in state.slot_images
    assert state.boot_release_id == plan.release_id
    _assert_one_closed_reset_session(state)


def test_selector_trial_must_reference_its_slot_image():
    plan = _plan("1.4.0")
    state = InMemoryReleaseState.with_confirmed(plan)
    confirmed_ref = state.selector.confirmed
    state.selector = replace(
        state.selector,
        generation=state.selector.generation + 1,
        trial=replace(confirmed_ref, name="B"),
        trial_generation=state.selector.generation + 1,
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(plan, InMemoryReleaseAdapter(state))

    assert caught.value.phase == "resume_confirmed"
    assert "selector" in str(caught.value.primary)
    assert state.selector.confirmed == confirmed_ref
    assert confirmed_ref.name in state.slot_images
    assert state.boot_release_id == plan.release_id
    _assert_one_closed_reset_session(state)


def test_cold_boot_fails_closed_instead_of_guessing_from_an_invalid_selector():
    plan = _plan("1.4.0")
    state = InMemoryReleaseState.with_confirmed(plan)
    confirmed_ref = state.selector.confirmed
    previous_observation = state.last_boot_observation

    def corrupt_after_session_operation(_session):
        state.selector = replace(
            state.selector,
            retired=(confirmed_ref,),
        )

    with pytest.raises(ReleaseFailure) as caught:
        InMemoryReleaseAdapter(state).run_session(
            corrupt_after_session_operation)

    assert caught.value.phase == "reset"
    assert "selector" in str(caught.value.primary)
    assert state.last_boot_observation is previous_observation
    assert state.boot_count == previous_observation.boot_id
    assert state.selector.confirmed == confirmed_ref
    assert confirmed_ref.name in state.slot_images
    _assert_one_closed_reset_session(state)


def test_trial_selection_revalidates_selector_before_committing():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    confirmed_ref = state.selector.confirmed

    def corrupt_selector_after_staging(draft):
        state.selector = replace(
            state.selector,
            retired=(confirmed_ref,),
        )
        return draft

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                staged_mutator=corrupt_selector_after_staging,
            ),
        )

    assert caught.value.phase == "activate_trial"
    assert "selector" in str(caught.value.primary)
    assert state.trial_release_id is None
    assert tuple(state.slot_images) == (confirmed_ref.name,)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    _assert_one_closed_reset_session(state)


def test_trial_confirmation_revalidates_selector_before_promoting():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    def corrupt_selector_before_promote(event, _release_id):
        if event == "before_promote":
            state.selector = replace(
                state.selector,
                trial_generation=0,
            )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(state),
            observer=corrupt_selector_before_promote,
        )

    assert caught.value.phase == "promote"
    assert "selector" in str(caught.value.primary)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    _assert_one_closed_reset_session(state)


def test_unconsumed_trial_generation_must_match_selector_generation():
    plan = _plan("1.4.0")
    state = InMemoryReleaseState.with_confirmed(plan)
    confirmed_ref = state.selector.confirmed
    trial_ref = replace(confirmed_ref, name="B")
    state.slot_images["B"] = replace(
        state.slot_images[confirmed_ref.name],
        slot_ref=trial_ref,
    )
    state.selector = replace(
        state.selector,
        generation=state.selector.generation + 2,
        trial=trial_ref,
        trial_generation=state.selector.generation + 1,
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(plan, InMemoryReleaseAdapter(state))

    assert caught.value.phase == "resume_confirmed"
    assert "selector" in str(caught.value.primary)
    assert state.boot_release_id == plan.release_id
    _assert_one_closed_reset_session(state)


def test_staging_write_failure_keeps_the_old_release_boot_visible():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    sentinels = {
        ("sd", "settings.json"): b'{"brightness":91}\n',
        ("sd", "functions/user.py"): b"# user add-on\n",
        ("sd", "unknown.bin"): b"UNKNOWN",
    }
    state = _state_with_old_release(old_plan, sentinels.items())
    injected = OSError("injected staging write failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(("stage_write", 1, injected),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "stage"
    assert caught.value.primary is injected
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.staged_releases == {}
    assert {
        location: state.shared_files[location]
        for location in sentinels
    } == sentinels
    _assert_one_closed_reset_session(state)


def test_staging_hash_failure_never_activates_the_candidate():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    injected = ValueError("injected device SHA mismatch")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(("verify", 1, injected),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "verify"
    assert caught.value.primary is injected
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.staged_releases == {}
    _assert_one_closed_reset_session(state)


@pytest.mark.parametrize(
    "mutator",
    (
        lambda staged: replace(staged, assets=staged.assets[:-1]),
        lambda staged: replace(
            staged,
            assets=staged.assets + (
                ("managed_release", "sd", "extra.py", b"extra"),
            ),
        ),
        lambda staged: replace(
            staged,
            assets=(
                staged.assets[:-1]
                + (
                    staged.assets[-1][:-1]
                    + (staged.assets[-1][-1] + b"tampered",),
                )
            ),
        ),
        lambda staged: replace(staged, manifest_sha256="0" * 64),
    ),
    ids=("missing", "extra", "payload-hash", "manifest-hash"),
)
def test_staging_verification_rejects_any_non_exact_candidate(mutator):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    adapter = InMemoryReleaseAdapter(
        state,
        staged_mutator=mutator,
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "verify"
    assert "staged release hash verification" in str(caught.value.primary)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    assert state.staged_releases == {}
    _assert_one_closed_reset_session(state)


def test_slot_managed_path_never_overwrites_a_shared_user_file():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    user_location = ("sd", "catalog.py")
    user_bytes = b"# user-owned catalog\n"
    state = _state_with_old_release(
        old_plan,
        extra_files=((user_location, user_bytes),),
    )
    apply_release(new_plan, InMemoryReleaseAdapter(state))

    expected_catalog = next(
        asset.payload
        for asset in new_plan.assets
        if (asset.zone, asset.relative_path) == user_location
    )
    assert state.shared_files[user_location] == user_bytes
    assert state.active_slot_files()[user_location] == expected_catalog
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    _assert_one_closed_reset_session(state)


def test_trial_activation_failure_rolls_back_to_the_old_release():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    injected = RuntimeError("injected trial activation failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(("activate_trial", 1, injected),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "activate_trial"
    assert caught.value.primary is injected
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    _assert_one_closed_reset_session(state)


def test_lost_trial_selection_acknowledgement_reconciles_and_continues():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(
            (
                "activate_trial_after",
                1,
                OSError("trial selection acknowledgement lost"),
            ),
        ),
    )

    result = apply_release(new_plan, adapter)

    assert result == new_plan.release_id
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.trial_release_id is None
    assert state.selector.retired == ()
    assert state.sessions_started == 3
    _assert_one_closed_reset_session(state)


def test_retry_resumes_an_unconsumed_trial_after_the_first_reset_failed():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(("reset", 1, OSError("reset failed")),),
            ),
        )

    assert caught.value.phase == "reset"
    assert state.trial_release_id == new_plan.release_id
    assert state.selector.trial_consumed is False
    staged_write_count = state.device_write_attempts

    result = apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert result == new_plan.release_id
    assert state.device_write_attempts == staged_write_count
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.trial_release_id is None
    assert state.selector.retired == ()
    assert state.reset_attempts == state.sessions_started
    assert state.resets == state.sessions_started - 1
    assert state.sessions_closed == state.sessions_started
    assert state.session_open is False


def test_retry_rearms_a_consumed_trial_after_the_first_close_failed():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(("close", 1, OSError("close failed")),),
            ),
        )

    assert caught.value.phase == "close"
    assert state.trial_release_id == new_plan.release_id
    assert state.selector.trial_consumed is True
    assert state.boot_release_id == new_plan.release_id
    staged_write_count = state.device_write_attempts
    consumed_generation = state.selector.generation

    result = apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert result == new_plan.release_id
    assert state.device_write_attempts == staged_write_count
    assert state.selector.generation > consumed_generation
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.trial_release_id is None
    assert state.selector.retired == ()
    _assert_one_closed_reset_session(state)


def test_a_different_plan_rejects_a_durable_pending_trial_before_staging():
    old_plan = _plan("1.3.0", legacy=True)
    abandoned_plan = _plan("1.4.0")
    replacement_plan = _plan("1.5.0")
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure):
        apply_release(
            abandoned_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(("reset", 1, OSError("reset failed")),),
            ),
        )

    assert state.trial_release_id == abandoned_plan.release_id
    assert state.selector.trial_consumed is False

    result = apply_release(
        replacement_plan,
        InMemoryReleaseAdapter(state),
    )

    assert result == replacement_plan.release_id
    assert state.rollback_attempts == 1
    assert state.confirmed_release_id == replacement_plan.release_id
    assert state.boot_release_id == replacement_plan.release_id
    assert state.trial_release_id is None
    assert state.selector.retired == ()
    assert all(
        image.slot_ref.release_id != abandoned_plan.release_id
        for image in state.slot_images.values()
    )
    assert state.reset_attempts == state.sessions_started
    assert state.resets == state.sessions_started - 1
    assert state.sessions_closed == state.sessions_started
    assert state.session_open is False


def test_trial_smoke_failure_rolls_back_without_changing_boot_selection():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    injected = RuntimeError("injected cold-smoke failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(("smoke_trial", 1, injected),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "smoke_trial"
    assert caught.value.primary is injected
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    assert new_plan.release_id not in state.staged_releases
    _assert_one_closed_reset_session(state)


def test_trial_smoke_rejects_the_wrong_application_version():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    adapter = InMemoryReleaseAdapter(
        state,
        smoke_result=_smoke_result(new_plan, app_version="1.3.0"),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "smoke_trial"
    assert "application version" in str(caught.value.primary)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    _assert_one_closed_reset_session(state)


def test_trial_smoke_rejects_a_stale_release_identity():
    old_plan = _plan("1.4.0", legacy=True, mode="source")
    new_plan = _plan("1.4.0", mode="mpy")
    state = _state_with_old_release(old_plan)
    adapter = InMemoryReleaseAdapter(
        state,
        smoke_result=_smoke_result(
            new_plan,
            release_id=old_plan.release_id,
        ),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "smoke_trial"
    assert "release identity" in str(caught.value.primary)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    _assert_one_closed_reset_session(state)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"mode": "mpy"}, "build mode"),
        ({"abi_tag": "wrong-abi"}, "ABI"),
    ),
)
def test_trial_smoke_rejects_the_wrong_build_identity(changes, message):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    adapter = InMemoryReleaseAdapter(
        state,
        smoke_result=_smoke_result(new_plan, **changes),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "smoke_trial"
    assert message in str(caught.value.primary)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    _assert_one_closed_reset_session(state)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"resident_runtime": False}, "resident runtime"),
        ({"root_visible": False}, "root visible"),
    ),
)
def test_trial_smoke_requires_resident_runtime_at_the_visible_root(
        changes, message):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    adapter = InMemoryReleaseAdapter(
        state,
        smoke_result=_smoke_result(new_plan, **changes),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "smoke_trial"
    assert message in str(caught.value.primary)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    _assert_one_closed_reset_session(state)


@pytest.mark.parametrize(
    "buffers",
    (
        (),
        (("main", 8191, 1),),
        (("other", 8192, 1),),
        (("main", 8192, 0),),
        (("main", 8192, 1), ("plot", 64, 2)),
        [("main", 8192, 1)],
    ),
)
def test_trial_smoke_requires_one_valid_main_framebuffer(buffers):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    adapter = InMemoryReleaseAdapter(
        state,
        smoke_result=_smoke_result(new_plan, buffers=buffers),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "smoke_trial"
    assert "framebuffer" in str(caught.value.primary)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    _assert_one_closed_reset_session(state)


def test_confirmed_release_requires_a_second_independent_cold_smoke():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    good = _smoke_result(new_plan)
    bad = replace(good, root_visible=False)

    def smoke_oracle(_state, _selected, boot_count):
        return good if boot_count == 2 else bad

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                smoke_result=smoke_oracle,
            ),
        )

    assert caught.value.phase == "smoke_confirmed"
    assert "root visible" in str(caught.value.primary)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    assert state.selector.retired == ()
    assert all(
        image.slot_ref.release_id != new_plan.release_id
        for image in state.slot_images.values()
    )
    _assert_one_closed_reset_session(state)


def test_retry_cleans_a_failed_confirmation_after_revert_ack_was_lost():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    good = _smoke_result(new_plan)
    bad = replace(good, root_visible=False)

    def smoke_oracle(_state, _selected, boot_count):
        return good if boot_count == 2 else bad

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                smoke_result=smoke_oracle,
                failures=(
                    (
                        "rollback_confirmation_after",
                        1,
                        OSError("revert acknowledgement lost"),
                    ),
                ),
            ),
        )

    assert caught.value.phase == "smoke_confirmed"
    assert tuple(
        secondary.phase for secondary in caught.value.secondary
    ) == ("rollback_confirmation",)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert _retired_release_ids(state) == (new_plan.release_id,)
    staged_write_count = state.device_write_attempts

    result = apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert result == new_plan.release_id
    assert state.device_write_attempts > staged_write_count
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.trial_release_id is None
    assert state.selector.retired == ()
    _assert_one_closed_reset_session(state)


def test_rollback_failure_preserves_the_smoke_error_as_primary():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    smoke_error = RuntimeError("injected cold-smoke failure")
    rollback_error = OSError("injected rollback failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(
            ("smoke_trial", 1, smoke_error),
            ("rollback_trial", 1, rollback_error),
        ),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    failure = caught.value
    assert failure.phase == "smoke_trial"
    assert failure.primary is smoke_error
    assert tuple(
        (secondary.phase, secondary.error)
        for secondary in failure.secondary
    ) == (("rollback_trial", rollback_error),)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id == new_plan.release_id
    assert state.selector.trial_consumed is True
    _assert_one_closed_reset_session(state)


def test_retry_recovers_a_trial_left_by_rollback_failure():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure):
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(
                    ("smoke_trial", 1, OSError("smoke read failed")),
                    ("rollback_trial", 1, OSError("rollback failed")),
                ),
            ),
        )

    assert state.confirmed_release_id == old_plan.release_id
    assert state.trial_release_id == new_plan.release_id
    assert state.selector.trial_consumed is True
    staged_write_count = state.device_write_attempts

    result = apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert result == new_plan.release_id
    assert state.device_write_attempts == staged_write_count
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.trial_release_id is None
    assert state.selector.retired == ()
    _assert_one_closed_reset_session(state)


def test_final_reset_failure_never_overwrites_the_transaction_primary():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    smoke_error = RuntimeError("injected cold-smoke failure")
    reset_error = OSError("injected final reset failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(
            ("smoke_trial", 1, smoke_error),
            ("reset", 2, reset_error),
        ),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    failure = caught.value
    assert failure.phase == "smoke_trial"
    assert failure.primary is smoke_error
    assert tuple(
        (secondary.phase, secondary.error)
        for secondary in failure.secondary
    ) == (("reset", reset_error),)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.trial_release_id is None
    assert state.reset_attempts == 2
    assert state.resets == 1
    assert state.sessions_closed == 2
    assert state.session_open is False


def test_rollback_reset_and_close_failures_remain_ordered_secondaries():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    smoke_error = RuntimeError("injected cold-smoke failure")
    rollback_error = OSError("injected rollback failure")
    reset_error = OSError("injected reset failure")
    close_error = OSError("injected close failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(
            ("smoke_trial", 1, smoke_error),
            ("rollback_trial", 1, rollback_error),
            ("reset", 2, reset_error),
            ("close", 2, close_error),
        ),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    failure = caught.value
    assert failure.primary is smoke_error
    assert tuple(
        (secondary.phase, secondary.error)
        for secondary in failure.secondary
    ) == (
        ("rollback_trial", rollback_error),
        ("reset", reset_error),
        ("close", close_error),
    )
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.reset_attempts == 2
    assert state.close_attempts == 2
    assert state.sessions_closed == 2
    assert state.session_open is False


def test_precommit_promotion_failure_keeps_the_old_release_confirmed():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    injected = OSError("injected precommit promotion failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(("promote_before", 1, injected),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "promote"
    assert caught.value.primary is injected
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id is None
    _assert_one_closed_reset_session(state)


def test_retry_recovers_a_trial_after_precommit_reconciliation_failed():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    promotion_error = OSError("promotion failed before commit")
    reconciliation_error = OSError("selector readback failed")

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(
                    ("promote_before", 1, promotion_error),
                    ("reconcile_confirm", 1, reconciliation_error),
                ),
            ),
        )

    assert caught.value.phase == "promote"
    assert caught.value.primary is promotion_error
    assert tuple(
        (secondary.phase, secondary.error)
        for secondary in caught.value.secondary
    ) == (("reconcile_confirm", reconciliation_error),)
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    assert state.trial_release_id == new_plan.release_id
    assert state.selector.trial_consumed is True
    staged_write_count = state.device_write_attempts

    result = apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert result == new_plan.release_id
    assert state.device_write_attempts == staged_write_count
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.trial_release_id is None
    assert state.selector.retired == ()
    _assert_one_closed_reset_session(state)


def test_lost_promotion_acknowledgement_keeps_the_new_release_confirmed():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    injected = OSError("promotion committed before response was lost")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(("promote_after", 1, injected),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "promote"
    assert caught.value.primary is injected
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.trial_release_id is None
    _assert_one_closed_reset_session(state)


def test_committed_promotion_is_reconciled_without_attempting_rollback():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    acknowledgement_error = OSError("promotion acknowledgement lost")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(
            ("promote_after", 1, acknowledgement_error),
            ("rollback_trial", 1, OSError("must not be attempted")),
        ),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "promote"
    assert caught.value.primary is acknowledgement_error
    assert caught.value.secondary == ()
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert _retired_release_ids(state) == (old_plan.release_id,)
    assert state.rollback_attempts == 0
    _assert_one_closed_reset_session(state)


def test_promotion_reconciliation_failure_preserves_the_commit_primary():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    promotion_error = OSError("promotion acknowledgement lost")
    reconciliation_error = OSError("selector readback failed")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(
            ("promote_after", 1, promotion_error),
            ("reconcile_confirm", 1, reconciliation_error),
            ("rollback_trial", 1, OSError("must not be attempted")),
        ),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "promote"
    assert caught.value.primary is promotion_error
    assert tuple(
        (secondary.phase, secondary.error)
        for secondary in caught.value.secondary
    ) == (("reconcile_confirm", reconciliation_error),)
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert _retired_release_ids(state) == (old_plan.release_id,)
    assert state.rollback_attempts == 0
    _assert_one_closed_reset_session(state)


def test_successful_promotion_still_requires_selector_readback():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    readback_error = OSError("selector readback failed")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(
            ("reconcile_confirm", 1, readback_error),
            ("rollback_trial", 1, OSError("must not be attempted")),
        ),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "promote"
    assert caught.value.primary is readback_error
    assert caught.value.secondary == ()
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert _retired_release_ids(state) == (old_plan.release_id,)
    assert state.rollback_attempts == 0
    _assert_one_closed_reset_session(state)


def test_lost_promotion_acknowledgement_persists_cleanup_for_retry():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure):
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(
                    ("promote_after", 1, OSError("lost acknowledgement")),
                ),
            ),
        )

    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert _retired_release_ids(state) == (old_plan.release_id,)
    retired_ref = state.selector.retired[0]
    assert ("sd", "legacy.py") in state.slot_files(retired_ref)

    apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert ("sd", "legacy.py") not in state.active_slot_files()
    assert state.selector.retired == ()
    assert retired_ref.name not in state.slot_images
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.sessions_started == 4
    assert state.reset_attempts == 4
    assert state.sessions_closed == 4
    assert state.session_open is False


@pytest.mark.parametrize(
    ("event", "phase", "new_is_confirmed"),
    (
        ("before_promote", "observer_before_promote", False),
        ("after_promote", "observer_after_promote", True),
    ),
)
def test_observer_failure_preserves_the_commit_boundary(
        event, phase, new_is_confirmed):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    injected = RuntimeError("observer failed at " + event)
    events = []

    def observer(observed_event, release_id):
        events.append((observed_event, release_id))
        if observed_event == event:
            raise injected

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(state),
            observer=observer,
        )

    assert caught.value.phase == phase
    assert caught.value.primary is injected
    expected_release_id = (
        new_plan.release_id if new_is_confirmed else old_plan.release_id)
    assert state.confirmed_release_id == expected_release_id
    assert state.boot_release_id == expected_release_id
    assert state.trial_release_id is None
    if new_is_confirmed:
        assert _retired_release_ids(state) == (old_plan.release_id,)
    else:
        assert state.selector.retired == ()
    assert events == [
        ("before_promote", new_plan.release_id),
        *(([("after_promote", new_plan.release_id)])
           if new_is_confirmed else []),
    ]
    _assert_one_closed_reset_session(state)


def test_postcommit_cleanup_failure_keeps_the_new_release_confirmed():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    injected = OSError("injected cleanup failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(("cleanup", 1, injected),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "cleanup"
    assert caught.value.primary is injected
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert _retired_release_ids(state) == (old_plan.release_id,)
    retired_ref = state.selector.retired[0]
    assert state.slot_files(retired_ref)[
        ("sd", "legacy.py")] == b"# old managed module\n"
    _assert_one_closed_reset_session(state)


def test_reset_failure_after_success_reports_failure_but_keeps_new_release():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    injected = OSError("injected final reset failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(("reset", 3, injected),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "reset"
    assert caught.value.primary is injected
    assert caught.value.secondary == ()
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert ("sd", "legacy.py") not in state.active_slot_files()
    assert state.selector.retired == ()
    assert state.reset_attempts == 3
    assert state.resets == 2
    assert state.sessions_closed == 3
    assert state.session_open is False


def test_close_failure_after_success_reports_failure_but_keeps_new_release():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    injected = OSError("injected session close failure")
    adapter = InMemoryReleaseAdapter(
        state,
        failures=(("close", 3, injected),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == "close"
    assert caught.value.primary is injected
    assert caught.value.secondary == ()
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.reset_attempts == 3
    assert state.resets == 3
    assert state.close_attempts == 3
    assert state.sessions_closed == 3
    assert state.session_open is False


@pytest.mark.parametrize(
    ("event", "expected_phase"),
    (("stage_write", "stage"), ("smoke_trial", "smoke_trial")),
)
def test_cancellation_before_promotion_preserves_old_release(
        event, expected_phase):
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)
    cancellation = KeyboardInterrupt()
    adapter = InMemoryReleaseAdapter(
        state,
        failures=((event, 1, cancellation),),
    )

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(new_plan, adapter)

    assert caught.value.phase == expected_phase
    assert caught.value.primary is cancellation
    assert state.confirmed_release_id == old_plan.release_id
    assert state.boot_release_id == old_plan.release_id
    _assert_one_closed_reset_session(state)


def test_retrying_the_same_plan_replaces_hidden_partial_staging_safely():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    user_location = ("sd", "functions/user.py")
    user_bytes = b"# user add-on\n"
    state = _state_with_old_release(
        old_plan,
        extra_files=((user_location, user_bytes),),
    )
    first_error = OSError("injected partial staging failure")

    with pytest.raises(ReleaseFailure):
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(("stage_write", 1, first_error),),
            ),
        )

    result = apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert result == new_plan.release_id
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert state.staged_releases == {}
    assert state.shared_files[user_location] == user_bytes
    assert state.sessions_started == 4
    assert state.reset_attempts == 4
    assert state.resets == 4
    assert state.sessions_closed == 4
    assert state.session_open is False


def test_next_release_session_recovers_retired_slot_cleanup():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure):
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(("cleanup", 1, OSError("cleanup failed")),),
            ),
        )

    apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert ("sd", "legacy.py") not in state.active_slot_files()
    assert state.selector.retired == ()
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id


def test_retry_recovers_power_loss_after_retired_slot_erasure():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure) as caught:
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(
                    (
                        "cleanup_delete_after",
                        1,
                        OSError("power lost after slot erase"),
                    ),
                ),
            ),
        )

    assert caught.value.phase == "cleanup"
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id
    assert _retired_release_ids(state) == (old_plan.release_id,)
    retired_ref = state.selector.retired[0]
    assert retired_ref.name not in state.slot_images

    apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert state.selector.retired == ()
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id


def test_retired_slot_cleanup_never_deletes_a_user_replacement_path():
    old_plan = _plan("1.3.0", legacy=True)
    new_plan = _plan("1.4.0")
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure):
        apply_release(
            new_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(("cleanup", 1, OSError("cleanup failed")),),
            ),
        )

    user_location = ("sd", "legacy.py")
    user_payload = b"# user replacement outside managed slots\n"
    state.shared_files[user_location] = user_payload

    apply_release(new_plan, InMemoryReleaseAdapter(state))

    assert state.shared_files[user_location] == user_payload
    assert state.selector.retired == ()
    assert state.confirmed_release_id == new_plan.release_id
    assert state.boot_release_id == new_plan.release_id


def test_retired_slot_path_can_become_managed_again_without_deletion():
    old_plan = _plan("1.3.0", legacy=True)
    middle_plan = _plan("1.4.0")
    next_plan = _plan("1.5.0", legacy=True)
    state = _state_with_old_release(old_plan)

    with pytest.raises(ReleaseFailure):
        apply_release(
            middle_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(("cleanup", 1, OSError("cleanup failed")),),
            ),
        )

    assert _retired_release_ids(state) == (old_plan.release_id,)

    apply_release(middle_plan, InMemoryReleaseAdapter(state))

    result = apply_release(next_plan, InMemoryReleaseAdapter(state))

    expected_legacy = next(
        asset.payload
        for asset in next_plan.assets
        if (asset.zone, asset.relative_path) == ("sd", "legacy.py")
    )
    assert result == next_plan.release_id
    assert state.active_slot_files()[
        ("sd", "legacy.py")] == expected_legacy
    assert ("sd", "catalog.py") not in state.active_slot_files()
    assert state.selector.retired == ()
    assert state.confirmed_release_id == next_plan.release_id
    assert state.boot_release_id == next_plan.release_id


_POWER_CUT_EVENTS = (
    "resume_cleanup",
    "stage_write",
    "verify",
    "activate_trial",
    "activate_trial_after",
    "reconcile_trial_selection",
    "abort_staging",
    "smoke_trial",
    "smoke_confirmed",
    "promote_before",
    "promote_after",
    "reconcile_confirm",
    "rollback_trial",
    "rollback_confirmation_before",
    "rollback_confirmation_after",
    "cleanup",
    "cleanup_delete",
    "cleanup_delete_after",
    "cleanup_after",
)

_POWER_CUT_CASES = tuple(
    [(event, 1) for event in _POWER_CUT_EVENTS]
    + [("reset", occurrence) for occurrence in (1, 2, 3)]
    + [("close", occurrence) for occurrence in (1, 2, 3)]
)

_USER_SENTINELS = {
    ("sd", "settings.json"): b'{"brightness":73,"user":true}\n',
    ("sd", "vars.json"): b'{"answer":42}\n',
    ("sd", "Add-ons/user_pack.py"): b"# user add-on\n",
    ("sd", "notes/private.bin"): b"\x00USER\xff",
    ("internal", "board-calibration.bin"): b"CALIBRATION",
}


def _apply_with_power_cut_and_retry(state, plan, event, occurrence):
    cut_adapter = InMemoryReleaseAdapter(
        state,
        failures=((event, occurrence, OSError("power cut")),),
    )
    try:
        apply_release(plan, cut_adapter)
        return
    except ReleaseFailure:
        pass
    for _ in range(4):
        try:
            apply_release(plan, InMemoryReleaseAdapter(state))
            return
        except ReleaseFailure:
            continue
    raise AssertionError(
        "release never recovered after power cut at "
        + event
        + " occurrence "
        + str(occurrence))


def _assert_recovered_release(state, plan):
    assert state.confirmed_release_id == plan.release_id
    assert state.boot_release_id == plan.release_id
    assert state.selector.trial is None
    assert state.selector.retired == ()
    assert state.selector.confirmation_pending is False
    assert state.staged_releases == {}
    assert state.confirmed_manifests == {
        plan.release_id: (plan.manifest_bytes, plan.manifest_sha256),
    }
    assert {
        location: state.shared_files[location]
        for location in _USER_SENTINELS
    } == _USER_SENTINELS
    assert state.session_open is False
    assert state.reset_attempts == state.sessions_started
    assert state.close_attempts == state.sessions_started
    assert state.sessions_closed == state.sessions_started
    assert state.resets <= state.sessions_started


def _state_with_erased_retired_slot():
    old_plan = _plan("1.2.0", legacy=True)
    middle_plan = _plan("1.3.0")
    state = _state_with_old_release(
        old_plan, extra_files=_USER_SENTINELS.items())
    with pytest.raises(ReleaseFailure):
        apply_release(
            middle_plan,
            InMemoryReleaseAdapter(
                state,
                failures=(
                    (
                        "cleanup_delete_after",
                        1,
                        OSError("power lost after slot erase"),
                    ),
                ),
            ),
        )
    assert state.confirmed_release_id == middle_plan.release_id
    assert _retired_release_ids(state) == (old_plan.release_id,)
    assert state.selector.retired[0].name not in state.slot_images
    return state


@pytest.mark.parametrize("scenario", ("clean", "erased_retired"))
@pytest.mark.parametrize(("event", "occurrence"), _POWER_CUT_CASES)
def test_power_cut_at_any_point_recovers_with_the_same_plan(
        scenario, event, occurrence):
    new_plan = _plan("1.4.0")
    if scenario == "clean":
        state = _state_with_old_release(
            _plan("1.3.0", legacy=True),
            extra_files=_USER_SENTINELS.items(),
        )
    else:
        state = _state_with_erased_retired_slot()

    _apply_with_power_cut_and_retry(state, new_plan, event, occurrence)

    _assert_recovered_release(state, new_plan)
