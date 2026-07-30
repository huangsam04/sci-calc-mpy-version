import hashlib
import json
from pathlib import Path

import pytest

from calc import loader, plugin_reload
from tools.release_plan import (
    MPY_MODE,
    SOURCE_MODE,
    ReleaseTreeSnapshot,
    plan_release,
    validate_release_plan,
)


SOURCE = Path(__file__).parents[1] / "source"
FIXTURE_DIR = SOURCE / "functions"
FIXTURE_FILES = (
    "_acceptance_core.py",
    "_acceptance_dependent.py",
    "_acceptance_missing.py",
)


def _fixture_snapshot():
    files = [("version.py", (SOURCE / "version.py").read_bytes())]
    for filename in FIXTURE_FILES:
        files.append(("functions/" + filename,
                      (FIXTURE_DIR / filename).read_bytes()))
    return ReleaseTreeSnapshot.from_files(
        files, build_files=(("version.mpy", b"fixture-version-mpy"),))


def _fixture_records(plan):
    manifest = json.loads(plan.manifest_bytes)
    return tuple(
        record for record in manifest["assets"]
        if record["path"].startswith("functions/_acceptance_")
    )


def _manifest_snapshot(plan):
    return tuple(
        record["path"].rsplit("/", 1)[1]
        for record in _fixture_records(plan)
    )


def _finish(environment):
    while not environment.complete:
        environment.step()


@pytest.mark.parametrize("mode", (SOURCE_MODE, MPY_MODE))
def test_acceptance_fixture_pack_is_a_hash_verified_source_slot_asset(mode):
    plan = plan_release(_fixture_snapshot(), mode=mode)

    validate_release_plan(plan)
    assert plan.manifest_sha256 == hashlib.sha256(
        plan.manifest_bytes).hexdigest()
    assert _manifest_snapshot(plan) == FIXTURE_FILES

    records = _fixture_records(plan)
    assert len(records) == len(FIXTURE_FILES)
    for record in records:
        filename = record["path"].rsplit("/", 1)[1]
        payload = (FIXTURE_DIR / filename).read_bytes()
        assert record["key"] == "sd:functions/" + filename[:-3]
        assert record["format"] == "source"
        assert record["role"] == "managed_release"
        assert record["zone"] == "sd"
        assert record["size"] == len(payload)
        assert record["sha256"] == hashlib.sha256(payload).hexdigest()


def test_acceptance_fixture_pack_is_hidden_from_normal_scan_but_loadable_explicitly(
        monkeypatch):
    plan = plan_release(_fixture_snapshot(), mode=MPY_MODE)
    files = _manifest_snapshot(plan)

    assert not set(FIXTURE_FILES).intersection(
        loader._plugin_files(str(FIXTURE_DIR)))

    calls = []
    real_execute = loader._execute_plugin

    def record_execute(path, module_name):
        calls.append(Path(path).name)
        return real_execute(path, module_name)

    monkeypatch.setattr(plugin_reload, "_execute_plugin", record_execute)
    environment = plugin_reload.FunctionEnvironment(
        str(FIXTURE_DIR), files=files,
        selected_files=("_acceptance_dependent",))
    _finish(environment)

    assert calls == ["_acceptance_dependent.py", "_acceptance_core.py"]
    assert set(environment.report.functions) == {
        "_acceptance_core", "_acceptance_dependent"}
    assert environment.report.errors == []

    calls[:] = []
    missing = plugin_reload.FunctionEnvironment(
        str(FIXTURE_DIR), files=files,
        selected_files=("_acceptance_missing",))
    _finish(missing)

    assert calls == ["_acceptance_missing.py"]
    assert dict(missing.report.errors)["_acceptance_missing"] == (
        "Dependency failed: _acceptance_absent")
