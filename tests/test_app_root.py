# Host behaviour tests for the application resource root resolver.
import sys
import approot


def test_slot_root_is_found_behind_the_frozen_import_path():
    original = sys.path[:]
    try:
        sys.path[:] = [".frozen", "/sd/.slots/B", "/lib"]
        assert approot.app_root() == "/sd/.slots/B"
    finally:
        sys.path[:] = original
