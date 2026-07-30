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
