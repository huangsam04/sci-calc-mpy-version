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

    workspace = memory.reserve_plot_workspace(64)

    assert len(workspace) == plot_curve_buffer_size(64)
    assert memory.get_buffer("plot_curve", len(workspace)) is workspace
    assert tuple(memory._buffers) == ("plot_curve",)
    assert memory.release_plot_workspace()
    assert memory.get_buffer("plot_curve") is None


def test_failed_optional_allocation_can_be_retried_without_placeholder():
    calls = []

    def allocator(size):
        calls.append(size)
        if len(calls) <= 2:
            raise MemoryError("fragmented")
        return bytearray(size)

    memory = MemoryManager()

    assert memory.reserve_buffer("optional", 32, allocator) is None
    assert memory.release_buffer("optional") is False
    assert len(memory.reserve_buffer("optional", 32, allocator)) == 32


def test_function_reload_reclaims_before_compiling(monkeypatch):
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


def test_memory_intensive_operation_releases_plot_then_collects(monkeypatch):
    events = []
    collector = CollectSpy()
    memory = MemoryManager(gc_module=collector)
    memory.reserve_buffer("plot_curve", 8)

    class NavHarness:
        pass

    nav = NavHarness()
    nav.memory = memory
    nav._managed = ()
    nav._collect_pending = True
    main.Nav.prepare_memory_intensive_operation(nav, object())

    assert memory.get_buffer("plot_curve") is None
    assert collector.collects == 1


def test_async_plot_uses_preplanned_workspace_without_late_bytearray(
        monkeypatch):
    memory = MemoryManager()
    memory.reserve_plot_workspace(64)
    plot = PlotScreen(None, registry=build_registry(), memory=memory)
    plot.expr = "x^2"
    plot._needs_curve_restore = True
    plot._curve_restore_auto_scale = True
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

    assert plot._curve_fb is not None
