import hashlib

from tools.release_apply import apply_release
from tools.release_device_memory import (
    InMemoryReleaseAdapter,
    InMemoryReleaseState,
)
from tools.release_plan import ReleaseTreeSnapshot, plan_release


def _plan(version, legacy=False):
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
    return plan_release(ReleaseTreeSnapshot.from_files(files), mode="source")


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


def _state_with_old_release(old_plan, extra_files=(), missing_paths=()):
    files = _device_files(old_plan)
    files.update(extra_files)
    for location in missing_paths:
        files.pop(location, None)
    return InMemoryReleaseState(
        files=files,
        confirmed_manifests={
            old_plan.release_id: (
                old_plan.manifest_bytes,
                hashlib.sha256(old_plan.manifest_bytes).hexdigest(),
            ),
        },
        confirmed_release_id=old_plan.release_id,
        boot_release_id=old_plan.release_id,
    )


def _assert_one_closed_reset_session(state):
    assert state.sessions_started == 1
    assert state.resets == 1
    assert state.sessions_closed == 1
    assert state.session_open is False


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
    assert ("sd", "legacy.py") not in state.files
    for asset in new_plan.assets:
        if asset.role in ("bootstrap_fixed", "managed_release"):
            assert state.files[(asset.zone, asset.relative_path)] == asset.payload
    assert {
        location: state.files[location]
        for location in sentinels
    } == sentinels
    _assert_one_closed_reset_session(state)


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
        location: seed_state.files[location]
        for location in expected_seeds
    } == expected_seeds
    _assert_one_closed_reset_session(seed_state)
