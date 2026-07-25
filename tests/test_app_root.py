# Host behaviour tests for the application resource root resolver.
import sys
import approot


def test_slot_root_first_on_sys_path_wins():
    original = sys.path[:]
    try:
        sys.path[:] = ["/sd/.slots/B", ".frozen", "/lib"]
        assert approot.app_root() == "/sd/.slots/B"
    finally:
        sys.path[:] = original


def test_flat_sd_deployment_resolves_to_sd():
    original = sys.path[:]
    try:
        sys.path[:] = ["/sd", "", "/lib"]
        assert approot.app_root() == "/sd"
    finally:
        sys.path[:] = original


def test_empty_cwd_entry_falls_back_to_sd():
    original = sys.path[:]
    try:
        sys.path[:] = ["", ".frozen", "/lib"]
        assert approot.app_root() == "/sd"
    finally:
        sys.path[:] = original


def test_missing_sys_path_falls_back_to_sd():
    original = sys.path[:]
    try:
        sys.path[:] = []
        assert approot.app_root() == "/sd"
    finally:
        sys.path[:] = original
