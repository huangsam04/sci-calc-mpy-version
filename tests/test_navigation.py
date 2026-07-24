import ui.renderer
import ui.sidebar

from main import Nav, _drain_input_batch, _present_first_ui_frame


class RendererStub:
    def __init__(self, display, sidebar, memory=None):
        self.presented = []
        self.last_present_us = 7
        self.invalidated = False

    def present(self, screen):
        self.presented.append(screen)

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


def _nav(monkeypatch, memory=None):
    monkeypatch.setattr(ui.renderer, "Renderer", RendererStub)
    monkeypatch.setattr(ui.sidebar, "Sidebar", lambda font, registry: object())
    return Nav(None, None, object(), memory=memory)


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


def test_first_ui_frame_replaces_boot_progress_before_return(monkeypatch):
    nav = _nav(monkeypatch)
    root = ScreenStub()

    _present_first_ui_frame(nav, root)

    assert root.calls == ["activate"]
    assert nav.renderer.presented == [root]
