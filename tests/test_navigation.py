import ui.renderer
import ui.sidebar

from main import Nav, _drain_input_batch, _present_first_ui_frame


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


def _nav(monkeypatch, memory=None, display=None):
    monkeypatch.setattr(ui.renderer, "Renderer", RendererStub)
    monkeypatch.setattr(ui.sidebar, "Sidebar", lambda font, registry: object())
    return Nav(display, None, object(), memory=memory)


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


def test_memory_reset_releases_managed_caches_and_locks_input(monkeypatch):
    memory = MemoryStub()
    nav = _nav(monkeypatch, memory)
    root = ScreenStub()
    managed = (ScreenStub(releases=True), ScreenStub(releases=True))
    nav.register_screens(managed)
    nav.boot(root)
    nav.go_to(managed[0])

    nav.reset(root)

    assert nav.current is root
    assert [screen.calls.count("release_memory")
            for screen in managed] == [1, 1]
    assert memory.collections == 1
    keyboard = KeyboardStub([(3, 0, False)], pressed=True)
    assert nav.poll_event(keyboard) is None
    keyboard.pressed = False
    assert nav.poll_event(keyboard) == (3, 0, False)


def test_reset_deactivates_the_stack_then_releases_every_registered_page(
        monkeypatch):
    events = []
    memory = LifecycleMemory(events)
    nav = _nav(monkeypatch, memory)
    get_nav = lambda: nav
    root = LifecycleScreen("root", events, get_nav)
    settings = LifecycleScreen("settings", events, get_nav)
    about = LifecycleScreen("about", events, get_nav)
    dormant = LifecycleScreen("dormant", events, get_nav)
    nav.register_screens((root, settings, about, dormant))
    nav.stack[:] = [root, settings, about]

    nav.reset(root)

    assert events == [
        "about.deactivate",
        "settings.deactivate",
        "root.deactivate",
        "root.release_memory",
        "settings.release_memory",
        "about.release_memory",
        "dormant.release_memory",
        "workspace",
        "collect",
        "root.activate:locked=True",
    ]
    assert nav.stack == [root]


def test_memory_intensive_operation_keeps_the_active_page_but_releases_others(
        monkeypatch):
    memory = MemoryStub()
    nav = _nav(monkeypatch, memory)
    active = ScreenStub(releases=True)
    inactive = ScreenStub(releases=True)
    nav.register_screens((active, inactive))

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
