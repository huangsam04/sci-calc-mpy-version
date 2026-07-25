# Device boot environment adapter for the boot supervisor.
# Wires the real filesystem, selector store, boot log, sys.path, module
# purge, GC, execfile and recovery display into one environment object.
# Only gc/os/sys plus the boot codecs are imported, so the module also
# loads on CPython for host behaviour tests.
import gc
import os
import sys

import bootlog
import bootsel

_SLOT_BASE = "/sd/.slots"
_MANIFEST_NAME = "release.manifest"
_SELECTOR_PATHS = ("/sys/sel.0", "/sys/sel.1")
_BOOTLOG_PATHS = ("/sys/boot.0", "/sys/boot.1")

SLOT_BASE = _SLOT_BASE
MANIFEST_NAME = _MANIFEST_NAME
SELECTOR_PATHS = _SELECTOR_PATHS
BOOTLOG_PATHS = _BOOTLOG_PATHS

_SLOT_PACKAGES = (
    "anim", "approot", "benchmarks", "calc", "diagnostics", "display",
    "functions", "input", "launch", "main", "performance",
    "runtime_acceptance", "runtime_handle", "runtime_scenarios",
    "runtime_scenarios_host", "screens", "ui", "utils", "version",
)


def _is_slot_module(name):
    for package in _SLOT_PACKAGES:
        if name == package or name.startswith(package + "."):
            return True
    return False


def purge_slot_modules():
    for name in list(sys.modules):
        if _is_slot_module(name):
            del sys.modules[name]


class BootEnvironment:
    def __init__(self, slot_base=_SLOT_BASE,
                 selector_paths=_SELECTOR_PATHS,
                 bootlog_paths=_BOOTLOG_PATHS,
                 manifest_name=_MANIFEST_NAME):
        self._slot_base = slot_base
        self._manifest_name = manifest_name
        self._selector_store = bootsel.SelectorStore(*selector_paths)
        self._bootlog_store = bootlog.BootLogStore(*bootlog_paths)

    def read_selector(self):
        return self._selector_store.read()

    def write_selector(self, selector):
        return self._selector_store.write(selector)

    def write_boot_record(self, entry):
        return self._bootlog_store.write(entry)

    def slot_root(self, name):
        return self._slot_base + "/" + name

    def slot_exists(self, name):
        try:
            os.stat(self.slot_root(name) + "/" + self._manifest_name)
            return True
        except OSError:
            return False

    def set_sys_path(self, entries):
        sys.path = list(entries)

    def purge_slot_modules(self):
        purge_slot_modules()

    def collect_garbage(self):
        gc.collect()

    def exec_file(self, path):
        execfile(path)

    def show_recovery(self, error):
        try:
            from recovery import show_recovery
            show_recovery(error)
        except Exception as recovery_error:
            print("SCI-CALC recovery failed: " + str(recovery_error))


def environment(**kwargs):
    return BootEnvironment(**kwargs)
