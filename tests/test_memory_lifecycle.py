import sys

import main
from calc.functions import build_registry
from screens import plot as plot_module
from screens.plot import PlotScreen
from ui.element import SETTLE_MORE
from ui.memory import MemoryManager, plot_curve_buffer_size


class CollectSpy:
    def __init__(self):
        self.collects = 0

    def collect(self):
        self.collects += 1


def test_memory_plan_owns_only_one_fixed_plot_workspace():
    memory = MemoryManager()
    boot_workspace = memory.get_plot_workspace()

    workspace = memory.reserve_plot_workspace(64)

    assert plot_curve_buffer_size(64) == 104
    assert workspace is boot_workspace
    assert len(workspace) == plot_curve_buffer_size(64)
    assert memory.get_plot_workspace(len(workspace)) is workspace
    assert memory.release_plot_workspace() is False
    assert memory.get_plot_workspace() is workspace


def test_function_reload_reclaims_before_compiling(monkeypatch):
    events = []

    class NavStub:
        def prepare_memory_intensive_operation(self, incoming):
            events.append(("prepare", incoming))

    panel = object()
    registry = object()
    settings = {"enabled_functions": ["basic", "plugin:external"]}

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


def test_function_reload_collects_before_and_after_compiling(monkeypatch):
    events = []

    class NavStub:
        def prepare_memory_intensive_operation(self, incoming):
            events.append(("prepare", incoming))

    panel = object()
    registry = object()
    settings = {"enabled_functions": ["basic", "plugin:external"]}
    monkeypatch.setattr(
        main.gc, "collect", lambda: events.append(("collect",)))
    monkeypatch.setattr(
        main, "_reload_functions",
        lambda reload_settings, reload_registry: events.append(
            ("reload", reload_settings, reload_registry)) or "reloaded")

    assert main._reload_functions_after_reclaim(
        NavStub(), panel, settings, registry) == "reloaded"
    assert events == [
        ("prepare", panel),
        ("collect",),
        ("reload", settings, registry),
        ("collect",),
    ]


def test_function_reload_releases_rebuildable_loader_after_compiling(
        monkeypatch):
    class NavStub:
        def prepare_memory_intensive_operation(self, _incoming):
            pass

    loader = object()
    calc_package = sys.modules["calc"]
    prior_loader = getattr(calc_package, "loader", None)
    had_loader = hasattr(calc_package, "loader")
    monkeypatch.setitem(sys.modules, "calc.loader", loader)
    calc_package.loader = loader
    monkeypatch.setattr(main.gc, "mem_free", lambda: 1, raising=False)
    monkeypatch.setattr(
        main, "_reload_functions",
        lambda _settings, _registry: "reloaded")

    try:
        assert main._reload_functions_after_reclaim(
            NavStub(), object(),
            {"enabled_functions": ["plugin:external"]},
            object()) == "reloaded"
        assert "calc.loader" not in sys.modules
        assert getattr(calc_package, "loader", None) is not loader
    finally:
        if had_loader:
            calc_package.loader = prior_loader
        elif hasattr(calc_package, "loader"):
            delattr(calc_package, "loader")


def test_bundled_function_reload_skips_cold_reclaim_and_gc(monkeypatch):
    events = []

    class NavStub:
        def prepare_memory_intensive_operation(self, _incoming):
            events.append("prepare")

    monkeypatch.setattr(
        main.gc, "collect", lambda: events.append("collect"))
    monkeypatch.setattr(
        main, "_drop_function_loader_module", lambda: events.append("drop"))
    monkeypatch.setattr(
        main, "_reload_bundled_functions",
        lambda reload_settings, reload_registry: events.append(
            (reload_settings, reload_registry)) or "reloaded")
    settings = {"enabled_functions": [
        "basic", "plugin:basic", "plugin:solve", "plugin:trig"]}
    registry = object()

    assert main._reload_functions_after_reclaim(
        NavStub(), object(), settings, registry) == "reloaded"
    assert events == [(settings, registry)]


def test_memory_intensive_operation_releases_plot_then_collects(monkeypatch):
    events = []
    collector = CollectSpy()
    memory = MemoryManager(gc_module=collector)
    workspace = memory.get_plot_workspace()

    class NavHarness:
        pass

    nav = NavHarness()
    nav.memory = memory
    nav.stack = ()
    nav._pending_screen = None
    nav._collect_pending = True
    nav._release_owned_screens = (
        lambda active: main.Nav._release_owned_screens(nav, active))
    main.Nav.prepare_memory_intensive_operation(nav, object())

    assert memory.get_plot_workspace() is workspace
    assert collector.collects == 1


def test_async_plot_uses_preplanned_workspace_without_late_bytearray(
        monkeypatch):
    memory = MemoryManager()
    memory.reserve_plot_workspace(64)
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "x^2"
    plot._state[2][2] = True
    plot._state[2][3] = True
    monkeypatch.setattr(
        plot_module, "bytearray",
        lambda size: (_ for _ in ()).throw(MemoryError("late allocation")),
        raising=False)

    flags = SETTLE_MORE
    steps = 0
    while flags & SETTLE_MORE:
        flags = plot.settle_step()
        steps += 1
        assert steps < 40

    assert plot._state[2][0] is not None
    assert plot._state[2][0] is plot._state[2][1]
    assert len(plot._state[2][0]) == 104
