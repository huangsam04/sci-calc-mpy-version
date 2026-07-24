import sys
import types

import screens

from anim.engine import (cancel_all_animations, cancel_animations,
                         insert_animation, is_animating)
from ui.lazy_screen import (
    BUILD_STOPWATCH, DEFAULT_FUNCTION_PANEL, DEFAULT_FUNCTION_PICKER,
    DEFAULT_LIST, DEFAULT_VARIABLE_PANEL, LazyScreen, ScreenFactory)
from ui.residency import SETTLE_MORE, SETTLE_REDRAW


class DisplayStub:
    def __init__(self):
        self.rectangles = []

    def draw_text8x8(self, *args, **kwargs):
        pass

    def draw_hline(self, *args, **kwargs):
        pass

    def draw_rectangle(self, *args, **kwargs):
        self.rectangles.append(args)

    def fill_rectangle(self, *args, **kwargs):
        pass


def test_canceling_lazy_page_cancels_animation_targeting_loaded_screen():
    class RealScreen:
        value = 0

        def activate_default(self):
            pass

        def animation_children(self):
            return ()

    screen = LazyScreen(
        "page", "Page", DEFAULT_LIST, lambda: RealScreen())
    screen.settle_step()
    loaded = screen.loaded()
    insert_animation(loaded, "value", 0, 10, 100)

    try:
        cancel_animations(screen)
        assert is_animating(loaded) is False
    finally:
        cancel_all_animations()


def test_lazy_list_shells_keep_each_real_page_frame_shape():
    factory = lambda: None
    function_panel = LazyScreen(
        "panel", "Functions", DEFAULT_FUNCTION_PANEL, factory)
    function_picker = LazyScreen(
        "picker", "Functions", DEFAULT_FUNCTION_PICKER, factory)
    variable_panel = LazyScreen(
        "variables", "Variables", DEFAULT_VARIABLE_PANEL, factory)

    panel_display = DisplayStub()
    picker_display = DisplayStub()
    variable_display = DisplayStub()
    function_panel.draw_transition_default(panel_display)
    function_picker.draw_transition_default(picker_display)
    variable_panel.draw_transition_default(variable_display)

    assert panel_display.rectangles == [(0, 13, 210, 40, 15)]
    assert picker_display.rectangles == [(0, 13, 210, 40, 15)]
    assert variable_display.rectangles == [(0, 13, 210, 40, 15)]


def test_default_page_shell_does_not_construct_the_real_screen():
    created = []

    def factory():
        created.append(True)
        raise AssertionError("default shell must not allocate the page")

    screen = LazyScreen("page", "Page", DEFAULT_LIST, factory)

    screen.activate_default()
    screen.draw_transition_default(DisplayStub())
    screen.draw(DisplayStub())

    assert created == []


def test_first_settle_constructs_then_restores_the_real_screen():
    events = []

    class RealScreen:
        def activate_default(self):
            events.append("activate_default")

        def restore_state(self, state):
            events.append(("restore", state))

        def settle_step(self):
            events.append("settle")
            return 0

    screen = LazyScreen(
        "page", "Page", DEFAULT_LIST,
        lambda: events.append("construct") or RealScreen())
    state = {"cursor": 3}
    screen.restore_state(state)

    flags = screen.settle_step()

    assert flags == SETTLE_REDRAW | SETTLE_MORE
    assert events == ["construct", "activate_default", ("restore", state)]
    assert screen.settle_step() == 0
    assert events[-1] == "settle"


def test_release_destroys_the_page_and_reentry_builds_a_fresh_instance():
    instances = []

    class RealScreen:
        def __init__(self):
            self.events = []
            instances.append(self)

        def activate_default(self):
            self.events.append("activate")

        def deactivate(self):
            self.events.append("deactivate")

        def release_memory(self):
            self.events.append("release")
            return True

    screen = LazyScreen("page", "Page", DEFAULT_LIST, RealScreen)
    screen.settle_step()
    first = screen.loaded()

    assert screen.release_memory() is True
    screen.deactivate()
    assert first.events == ["activate", "deactivate", "release"]
    assert screen.loaded() is None

    screen.settle_step()
    assert screen.loaded() is not first
    assert len(instances) == 2


def test_release_returns_factory_owned_page_code():
    events = []

    class RealScreen:
        def activate_default(self):
            pass

        def deactivate(self):
            pass

        def release_memory(self):
            return False

    class Factory:
        def __call__(self, key):
            events.append(("construct", key))
            return RealScreen()

        def detach_screen(self, screen):
            events.append(("detach", screen.__class__.__name__))

        def release_screen(self, key):
            events.append(("unload", key))

    screen = LazyScreen(
        "page", "Page", DEFAULT_LIST, Factory(), 7)
    screen.settle_step()
    screen.release_memory()

    assert events == [
        ("construct", 7),
        ("detach", "RealScreen"),
        ("unload", 7),
    ]


def test_failed_page_construction_unloads_its_imported_module():
    events = []

    class Factory:
        def __call__(self, key):
            events.append(("construct", key))
            raise MemoryError("page constructor failed")

        def release_screen(self, key):
            events.append(("unload", key))

    screen = LazyScreen(
        "page", "Page", DEFAULT_LIST, Factory(), factory_key=7)

    try:
        screen.settle_step()
    except MemoryError:
        pass

    assert screen.loaded() is None
    assert events == [("construct", 7), ("unload", 7)]


def test_screen_factory_removes_both_module_registry_references():
    calculator = type("Calculator", (), {"input_box": object()})()
    factory = ScreenFactory(
        None, None, None, {}, object(), calculator, object(), object())
    missing = object()
    original_registry = sys.modules.get("screens.stopwatch", missing)
    original_attribute = getattr(screens, "stopwatch", missing)
    module = types.ModuleType("screens.stopwatch")
    sys.modules["screens.stopwatch"] = module
    screens.stopwatch = module

    try:
        factory.release_screen(BUILD_STOPWATCH)

        assert "screens.stopwatch" not in sys.modules
        assert not hasattr(screens, "stopwatch")
    finally:
        if original_registry is not missing:
            sys.modules["screens.stopwatch"] = original_registry
        if original_attribute is not missing:
            screens.stopwatch = original_attribute


def test_error_reset_releases_loaded_page_without_reconstructing_it():
    events = []

    class RealScreen:
        def activate_default(self):
            pass

        def deactivate(self):
            events.append("deactivate")

        def release_memory(self):
            events.append("release")
            return True

    screen = LazyScreen(
        "page", "Page", DEFAULT_LIST,
        lambda: events.append("construct") or RealScreen())
    screen.settle_step()

    screen.reset_state()
    screen.show_residency_error("bad swap")
    screen.draw(DisplayStub())
    assert screen.settle_step() == 0

    assert events == ["construct", "deactivate", "release"]
    assert screen.loaded() is None

    screen.clear_residency_error()
    screen.settle_step()
    assert events[-1] == "construct"
