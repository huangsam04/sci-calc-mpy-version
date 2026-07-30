import sys

import pytest
import ui.renderer
import ui.sidebar

from calc.functions import build_registry
from main import (PAGE_ABOUT, PAGE_CALCULATOR, PAGE_FUNCTION_PANEL,
                  PAGE_FUNCTION_PICKER, PAGE_LETTERS, PAGE_PLOT,
                  PAGE_SETTINGS, PAGE_STOPWATCH, PAGE_VARIABLE_PANEL, Nav,
                  _drain_input_batch, _navigate_registered_page,
                  _present_first_ui_frame)
from screens.calculator import CalculatorScreen
from screens.function_panel import FunctionPanel
from screens.main_menu import MainMenu


class RendererStub:
    def __init__(self, display, sidebar, memory=None):
        self.display = display
        self.presented = []
        self.last_present_us = 7
        self.invalidated = False
        self.commit = True
        self.error = None

    def present(self, screen):
        if self.error is not None:
            raise self.error
        self.presented.append(screen)
        return self.commit

    def invalidate(self):
        self.invalidated = True


class MemoryStub:
    def __init__(self):
        self.plot_releases = 0
        self.collections = 0

    def release_plot_workspace(self):
        self.plot_releases += 1
        return True

    def collect(self):
        self.collections += 1


class ScreenStub:
    def __init__(self, releases=False, plot=False):
        self.calls = []
        self.releases = releases
        self.requires_plot_workspace = plot

    def activate(self):
        self.calls.append("activate")

    def deactivate(self):
        self.calls.append("deactivate")

    def release_memory(self):
        self.calls.append("release_memory")
        return self.releases


class KeyboardStub:
    def __init__(self, events=(), pressed=False):
        self.events = list(events)
        self.pressed = pressed

    def pop_key_event(self):
        return self.events.pop(0) if self.events else None

    def any_pressed(self):
        return self.pressed


class BrightnessDisplay:
    def __init__(self, brightness=80):
        self.brightness = brightness
        self.currents = []
        self.restored = []

    def set_transition_current(self, level):
        self.currents.append(level)

    def set_brightness(self, percent):
        self.brightness = percent
        self.restored.append(percent)


class LifecycleMemory:
    def __init__(self, events):
        self.events = events

    def release_plot_workspace(self):
        self.events.append("workspace")
        return True

    def collect(self):
        self.events.append("collect")


class LifecycleScreen:
    def __init__(self, name, events, nav_getter):
        self.name = name
        self.events = events
        self.nav_getter = nav_getter

    def activate(self):
        self.events.append(
            self.name + ".activate:locked="
            + str(self.nav_getter()._input_locked))

    def deactivate(self):
        self.events.append(self.name + ".deactivate")

    def release_memory(self):
        self.events.append(self.name + ".release_memory")
        return True


def _nav(monkeypatch, memory=None, display=None, page_builder=None):
    monkeypatch.setattr(ui.renderer, "Renderer", RendererStub)
    monkeypatch.setattr(ui.sidebar, "Sidebar", lambda font, registry: object())
    return Nav(
        display, None, object(), memory=memory,
        page_builder=page_builder)


def test_navigation_exposes_followup_input_immediately_without_page_motion(
        monkeypatch):
    nav = _nav(monkeypatch)
    first = ScreenStub()
    second = ScreenStub()
    keyboard = KeyboardStub([(3, 0, False), (3, 1, False)])
    nav.boot(first)

    nav.go_to(second, (3, 3, False))

    assert nav.current is second
    assert nav.poll_event(keyboard) == (3, 0, False)
    assert nav.poll_event(keyboard) == (3, 1, False)
    assert not hasattr(nav, "is_transitioning")


def test_main_menu_routes_page_ids_through_nav_owned_lazy_construction(
        monkeypatch):
    built = []
    pages = {
        PAGE_CALCULATOR: ScreenStub(),
        PAGE_FUNCTION_PANEL: ScreenStub(),
    }

    def build(page_id, _parent):
        built.append(page_id)
        return pages[page_id]

    nav = _nav(monkeypatch, page_builder=build)
    root = MainMenu()
    root.add_screen("Calculator", PAGE_CALCULATOR)
    root.add_screen("Function Panel", PAGE_FUNCTION_PANEL)
    nav.boot(root)

    result = root.update(None, (3, 3, False))
    assert _navigate_registered_page(
        nav, root, result, (3, 3, False)) is True
    assert result == PAGE_CALCULATOR
    assert nav.current is pages[PAGE_CALCULATOR]
    assert built == [PAGE_CALCULATOR]

    nav.back()

    assert root.update(None, (3, 1, False)) == "REDRAW"
    result = root.update(None, (3, 3, False))
    assert _navigate_registered_page(
        nav, root, result, (3, 3, False)) is True
    assert result == PAGE_FUNCTION_PANEL
    assert nav.current is pages[PAGE_FUNCTION_PANEL]
    assert built == [PAGE_CALCULATOR, PAGE_FUNCTION_PANEL]
    assert not hasattr(nav, "_managed")


def test_lazy_page_construction_oom_keeps_active_root(monkeypatch):
    def exhaust(_page_id, _parent):
        raise MemoryError("page")

    nav = _nav(monkeypatch, page_builder=exhaust)
    root = ScreenStub()
    nav.boot(root)

    try:
        nav.open(PAGE_CALCULATOR)
    except MemoryError as error:
        assert str(error) == "page"
    else:
        raise AssertionError("page construction should fail")

    assert nav.current is root
    assert nav.stack == [root]
    assert root.calls == ["activate"]


def test_lazy_page_activation_oom_releases_candidate_and_restores_root(
        monkeypatch):
    primary = MemoryError("activate")

    class FailingScreen(ScreenStub):
        def activate(self):
            self.calls.append("activate")
            raise primary

    candidate = FailingScreen(releases=True)
    nav = _nav(
        monkeypatch,
        page_builder=lambda _page_id, _parent: candidate)
    root = ScreenStub()
    nav.boot(root)

    with pytest.raises(MemoryError) as caught:
        nav.open(PAGE_CALCULATOR)

    assert caught.value is primary
    assert nav.current is root
    assert nav.stack == [root]
    assert root.calls == [
        "activate", "deactivate", "release_memory", "activate"]
    assert candidate.calls == ["activate", "release_memory"]


def test_nav_rebuilds_calculator_and_keeps_only_its_lossless_state(
        monkeypatch):
    class Persistence:
        def request_settings(self, *_args):
            pass

        def detach_callbacks(self, _owner):
            pass

    registry = build_registry()
    nav = _nav(monkeypatch)
    nav.configure_pages(
        None, None, registry, {},
        {"display_digits": 6, "enabled_functions": ["basic"]},
        Persistence(), "1.4.0")
    root = MainMenu()
    nav.boot(root)

    first = nav.open(PAGE_CALCULATOR)
    first.input_box.set_str("2+3")
    history = first._state[0]
    history.append(("2+3", "5"))
    input_box = first.input_box
    nav.back()

    assert nav.current is root
    assert first not in nav.stack
    assert nav.find_page(PAGE_CALCULATOR) is None
    assert "screens.calculator" in sys.modules
    assert "calc.parser" in sys.modules
    assert "ui.error_popup" in sys.modules

    second = nav.open(PAGE_CALCULATOR)

    assert second is not first
    assert second.input_box is input_box
    assert second._state[0] is history
    assert second.input_box.get_str() == "2+3"


def test_nav_first_and_repeat_entry_covers_every_supported_page(monkeypatch):
    class Persistence:
        def request_settings(self, *_args):
            pass

        def detach_callbacks(self, _owner):
            pass

    nav = _nav(monkeypatch)
    nav.configure_pages(
        None, None, build_registry(), {},
        {"display_digits": 4, "enabled_functions": ["basic"]},
        Persistence(), "1.4.0")
    root = MainMenu()
    nav.boot(root)
    main_pages = (
        (PAGE_CALCULATOR, "screens.calculator"),
        (PAGE_PLOT, "screens.plot"),
        (PAGE_FUNCTION_PANEL, "screens.function_panel"),
        (PAGE_STOPWATCH, "screens.stopwatch"),
        (PAGE_SETTINGS, "screens.settings"),
    )

    for page_id, module_name in main_pages:
        first = nav.open(page_id)
        assert nav.current is first
        nav.back()
        assert nav.current is root
        assert nav.find_page(page_id) is None
        assert module_name in sys.modules
        second = nav.open(page_id)
        assert second is not first
        nav.back()

    calculator = nav.open(PAGE_CALCULATOR)
    for page_id, module_name in (
            (PAGE_LETTERS, "screens.letter_panel"),
            (PAGE_FUNCTION_PICKER, "screens.function_picker"),
            (PAGE_VARIABLE_PANEL, "screens.variable_panel")):
        child = nav.open(page_id)
        assert nav.current is child
        nav.back()
        assert nav.current is calculator
        assert nav.find_page(page_id) is None
        assert module_name in sys.modules
    nav.back()

    settings = nav.open(PAGE_SETTINGS)
    about = nav.open(PAGE_ABOUT)
    assert nav.current is about
    nav.back()
    assert nav.current is settings
    assert nav.find_page(PAGE_ABOUT) is None
    assert "screens.about" in sys.modules
    nav.back()


def test_navigation_commits_target_without_hardware_brightness_fade(
        monkeypatch):
    display = BrightnessDisplay(brightness=80)
    nav = _nav(monkeypatch, display=display)
    root = ScreenStub()
    child = ScreenStub()
    nav.boot(root)

    nav.go_to(child)

    assert display.currents == []
    assert display.restored == []
    assert nav.renderer.presented == []
    assert not hasattr(nav, "motion_active")
    assert not hasattr(nav, "advance_motion")
    assert nav.present_current() is True
    assert nav.renderer.presented == [child]


def test_input_batch_dispatches_five_edges_before_render_boundary(monkeypatch):
    nav = _nav(monkeypatch)
    nav.boot(ScreenStub())
    events = [(3, col % 3, False) for col in range(6)]
    keyboard = KeyboardStub(events)
    handled = []

    assert _drain_input_batch(nav, keyboard, handled.append) == 5
    assert handled == events[:5]
    assert nav.poll_event(keyboard) == events[5]


def test_plot_exit_releases_workspace_and_defers_collection(monkeypatch):
    memory = MemoryStub()
    nav = _nav(monkeypatch, memory)
    root = ScreenStub()
    heavy = ScreenStub(releases=True, plot=True)
    nav.boot(root)
    nav.go_to(heavy)

    nav.go_back()

    assert nav.current is root
    assert heavy.calls[-2:] == ["deactivate", "release_memory"]
    assert memory.plot_releases == 1
    assert memory.collections == 0
    assert nav.collect_pending()
    assert memory.collections == 1


def test_navigation_releases_rebuildable_state_on_every_ordinary_leave(
        monkeypatch):
    nav = _nav(monkeypatch)
    root = ScreenStub()
    child = ScreenStub()
    nav.boot(root)

    nav.go_to(child)
    nav.go_back()

    assert root.calls == [
        "activate", "deactivate", "release_memory", "activate"]
    assert child.calls == ["activate", "deactivate", "release_memory"]


def test_go_to_release_oom_keeps_the_current_page_and_allows_retry(
        monkeypatch):
    nav = _nav(monkeypatch)
    root = ScreenStub()
    child = ScreenStub()
    failure = MemoryError("injected departure release OOM")
    attempts = [0]
    normal_release = root.release_memory

    def fail_once():
        attempts[0] += 1
        if attempts[0] == 1:
            raise failure
        return normal_release()

    root.release_memory = fail_once
    nav.boot(root)

    with pytest.raises(MemoryError) as caught:
        nav.go_to(child)

    assert caught.value is failure
    assert nav.current is root
    nav.go_to(child)
    assert nav.current is child


def test_go_back_release_oom_keeps_the_child_page_and_allows_retry(
        monkeypatch):
    nav = _nav(monkeypatch)
    root = ScreenStub()
    child = ScreenStub()
    failure = MemoryError("injected return release OOM")
    attempts = [0]
    normal_release = child.release_memory

    def fail_once():
        attempts[0] += 1
        if attempts[0] == 1:
            raise failure
        return normal_release()

    child.release_memory = fail_once
    nav.boot(root)
    nav.go_to(child)

    with pytest.raises(MemoryError) as caught:
        nav.go_back()

    assert caught.value is failure
    assert nav.current is child
    nav.go_back()
    assert nav.current is root


def test_memory_reset_releases_owned_path_and_locks_input(monkeypatch):
    memory = MemoryStub()
    root = ScreenStub()
    child = ScreenStub(releases=True)
    nav = _nav(
        monkeypatch, memory,
        page_builder=lambda _page_id, _parent: child)
    nav.boot(root)
    nav.open(PAGE_CALCULATOR)

    nav.reset(root)

    assert nav.current is root
    assert child.calls.count("release_memory") == 1
    assert memory.collections == 1
    keyboard = KeyboardStub([(3, 0, False)], pressed=True)
    assert nav.poll_event(keyboard) is None
    keyboard.pressed = False
    assert nav.poll_event(keyboard) == (3, 0, False)


def test_reset_deactivates_then_releases_only_the_owned_page_path(
        monkeypatch):
    events = []
    memory = LifecycleMemory(events)
    get_nav = lambda: nav
    root = LifecycleScreen("root", events, get_nav)
    settings = LifecycleScreen("settings", events, get_nav)
    about = LifecycleScreen("about", events, get_nav)
    pages = {PAGE_SETTINGS: settings, PAGE_ABOUT: about}
    nav = _nav(
        monkeypatch, memory,
        page_builder=lambda page_id, _parent: pages[page_id])
    nav.boot(root)
    nav.open(PAGE_SETTINGS)
    nav.open(PAGE_ABOUT)
    events.clear()

    nav.reset(root)

    assert events == [
        "about.deactivate",
        "settings.deactivate",
        "root.deactivate",
        "about.release_memory",
        "settings.release_memory",
        "root.release_memory",
        "workspace",
        "collect",
        "root.activate:locked=True",
    ]
    assert nav.stack == [root]


def test_memory_intensive_operation_keeps_the_active_page_but_releases_others(
        monkeypatch):
    memory = MemoryStub()
    active = ScreenStub(releases=True)
    inactive = ScreenStub(releases=True)
    nav = _nav(
        monkeypatch, memory,
        page_builder=lambda _page_id, _parent: active)
    nav.boot(inactive)
    nav.open(PAGE_CALCULATOR)
    active.calls.clear()
    inactive.calls.clear()

    nav.prepare_memory_intensive_operation(active)

    assert active.calls == []
    assert inactive.calls == ["release_memory"]
    assert memory.plot_releases == 1
    assert memory.collections == 1


def test_first_ui_frame_replaces_boot_progress_before_return(monkeypatch):
    nav = _nav(monkeypatch)
    root = ScreenStub()

    _present_first_ui_frame(nav, root)

    assert root.calls == ["activate"]
    assert nav.renderer.presented == [root]


def test_navigation_reports_whether_renderer_committed_pixels(monkeypatch):
    nav = _nav(monkeypatch)
    root = ScreenStub()
    nav.boot(root)

    assert nav.present_current() is True
    nav.renderer.commit = False
    assert nav.present_current() is False
