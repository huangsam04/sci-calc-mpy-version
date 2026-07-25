# Host behaviour tests for the device boot environment adapter.
import sys

import pytest

import bootenv
import bootlog
import bootsel
import bootsupervisor


@pytest.fixture(autouse=True)
def _preserve_sys_modules():
    saved = dict(sys.modules)
    yield
    for name in list(sys.modules):
        if name not in saved:
            del sys.modules[name]
    sys.modules.update(saved)


def _ref(name, release_id, sha_byte):
    return bootsel.SlotEntry(name, release_id, bytes([sha_byte]) * 32)


def _paths(tmp_path):
    system_dir = tmp_path / "sys"
    system_dir.mkdir(exist_ok=True)
    return {
        "slot_base": str(tmp_path / "slots"),
        "selector_paths": (
            str(system_dir / "sel.0"), str(system_dir / "sel.1")),
        "bootlog_paths": (
            str(system_dir / "boot.0"), str(system_dir / "boot.1")),
    }


def test_purge_removes_slot_modules_and_keeps_the_boot_chain():
    planted = ("calc.parser", "main", "ui.menu", "display.ssd1322")
    for name in planted:
        sys.modules[name] = object()
    try:
        bootenv.purge_slot_modules()
        for name in planted:
            assert name not in sys.modules
        assert "bootsel" in sys.modules
        assert "bootsupervisor" in sys.modules
        assert "os" in sys.modules
    finally:
        for name in planted:
            sys.modules.pop(name, None)


def test_slot_probe_requires_the_release_manifest(tmp_path):
    env = bootenv.BootEnvironment(**_paths(tmp_path))
    assert not env.slot_exists("A")

    slot_root = tmp_path / "slots" / "A"
    slot_root.mkdir(parents=True)
    assert not env.slot_exists("A")
    (slot_root / "release.manifest").write_bytes(b"{}")

    assert env.slot_exists("A")
    assert env.slot_root("A") == str(tmp_path / "slots") + "/A"


def test_selector_round_trip_uses_the_dual_record_store(tmp_path):
    env = bootenv.BootEnvironment(**_paths(tmp_path))
    assert env.read_selector() is None

    stored = env.write_selector(bootsel.SelectorData(
        0, _ref("A", "app:1.4.0:source", 0x11), None, 0, False, (), False))

    assert stored.generation == 1
    assert env.read_selector() == stored


def test_boot_record_round_trip_uses_the_dual_record_store(tmp_path):
    env = bootenv.BootEnvironment(**_paths(tmp_path))

    stored = env.write_boot_record(bootlog.BootEntry(0, 1, None, None))

    assert stored.generation == 1
    paths = _paths(tmp_path)["bootlog_paths"]
    assert bootlog.BootLogStore(*paths).read() == stored


def test_set_sys_path_replaces_the_whole_list(tmp_path):
    env = bootenv.BootEnvironment(**_paths(tmp_path))
    original = sys.path[:]
    try:
        env.set_sys_path(("/sd/.slots/A", ".frozen", "/lib"))
        assert sys.path == ["/sd/.slots/A", ".frozen", "/lib"]
    finally:
        sys.path[:] = original


class _HarnessEnvironment(bootenv.BootEnvironment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exec_calls = []
        self.recovery_calls = []

    def exec_file(self, path):
        self.exec_calls.append(path)

    def show_recovery(self, error):
        self.recovery_calls.append(str(error))


def test_supervise_boots_an_armed_trial_through_the_real_chain(tmp_path):
    paths = _paths(tmp_path)
    harness = _HarnessEnvironment(**paths)
    confirmed = _ref("A", "app:1.3.0:source", 0x11)
    trial = _ref("B", "app:1.4.0:source", 0x22)
    harness.write_selector(bootsel.SelectorData(
        0, confirmed, None, 0, False, (), False))
    harness.write_selector(bootsel.SelectorData(
        0, confirmed, trial, 0, False, (), False))
    slot_root = tmp_path / "slots" / "B"
    slot_root.mkdir(parents=True)
    (slot_root / "release.manifest").write_bytes(b"{}")

    original_path = sys.path[:]
    try:
        bootsupervisor.supervise(harness)
    finally:
        sys.path[:] = original_path

    assert harness.exec_calls == [paths["slot_base"] + "/B/launch.py"]
    assert not harness.recovery_calls
    selector = harness.read_selector()
    assert selector.trial_consumed is True
    entry = bootlog.BootLogStore(*paths["bootlog_paths"]).read()
    assert entry.selected == trial
    assert entry.selection_generation == selector.trial_generation
    assert entry.selector_generation == selector.generation
