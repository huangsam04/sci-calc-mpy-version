# Host behaviour tests for the trusted first-takeover adoption flow.
import hashlib

import pytest

from tools import release_adoption
from tools.release_plan import ReleaseTreeSnapshot, plan_release


BASELINE_130 = {
    "boot.py": b"# 1.3.0 boot\n",
    "main.py": b"# 1.3.0 internal main\n",
    "sdcard.py": b"# 1.3.0 sdcard driver\n",
    "recovery.py": b"# 1.3.0 recovery\n",
    "display/mono_palette.py": b"# 1.3.0 palette\n",
    "display/ssd1322.py": b"# 1.3.0 display driver\n",
}


def _baseline_hashes():
    return {
        path: hashlib.sha256(payload).hexdigest()
        for path, payload in BASELINE_130.items()
    }


def _new_plan():
    files = [
        ("boot.py", b"# new boot chain\n"),
        ("internal_main.py", b"# new supervisor shim\n"),
        ("sdcard.py", BASELINE_130["sdcard.py"]),
        ("bootsel.py", b"# codec\n"),
        ("bootlog.py", b"# boot log\n"),
        ("bootsupervisor.py", b"# supervisor\n"),
        ("bootenv.py", b"# environment\n"),
        ("recovery.py", BASELINE_130["recovery.py"]),
        ("display/mono_palette.py", BASELINE_130["display/mono_palette.py"]),
        ("display/ssd1322.py", BASELINE_130["display/ssd1322.py"]),
        ("main.py", b"# app main\n"),
        ("version.py", b'VERSION = "1.4.0"\n'),
        ("settings.json", b"{}\n"),
        ("vars.json", b"{}\n"),
    ]
    return plan_release(
        ReleaseTreeSnapshot.from_files(files), mode="source")


class _AdoptionTwin:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.write_log = []
        self.exec_calls = []
        self.fail_on_write = None

    def read_file(self, path):
        return self.files.get(path)

    def write_file(self, path, data):
        if (self.fail_on_write is not None
                and path.startswith(self.fail_on_write)):
            raise OSError("flash write failed")
        self.files[path] = bytes(data)
        self.write_log.append(path)

    def exists(self, path):
        return path in self.files

    def exec(self, code, **params):
        self.exec_calls.append(code)
        return "OK"

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)
        self.write_log.append(dst)


def _baseline_device():
    return _AdoptionTwin(
        {"/" + path: payload for path, payload in BASELINE_130.items()})


def test_adoption_installs_the_new_anchor_in_a_safe_order():
    twin = _baseline_device()
    plan = _new_plan()

    release_adoption.adopt_device(
        twin, plan, baseline_hashes=_baseline_hashes())

    assert twin.files["/boot.py"] == b"# new boot chain\n"
    assert twin.files["/main.py"] == b"# new supervisor shim\n"
    assert twin.files["/bootsel.py"] == b"# codec\n"
    assert twin.files["/bootenv.py"] == b"# environment\n"
    main_index = twin.write_log.index("/main.py")
    assert main_index == len(twin.write_log) - 1
    assert twin.write_log.index("/boot.py") < twin.write_log.index(
        "/main.py.new") < main_index
    assert any("mkdir" in code for code in twin.exec_calls)


def test_adoption_refuses_a_tampered_baseline_without_writing():
    twin = _baseline_device()
    twin.files["/recovery.py"] = b"# tampered\n"
    plan = _new_plan()

    with pytest.raises(release_adoption.AdoptionError, match="baseline"):
        release_adoption.adopt_device(
            twin, plan, baseline_hashes=_baseline_hashes())

    assert not twin.write_log
    assert twin.files["/boot.py"] == BASELINE_130["boot.py"]


def test_adoption_refuses_an_unknown_preexisting_boot_module():
    twin = _baseline_device()
    twin.files["/bootsel.py"] = b"# foreign content\n"
    plan = _new_plan()

    with pytest.raises(release_adoption.AdoptionError, match="conflict"):
        release_adoption.adopt_device(
            twin, plan, baseline_hashes=_baseline_hashes())

    assert twin.files["/bootsel.py"] == b"# foreign content\n"


def test_adoption_is_idempotent_when_already_applied():
    plan = _new_plan()
    first = _baseline_device()
    release_adoption.adopt_device(
        first, plan, baseline_hashes=_baseline_hashes())
    adopted_files = dict(first.files)

    second = _AdoptionTwin(adopted_files)
    release_adoption.adopt_device(
        second, plan, baseline_hashes=_baseline_hashes())

    assert second.files == adopted_files
    assert not second.write_log


def test_adoption_writes_main_last_even_when_an_early_write_fails():
    twin = _baseline_device()
    twin.fail_on_write = "/bootsel.py"
    plan = _new_plan()

    with pytest.raises(OSError):
        release_adoption.adopt_device(
            twin, plan, baseline_hashes=_baseline_hashes())

    assert twin.files["/boot.py"] == BASELINE_130["boot.py"]
    assert twin.files["/main.py"] == BASELINE_130["main.py"]


def test_adoption_uses_the_pinned_com6_baseline_by_default():
    expected = {
        "boot.py": "e918c98fdf02faf11d6af325eec8c42399a070281a9988a9672b9774665b5af4",
        "main.py": "44fe63c3a3f98c1a8f2779addc11df4c6a31dd54d422e6828bf2e211e5d61ab0",
        "sdcard.py": "d2d4b98ed0d466c49a6c121c90a313d782defb59483a931b1ce1aae7904b60ea",
        "recovery.py": "46feef5addb3039b94b765d4b01291c8e5a442c0adda6b53f48ee5992d180cd1",
        "display/mono_palette.py": "5f9804a6dc8be3451e5e9ab6d3a35ef0a29889582b079a8cd5b796ba73f219a7",
        "display/ssd1322.py": "4065905c2d3c6f77709606c4c09a15ef20964022d7756f55fa6e948cfdfb86e6",
    }
    assert release_adoption.BASELINE_130_HASHES == expected
