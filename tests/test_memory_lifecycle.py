from pathlib import Path

import main
from calc.functions import build_registry
from screens import plot as plot_module
from screens.plot import PlotScreen
from ui.element import UIElement
from ui.memory import MemoryManager, plot_curve_buffer_size


class CollectSpy:
    def __init__(self):
        self.collects = 0

    def collect(self):
        self.collects += 1


class Screen(UIElement):
    def __init__(self, name, events, releases=False):
        super().__init__()
        self.name = name
        self.events = events
        self.releases = releases

    def activate(self):
        self.events.append(self.name + ".activate")

    def deactivate(self):
        self.events.append(self.name + ".deactivate")

    def release_memory(self):
        self.events.append(self.name + ".release")
        return self.releases


def test_memory_plan_reserves_plot_workspace_at_a_fixed_size():
    memory = MemoryManager()

    workspace = memory.reserve_plot_workspace(64)

    assert len(workspace) == plot_curve_buffer_size(64)
    assert memory.get_buffer("plot_curve", len(workspace)) is workspace


def test_released_optional_buffer_can_be_retried_after_a_previous_failure():
    calls = []

    def allocator(size):
        calls.append(size)
        if len(calls) <= 2:
            raise MemoryError("fragmented")
        return bytearray(size)

    memory = MemoryManager()

    assert memory.reserve_buffer("optional", 32, allocator) is None
    assert memory.release_buffer("optional") is True
    assert len(memory.reserve_buffer("optional", 32, allocator)) == 32


def test_navigation_captures_old_page_then_reclaims_before_new_activation(
        monkeypatch):
    events = []
    collector = CollectSpy()
    memory = MemoryManager(gc_module=collector)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(object(), None, registry, memory=memory)
    old = Screen("old", events, releases=True)
    new = Screen("new", events)
    nav.renderer.hold_outgoing = lambda screen: (
        events.append("capture." + screen.name) or True)
    nav.renderer.capture_incoming = lambda screen, default=False: (
        events.append("capture." + screen.name) or True)
    nav.renderer.can_start_transition = lambda: True

    nav.boot(old)
    nav.register_screens((old, new))
    events[:] = []

    nav.go_to(new)

    assert events == [
        "capture.old", "old.release", "old.deactivate", "new.activate",
        "capture.new",
    ]
    assert collector.collects == 1


def test_function_reload_reclaims_inactive_pages_before_plugin_compilation(
        monkeypatch):
    events = []

    class NavStub:
        def prepare_memory_intensive_operation(self, incoming):
            events.append(("prepare", incoming))

    panel = object()
    registry = object()
    settings = {"enabled_functions": ["basic"]}

    def reload_stub(reload_settings, reload_registry):
        events.append(("reload", reload_settings, reload_registry))
        return "reloaded"

    monkeypatch.setattr(main, "_reload_functions", reload_stub)

    assert main._reload_functions_after_reclaim(
        NavStub(), panel, settings, registry) == "reloaded"
    assert events == [
        ("prepare", panel),
        ("reload", settings, registry),
    ]


def test_plot_navigation_releases_old_page_and_defers_workspace_until_settle(
        monkeypatch):
    events = []
    collector = CollectSpy()
    memory = MemoryManager(gc_module=collector)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(type("Display", (), {"height": 64})(), None, registry,
                   memory=memory)
    old = Screen("old", events, releases=True)
    plot = Screen("plot", events)
    plot.requires_serial_memory = True
    plot.requires_plot_workspace = True
    captures = []
    nav.renderer.hold_outgoing = lambda screen: captures.append(screen)
    nav.renderer.can_start_transition = lambda: True
    nav.renderer.capture_incoming = lambda screen, default=False: (
        events.append("plot.default") or True)
    reserve_workspace = memory.reserve_plot_workspace
    monkeypatch.setattr(
        memory, "reserve_plot_workspace",
        lambda height: (events.append("plot.workspace")
                        or reserve_workspace(height)))

    nav.boot(old)
    nav.register_screens((old, plot))
    events[:] = []
    nav.go_to(plot)

    assert captures == [old]
    assert nav.is_transitioning() is True
    assert "plot.workspace" not in events
    assert events.index("old.release") < events.index("plot.activate")
    assert events.index("plot.activate") < events.index("plot.default")


def test_memory_intensive_operation_releases_layers_before_reclaim(monkeypatch):
    events = []
    memory = MemoryManager()
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(object(), None, registry, memory=memory)
    nav.renderer.release_transition_buffers = lambda: (
        events.append("transition.release") or True)
    monkeypatch.setattr(
        memory, "reclaim_for",
        lambda incoming, aggressive=False: events.append(
            ("reclaim", incoming, aggressive)) or True)
    monkeypatch.setattr(
        memory, "release_plot_workspace",
        lambda: events.append("plot.release") or True)

    panel = object()
    nav.prepare_memory_intensive_operation(panel)

    assert events == [
        "transition.release",
        ("reclaim", panel, True),
        "plot.release",
    ]


def test_navigation_reset_always_collects_after_aggressive_reclaim():
    events = []
    collector = CollectSpy()
    memory = MemoryManager(gc_module=collector)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(object(), None, registry, memory=memory)
    root = Screen("root", events, releases=False)
    nav.boot(root)
    nav.register_screens((root,))

    nav.reset(root)

    assert collector.collects == 1


def test_boot_presents_core_ui_before_requesting_optional_buffers():
    source = (Path(__file__).parents[1] / "source" / "main.py").read_text(
        encoding="utf-8")

    plugin_load = source.index("registry = _reload_functions(settings)")
    power_import = source.index("from utils.power import AWAKE, WOKE, DisplayPower")
    screens = source.index("from screens.main_menu import MainMenu")
    boot = source.index("nav.boot(main_menu)")
    first_frame = source.index("nav.mark_first_frame_presented()")
    optional_restore = source.index("nav.restore_optional_resources()")

    assert plugin_load < power_import < screens < boot < first_frame < optional_restore


def test_plot_uses_preplanned_workspace_without_late_bytearray_allocation(
        monkeypatch):
    memory = MemoryManager()
    memory.reserve_plot_workspace(64)
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "x^2"

    monkeypatch.setattr(
        plot_module, "bytearray",
        lambda size: (_ for _ in ()).throw(MemoryError("late allocation")),
        raising=False)

    plot._render_curve()

    assert plot._curve_fb is not None
