from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from tools.release_plan import (
    ReleaseTreeSnapshot,
    cleanup_candidates,
    plan_release,
    snapshot_release_tree,
)


def test_source_release_plan_is_deterministic_for_unordered_snapshot_input():
    files = (
        ("version.py", b'VERSION = "1.3.0"\n'),
        ("main.py", b'print("SCI-CALC")\n'),
    )

    forward = plan_release(
        ReleaseTreeSnapshot.from_files(files),
        mode="source",
    )
    reverse = plan_release(
        ReleaseTreeSnapshot.from_files(reversed(files)),
        mode="source",
    )

    assert forward == reverse
    assert tuple(asset.key for asset in forward.assets) == (
        "sd:main",
        "sd:version",
    )
    assert len(forward.release_id) == 64


def test_mpy_plan_keeps_bootstrap_launcher_and_function_packs_as_source():
    snapshot = ReleaseTreeSnapshot.from_files(
        (
            ("boot.py", b"# boot\n"),
            ("internal_main.py", b"# selector\n"),
            ("launch.py", b"import main\n"),
            ("functions/basic.py", b"# function pack\n"),
            ("main.py", b"# app\n"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        ),
        build_files=(
            ("main.mpy", b"compiled-main"),
            ("version.mpy", b"compiled-version"),
        ),
    )

    plan = plan_release(snapshot, mode="mpy")

    assert tuple(
        (asset.key, asset.kind, asset.zone, asset.relative_path)
        for asset in plan.assets
    ) == (
        ("bootstrap:boot", "source", "internal", "boot.py"),
        ("bootstrap:main", "source", "internal", "main.py"),
        ("sd:functions/basic", "source", "sd", "functions/basic.py"),
        ("sd:launch", "source", "sd", "launch.py"),
        ("sd:main", "mpy", "sd", "main.mpy"),
        ("sd:version", "mpy", "sd", "version.mpy"),
    )


def test_mpy_manifest_pins_the_device_abi_in_release_identity():
    snapshot = ReleaseTreeSnapshot.from_files(
        (("version.py", b'VERSION = "1.3.0"\n'),),
        build_files=(("version.mpy", b"compiled-version"),),
    )

    plan = plan_release(snapshot, mode="mpy")
    manifest = json.loads(plan.manifest_bytes)

    assert plan.abi_tag == "micropython-v1.29.0-preview:mpy-v6.3:xtensawin"
    assert manifest["abi_tag"] == plan.abi_tag


def test_plan_carries_the_manifest_hash_for_a_trusted_activation_record():
    plan = plan_release(
        ReleaseTreeSnapshot.from_files((
            ("version.py", b'VERSION = "1.3.0"\n'),
        )),
        mode="source",
    )

    assert plan.manifest_sha256 == hashlib.sha256(
        plan.manifest_bytes
    ).hexdigest()


def test_mpy_plan_fails_closed_when_a_selected_output_is_missing():
    snapshot = ReleaseTreeSnapshot.from_files(
        (
            ("main.py", b"# app\n"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        ),
        build_files=(("main.mpy", b"compiled-main"),),
    )

    with pytest.raises(
        ValueError,
        match=r"missing compiled runtime module: version\.mpy",
    ):
        plan_release(snapshot, mode="mpy")


def test_mpy_plan_fails_closed_when_a_selected_output_is_empty():
    snapshot = ReleaseTreeSnapshot.from_files(
        (("version.py", b'VERSION = "1.3.0"\n'),),
        build_files=(("version.mpy", b""),),
    )

    with pytest.raises(
        ValueError,
        match=r"empty compiled runtime module: version\.mpy",
    ):
        plan_release(snapshot, mode="mpy")


def test_recovery_display_source_is_planned_for_internal_and_sd_release_zones():
    snapshot = ReleaseTreeSnapshot.from_files((
        ("display/ssd1322.py", b"# display driver\n"),
        ("version.py", b'VERSION = "1.3.0"\n'),
    ))

    plan = plan_release(snapshot, mode="source")

    display_assets = tuple(
        (asset.key, asset.zone, asset.relative_path)
        for asset in plan.assets
        if asset.source_path == "display/ssd1322.py"
    )
    assert display_assets == (
        ("internal:display/ssd1322", "internal", "display/ssd1322.py"),
        ("sd:display/ssd1322", "sd", "display/ssd1322.py"),
    )


def test_font_sources_remain_host_only_and_generated_fonts_are_deployed():
    snapshot = ReleaseTreeSnapshot.from_files(
        (
            ("fonts/Bally7x9.c", b"legacy font source"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        ),
        build_files=(
            ("fonts/Bally7x9.xglcd", b"XGF1-compiled-font"),
        ),
    )

    plan = plan_release(snapshot, mode="source")

    font_assets = tuple(
        (
            asset.key,
            asset.role,
            asset.zone,
            asset.kind,
            asset.relative_path,
        )
        for asset in plan.assets
        if asset.source_path == "fonts/Bally7x9.c"
    )
    assert font_assets == (
        (
            "host:fonts/Bally7x9",
            "host_only",
            "host",
            "build_input",
            "fonts/Bally7x9.c",
        ),
        (
            "sd:fonts/Bally7x9",
            "managed_release",
            "sd",
            "font",
            "fonts/Bally7x9.xglcd",
        ),
    )


def test_settings_and_variables_are_seeded_without_becoming_managed_assets():
    snapshot = ReleaseTreeSnapshot.from_files((
        ("settings.json", b'{"brightness":50}\n'),
        ("vars.json", b"{}\n"),
        ("version.py", b'VERSION = "1.3.0"\n'),
    ))

    plan = plan_release(snapshot, mode="source")

    seeds = tuple(
        (asset.key, asset.role, asset.zone, asset.relative_path)
        for asset in plan.assets
        if asset.role == "seed_if_absent"
    )
    assert seeds == (
        ("seed:settings", "seed_if_absent", "sd", "settings.json"),
        ("seed:vars", "seed_if_absent", "sd", "vars.json"),
    )
    assert not any(
        asset.relative_path in ("settings.json", "vars.json")
        and asset.role == "managed_release"
        for asset in plan.assets
    )

    manifest = json.loads(plan.manifest_bytes)
    assert tuple(seed["key"] for seed in manifest["seeds"]) == (
        "seed:settings",
        "seed:vars",
    )
    assert all(
        asset["path"] not in ("settings.json", "vars.json")
        for asset in manifest["assets"]
    )


def test_host_only_scenario_adapter_does_not_change_device_release_identity():
    base = (("version.py", b'VERSION = "1.3.0"\n'),)
    first = plan_release(
        ReleaseTreeSnapshot.from_files(
            base + (("runtime_scenarios_host.py", b"# host adapter A\n"),)
        ),
        mode="source",
    )
    second = plan_release(
        ReleaseTreeSnapshot.from_files(
            base + (("runtime_scenarios_host.py", b"# host adapter B\n"),)
        ),
        mode="source",
    )

    host_asset = next(
        asset
        for asset in first.assets
        if asset.source_path == "runtime_scenarios_host.py"
    )
    manifest = json.loads(first.manifest_bytes)
    assert (host_asset.key, host_asset.role, host_asset.zone) == (
        "host:runtime_scenarios_host",
        "host_only",
        "host",
    )
    assert first.release_id == second.release_id
    assert all(
        asset["key"] != "host:runtime_scenarios_host"
        for asset in manifest["assets"]
    )


def test_snapshot_rejects_paths_that_escape_the_release_tree():
    with pytest.raises(ValueError, match="invalid release path"):
        ReleaseTreeSnapshot.from_files((
            ("../main.py", b"# escaped\n"),
        ))


def test_snapshot_rejects_case_folded_path_collisions():
    with pytest.raises(ValueError, match="release path collision"):
        ReleaseTreeSnapshot.from_files((
            ("Calc/Main.py", b"# first\n"),
            ("calc/main.py", b"# second\n"),
        ))


def test_plan_rejects_an_empty_generated_font():
    snapshot = ReleaseTreeSnapshot.from_files(
        (
            ("fonts/Bally7x9.c", b"legacy font source"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        ),
        build_files=(("fonts/Bally7x9.xglcd", b""),),
    )

    with pytest.raises(ValueError, match="empty generated font asset"):
        plan_release(snapshot, mode="source")


def test_plan_rejects_duplicate_logical_keys_created_by_classification():
    snapshot = ReleaseTreeSnapshot.from_files(
        (
            ("fonts/Bally7x9.c", b"legacy font source"),
            ("fonts/Bally7x9.py", b"# conflicting module\n"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        ),
        build_files=(
            ("fonts/Bally7x9.xglcd", b"XGF1-compiled-font"),
        ),
    )

    with pytest.raises(ValueError, match="release asset key collision"):
        plan_release(snapshot, mode="source")


def test_recovery_payload_stays_internal_source_in_both_build_modes():
    files = (
        ("recovery.py", b"# recovery\n"),
        ("version.py", b'VERSION = "1.3.0"\n'),
    )
    source_plan = plan_release(
        ReleaseTreeSnapshot.from_files(files),
        mode="source",
    )
    mpy_plan = plan_release(
        ReleaseTreeSnapshot.from_files(
            files,
            build_files=(("version.mpy", b"compiled-version"),),
        ),
        mode="mpy",
    )

    for plan in (source_plan, mpy_plan):
        recovery_assets = tuple(
            (asset.key, asset.kind, asset.zone, asset.relative_path)
            for asset in plan.assets
            if asset.source_path == "recovery.py"
        )
        assert recovery_assets == (
            ("internal:recovery", "source", "internal", "recovery.py"),
        )


def test_plan_retains_the_exact_immutable_bytes_selected_for_upload():
    mutable_main = bytearray(b'print("original")\n')
    snapshot = ReleaseTreeSnapshot.from_files((
        ("main.py", mutable_main),
        ("version.py", b'VERSION = "1.3.0"\n'),
    ))

    plan = plan_release(snapshot, mode="source")
    mutable_main[:] = b'print("changed!")\n'

    main_asset = next(asset for asset in plan.assets if asset.key == "sd:main")
    assert main_asset.payload == b'print("original")\n'


def test_filesystem_and_in_memory_adapters_produce_the_same_snapshot(tmp_path):
    source_root = tmp_path / "source"
    build_root = tmp_path / "build"
    source_root.mkdir()
    build_root.mkdir()
    (source_root / "main.py").write_bytes(b"# main\n")
    (source_root / "version.py").write_bytes(b'VERSION = "1.3.0"\n')
    (build_root / "main.mpy").write_bytes(b"compiled-main")

    filesystem_snapshot = snapshot_release_tree(source_root, build_root)
    memory_snapshot = ReleaseTreeSnapshot.from_files(
        (
            ("main.py", b"# main\n"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        ),
        build_files=(("main.mpy", b"compiled-main"),),
    )

    assert filesystem_snapshot == memory_snapshot


def test_filesystem_snapshot_ignores_generated_python_cache_files(tmp_path):
    source_root = tmp_path / "source"
    cache_root = source_root / "__pycache__"
    cache_root.mkdir(parents=True)
    (source_root / "version.py").write_bytes(b'VERSION = "1.3.0"\n')
    (cache_root / "version.cpython-312.pyc").write_bytes(b"stale cache")

    snapshot = snapshot_release_tree(source_root, tmp_path / "missing-build")

    assert tuple(path for path, _content in snapshot.source_files) == (
        "version.py",
    )


def test_current_source_tree_has_one_explicit_release_classification(tmp_path):
    build_root = tmp_path / "build"
    fonts_root = build_root / "fonts"
    fonts_root.mkdir(parents=True)
    for name in ("Bally7x9", "FixedFont5x8", "Neato5x7"):
        (fonts_root / (name + ".xglcd")).write_bytes(
            b"generated-" + name.encode("ascii"))

    project = Path(__file__).parents[1]
    plan = plan_release(
        snapshot_release_tree(project / "source", build_root),
        mode="source",
    )

    assert len(plan.assets) == 64
    assert Counter((asset.zone, asset.role) for asset in plan.assets) == {
        ("internal", "bootstrap_fixed"): 3,
        ("internal", "managed_release"): 3,
        ("sd", "managed_release"): 52,
        ("sd", "seed_if_absent"): 2,
        ("host", "host_only"): 4,
    }
    assert Counter(
        asset.kind for asset in plan.assets if asset.role != "host_only"
    ) == {
        "source": 55,
        "font": 3,
        "seed": 2,
    }


def test_current_mpy_plan_selects_exactly_one_format_per_device_module(
        tmp_path):
    project = Path(__file__).parents[1]
    source_root = project / "source"
    build_root = tmp_path / "build"
    fonts_root = build_root / "fonts"
    fonts_root.mkdir(parents=True)
    for name in ("Bally7x9", "FixedFont5x8", "Neato5x7"):
        (fonts_root / (name + ".xglcd")).write_bytes(
            b"generated-" + name.encode("ascii"))

    always_source = {
        "boot.py",
        "internal_main.py",
        "launch.py",
        "recovery.py",
        "sdcard.py",
        "runtime_scenarios_host.py",
    }
    for source_path in source_root.rglob("*.py"):
        relative = source_path.relative_to(source_root).as_posix()
        if relative in always_source or relative.startswith("functions/"):
            continue
        output = build_root / Path(relative).with_suffix(".mpy")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"compiled-" + relative.encode("ascii"))

    plan = plan_release(
        snapshot_release_tree(source_root, build_root),
        mode="mpy",
    )

    assert Counter(
        asset.kind for asset in plan.assets if asset.role != "host_only"
    ) == {
        "source": 11,
        "mpy": 44,
        "font": 3,
        "seed": 2,
    }
    sd_modules = [
        asset.relative_path.rsplit(".", 1)[0]
        for asset in plan.assets
        if asset.zone == "sd"
        and asset.kind in ("source", "mpy")
        and asset.role == "managed_release"
    ]
    assert len(sd_modules) == len(set(sd_modules)) == 49


def test_cleanup_uses_the_previous_owned_manifest_not_a_remote_listing():
    previous = plan_release(
        ReleaseTreeSnapshot.from_files((
            ("legacy.py", b"# old managed asset\n"),
            ("main.py", b"# current asset\n"),
            ("settings.json", b'{"brightness":50}\n'),
            ("version.py", b'VERSION = "1.3.0"\n'),
        )),
        mode="source",
    )
    current = plan_release(
        ReleaseTreeSnapshot.from_files((
            ("main.py", b"# current asset\n"),
            ("settings.json", b'{"brightness":60}\n'),
            ("version.py", b'VERSION = "1.3.0"\n'),
        )),
        mode="source",
    )

    assert cleanup_candidates(
        previous.manifest_bytes,
        previous.manifest_sha256,
        current,
    ) == (
        ("sd", "legacy.py"),
    )


def test_cleanup_rejects_a_manifest_not_named_by_the_trusted_activation_hash():
    previous = plan_release(
        ReleaseTreeSnapshot.from_files((
            ("legacy.py", b"# old managed asset\n"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        )),
        mode="source",
    )
    current = plan_release(
        ReleaseTreeSnapshot.from_files((
            ("version.py", b'VERSION = "1.3.0"\n'),
        )),
        mode="source",
    )

    with pytest.raises(ValueError, match="trusted release manifest hash"):
        cleanup_candidates(previous.manifest_bytes, "0" * 64, current)


def test_cleanup_rejects_a_manifest_that_claims_user_settings():
    previous = plan_release(
        ReleaseTreeSnapshot.from_files((
            ("legacy.py", b"# old managed asset\n"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        )),
        mode="source",
    )
    hostile = json.loads(previous.manifest_bytes)
    legacy = next(
        asset for asset in hostile["assets"] if asset["key"] == "sd:legacy")
    legacy["path"] = "SETTINGS.JSON"
    hostile["seeds"] = []
    payload = dict(hostile)
    del payload["release_id"]
    hostile["release_id"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    hostile_bytes = json.dumps(
        hostile,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")

    current = plan_release(
        ReleaseTreeSnapshot.from_files((
            ("version.py", b'VERSION = "1.3.0"\n'),
        )),
        mode="source",
    )
    with pytest.raises(ValueError, match="protected user path"):
        cleanup_candidates(
            hostile_bytes,
            hashlib.sha256(hostile_bytes).hexdigest(),
            current,
        )


def test_mode_switch_cleanup_removes_old_module_extensions_by_path():
    files = (
        ("main.py", b"# app\n"),
        ("version.py", b'VERSION = "1.3.0"\n'),
    )
    source_plan = plan_release(
        ReleaseTreeSnapshot.from_files(files),
        mode="source",
    )
    mpy_plan = plan_release(
        ReleaseTreeSnapshot.from_files(
            files,
            build_files=(
                ("main.mpy", b"compiled-main"),
                ("version.mpy", b"compiled-version"),
            ),
        ),
        mode="mpy",
    )

    assert cleanup_candidates(
        source_plan.manifest_bytes,
        source_plan.manifest_sha256,
        mpy_plan,
    ) == (
        ("sd", "main.py"),
        ("sd", "version.py"),
    )


def test_cleanup_never_deletes_a_case_folded_current_owned_path():
    previous = plan_release(
        ReleaseTreeSnapshot.from_files((
            ("Foo.py", b"# old spelling\n"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        )),
        mode="source",
    )
    current = plan_release(
        ReleaseTreeSnapshot.from_files((
            ("foo.py", b"# new spelling\n"),
            ("version.py", b'VERSION = "1.3.0"\n'),
        )),
        mode="source",
    )

    assert cleanup_candidates(
        previous.manifest_bytes,
        previous.manifest_sha256,
        current,
    ) == ()
