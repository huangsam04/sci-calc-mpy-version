import main
from pathlib import Path
from anim import engine
from ui import renderer as renderer_module
from ui.element import UIElement
from ui.memory import MemoryManager
from ui.residency import PageResidency, SessionSwap
from ui.theme import CONTENT_W
from ui.motion import (PAGE_TRANSITION_MS, PANEL_SLIDE_MS,
                       MENU_CURSOR_MS, TEXT_CURSOR_MS, ACTIVE_FRAME_MS,
                       ACTIVE_LOOP_SLEEP_MS)
from screens.main_menu import MainMenu


class DisplayStub:
    width = 256
    height = 64
    byte_width = 128
    buffer_length = byte_width * height

    def __init__(self):
        self.gs4_buf = bytearray(self.buffer_length)
        self.blits = []
        self.fills = []
        self.gs4_fb = type(
            "FB", (),
            {"blit": lambda _, source, x, y: self.blits.append(
                (source, x, y))})()
        self.present_count = 0
        self.regions = []
        self.brightness = 100
        self.transition_currents = []

    def clear_buffers(self, color=0):
        for index in range(len(self.gs4_buf)):
            self.gs4_buf[index] = color

    def present(self):
        self.present_count += 1

    def present_region(self, column_start, column_count, data):
        self.regions.append(
            (column_start, column_count, bytes(data)))
        self.present_count += 1

    def set_transition_current(self, value):
        self.transition_currents.append(value)

    def set_brightness(self, value):
        self.brightness = value

    def draw_rectangle(self, *args):
        pass

    def draw_text8x8(self, *args, **kwargs):
        pass

    def draw_hline(self, *args):
        pass

    def fill_rectangle(self, *args):
        self.fills.append(args)


class ScreenStub:
    def __init__(self):
        self.activations = 0
        self.deactivations = 0
        self.draws = 0

    def activate(self):
        self.activations += 1

    def deactivate(self):
        self.deactivations += 1

    def draw(self, display):
        self.draws += 1


class KeyboardStub:
    def __init__(self, pressed=False):
        self.pressed = pressed

    def any_pressed(self):
        return self.pressed


class HeapStub:
    def __init__(self, free):
        self.free = free
        self.collects = 0

    def mem_free(self):
        return self.free

    def collect(self):
        self.collects += 1


class SidebarSpy:
    def __init__(self):
        self.battery_refreshes = []

    def draw(self, display, refresh_battery=True):
        self.battery_refreshes.append(refresh_battery)


def test_navigation_uses_controller_fade_when_reveal_buffer_cannot_be_allocated(
        monkeypatch):
    """A fragmented heap still produces motion instead of a hard page cut."""
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)

    def out_of_memory(_size):
        raise MemoryError("simulated fragmented heap")

    monkeypatch.setattr(renderer_module, "bytearray", out_of_memory,
                        raising=False)
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplayStub()
    nav = main.Nav(display, None, registry)
    first = ScreenStub()
    second = ScreenStub()

    nav.boot(first)
    nav.present_current()
    assert nav.enable_optional_resources() is False
    nav.go_to(second)

    assert nav.current is second
    assert nav.is_transitioning() is True
    nav.draw_transition(100 + main.TRANSITION_MS // 2)
    assert second.draws == 1
    nav.draw_transition(100 + main.TRANSITION_MS)
    assert nav.is_transitioning() is False
    assert display.transition_currents
    assert display.brightness == 100


def test_default_page_draw_oom_uses_allocation_bounded_minimal_shell(
        monkeypatch):
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)

    class RecordingDisplay(DisplayStub):
        def __init__(self):
            super().__init__()
            self.text = []

        def draw_text8x8(self, x, y, value, **kwargs):
            self.text.append(value)

    class BrokenDefaultScreen(ScreenStub):
        transition_title = "Broken"

        def draw_transition_default(self, display):
            raise MemoryError("injected default draw failure")

    registry = type("Registry", (), {"angle_mode": 0})()
    display = RecordingDisplay()
    nav = main.Nav(display, None, registry)
    first = ScreenStub()
    broken = BrokenDefaultScreen()
    nav.boot(first)
    nav.enable_optional_resources()

    nav.go_to(broken)

    assert nav.is_transitioning() is True
    assert "Broken" in display.text
    assert "Loading..." in display.text


def test_partial_transition_construction_releases_buffer_before_recovery_gc(
        monkeypatch):
    """Failed strip views must not pin the optional reveal workspace."""
    events = []

    class MemorySpy(MemoryManager):
        def collect(self):
            events.append(("collect", tuple(sorted(self._buffers))))

    monkeypatch.setattr(
        renderer_module, "memoryview",
        lambda buffer: (_ for _ in ()).throw(
            MemoryError("simulated view pressure")),
        raising=False)
    registry = type("Registry", (), {"angle_mode": 0})()
    memory = MemorySpy()
    nav = main.Nav(DisplayStub(), None, registry, memory=memory)

    assert nav.enable_optional_resources() is False
    assert memory._buffers == {}
    assert events[-1] == ("collect", ())

    # Releasing a failed optional phase must leave it retryable once heap
    # pressure has passed.
    monkeypatch.setattr(renderer_module, "memoryview", memoryview)
    assert nav.enable_optional_resources() is True
    assert tuple(sorted(memory._buffers)) == ("transition_strip",)


def test_navigation_defers_transition_buffers_until_optional_resources_are_enabled():
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)

    assert nav.renderer._transition_strip is None
    assert nav.enable_optional_resources() is True
    assert nav.renderer._transition_strip is not None


def test_first_navigation_allocates_only_the_fixed_reveal_strip_after_release(
        monkeypatch):
    allocated = []
    real_bytearray = bytearray

    def track_allocation(size):
        allocated.append(size)
        return real_bytearray(size)

    monkeypatch.setattr(renderer_module, "bytearray", track_allocation,
                        raising=False)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)
    first = ScreenStub()
    second = ScreenStub()

    nav.boot(first)
    nav.go_to(second)

    assert allocated == [
        renderer_module.TRANSITION_STRIP_GROUPS * 2 * DisplayStub.height]
    assert nav.is_transitioning() is True


def test_navigation_uses_fade_below_reveal_headroom():
    heap = HeapStub(100_000)
    memory = MemoryManager(gc_module=heap)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry, memory=memory)
    first = ScreenStub()
    second = ScreenStub()

    assert nav.enable_optional_resources() is True
    nav.boot(first)
    heap.free = renderer_module.TRANSITION_ACTIVE_HEADROOM - 1

    nav.go_to(second)

    assert nav.is_transitioning() is True
    assert nav.renderer._transition_strip is None
    assert second.activations == 1


def test_navigation_transition_is_non_blocking_and_locks_trigger_key(monkeypatch):
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)
    nav.enable_optional_resources()
    first = ScreenStub()
    second = ScreenStub()
    nav.boot(first)
    nav.present_current()

    nav.go_to(second)

    assert nav.current is second
    assert nav.is_transitioning() is True
    assert nav.filter_event(KeyboardStub(), (1, 1, False)) is None
    nav.draw_transition(400)
    assert nav.is_transitioning() is False
    assert nav.filter_event(KeyboardStub(pressed=True), (1, 1, False)) is None
    nav.settle_current()
    assert nav.filter_event(KeyboardStub(), (1, 1, False)) == (1, 1, False)


def test_only_escape_is_accepted_until_page_restore_finishes(monkeypatch):
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)

    class RestoringScreen(ScreenStub):
        swap_key = "restoring"

        def reset_state(self):
            pass

        def activate_default(self):
            pass

        def settle_step(self):
            return 1

    first = ScreenStub()
    second = RestoringScreen()
    nav.boot(first)
    nav.go_to(second)
    nav.draw_transition(100 + main.TRANSITION_MS)

    assert nav.filter_event(KeyboardStub(), (1, 1, False)) is None
    assert nav.allows_page_update(None) is False
    escape = nav.filter_event(KeyboardStub(), (0, 0, False))
    assert escape == (0, 0, False)
    assert nav.allows_page_update(escape) is True


def test_navigation_reveals_default_page_before_restoring_swap(
        monkeypatch, tmp_path):
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry, residency=residency)

    class StatefulScreen(ScreenStub):
        def __init__(self, key, value):
            super().__init__()
            self.swap_key = key
            self.value = value
            self.default_draws = 0

        def snapshot_state(self):
            return {"value": self.value}

        def reset_state(self):
            self.value = ""

        def restore_state(self, state):
            self.value = state["value"]

        def draw_transition_default(self, display):
            self.default_draws += 1

        def settle_step(self):
            return 0

    first = StatefulScreen("first", "saved")
    second = StatefulScreen("second", "")
    nav.enable_optional_resources()
    nav.boot(first)
    nav.present_current()

    nav.go_to(second)

    assert first.value == ""
    assert second.default_draws == 1
    assert second.draws == 0
    assert not (tmp_path / "first.swp").exists()

    nav.draw_transition(100 + main.TRANSITION_MS)
    while nav.settle_current():
        pass
    assert (tmp_path / "first.swp").exists()

    nav.go_back()
    assert first.value == ""
    nav.draw_transition(100 + main.TRANSITION_MS)
    while nav.settle_current():
        pass

    assert first.value == "saved"


def test_transition_performs_no_swap_or_page_rebuild_work(monkeypatch, tmp_path):
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)

    class CountingSwap(SessionSwap):
        def __init__(self, directory):
            super().__init__(directory)
            self.calls = []

        def pack(self, state):
            self.calls.append("pack")
            return super().pack(state)

        def write_packed(self, key, payload):
            self.calls.append("write")
            return super().write_packed(key, payload)

        def read(self, key):
            self.calls.append("read")
            return super().read(key)

    class DeferredScreen(ScreenStub):
        def __init__(self, key):
            super().__init__()
            self.swap_key = key
            self.rebuilds = 0

        def snapshot_state(self):
            return {"value": "bounded"}

        def reset_state(self):
            pass

        def activate_default(self):
            pass

        def settle_step(self):
            self.rebuilds += 1
            return 0

    swap = CountingSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry, residency=residency)
    first = DeferredScreen("first")
    second = DeferredScreen("second")
    nav.boot(first)

    nav.go_to(second)
    nav.draw_transition(150)
    nav.draw_transition(100 + main.TRANSITION_MS)

    assert swap.calls == []
    assert second.rebuilds == 0


def test_page_transition_stays_within_responsive_motion_budget():
    assert 160 <= main.TRANSITION_MS <= 200
    assert main.TRANSITION_MS == PAGE_TRANSITION_MS
    assert TEXT_CURSOR_MS < MENU_CURSOR_MS < PANEL_SLIDE_MS < PAGE_TRANSITION_MS
    assert ACTIVE_FRAME_MS == 16
    assert ACTIVE_LOOP_SLEEP_MS == 1


def test_periodic_gc_is_suspended_while_any_animation_is_active():
    source = (Path(__file__).parents[1] / "source" / "main.py").read_text(
        encoding="utf-8")
    start = source.index("if (_frame % 100 == 0")
    end = source.index("_frame += 1", start)
    gc_gate = source[start:end]

    assert "not nav.is_transitioning()" in gc_gate
    assert "not has_active_animations()" in gc_gate

    diagnostics_start = source.index("if (diagnostics", end)
    diagnostics_end = source.index(
        "if calc_screen.context.dirty", diagnostics_start)
    diagnostics_gate = source[diagnostics_start:diagnostics_end]
    assert "and not active" in diagnostics_gate


def test_idle_work_rechecks_animations_started_by_page_settlement():
    source = (Path(__file__).parents[1] / "source" / "main.py").read_text(
        encoding="utf-8")
    start = source.index("settling = nav.settle_current()")
    end = source.index("time.sleep_ms", start)
    idle_work = source[start:end]

    recheck = idle_work.index(
        "active = nav.is_transitioning() or has_active_animations()")
    persistence = idle_work.index("persistence.flush(now)")
    assert recheck < persistence
    assert "elif not settling and not active" in idle_work


def test_device_monitor_runs_full_residency_lifecycle_for_500_round_trips():
    source = (Path(__file__).parents[1] / "tools"
              / "device_runtime_monitor.py").read_text(encoding="utf-8")

    assert "TOTAL_ROUND_TRIPS = 500" in source
    assert "settling = nav.settle_current()" in source
    assert "while settling or active_animation_count()" in source
    assert "MAX_FIRST_FRAME_US = 32000" in source
    assert "MAX_ANIMATION_FRAME_US = 16000" in source
    assert "MIN_TRANSITION_FRAMES = 12" in source
    assert "MAX_HEAP_DRIFT_BYTES = 512" in source
    assert "MONITOR_SD_DURING_ANIMATION" in source
    assert "buffers_after != buffers_before" in source
    assert "MONITOR_ACCEPTANCE PASS" in source


def test_returning_to_main_menu_preserves_selected_item(monkeypatch, tmp_path):
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    nav = main.Nav(
        DisplayStub(), None, registry,
        residency=PageResidency(swap=swap))
    root = MainMenu(None)
    pages = [ScreenStub(), ScreenStub(), ScreenStub()]
    for index, page in enumerate(pages):
        root.add_screen("Page " + str(index), page)
    nav.boot(root)
    root.menu.cursor_pos = 2

    nav.go_to(pages[2])
    nav.go_back()

    assert root.menu.cursor_pos == 0
    nav.draw_transition(100 + main.TRANSITION_MS)
    while nav.settle_current():
        pass
    assert root.menu.cursor_pos == 2


def test_navigation_reset_clears_transition_lock_and_owned_animations(monkeypatch):
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)
    root = ScreenStub()
    child = UIElement()
    child.activate = lambda: None
    child.deactivate = lambda: None
    child.draw = lambda display: None
    nav.boot(root)
    nav.go_to(child)
    engine.insert_animation(child, "x", 0, 10, 100)

    nav.reset(root)

    assert nav.current is root
    assert nav.is_transitioning() is False
    assert nav.filter_event(KeyboardStub(pressed=True), (1, 1, False)) is None
    assert nav.filter_event(KeyboardStub(), (1, 1, False)) == (1, 1, False)
    assert engine.is_animating(child) is False


def test_transition_reveals_only_new_content_columns(monkeypatch):
    """A forward wipe starts at the right and never uploads a full frame."""
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplayStub()
    nav = main.Nav(display, None, registry)
    nav.enable_optional_resources()
    nav.boot(ScreenStub())
    nav.go_to(ScreenStub())

    display.regions[:] = []
    nav.draw_transition(100 + main.TRANSITION_MS // 4)

    assert display.regions
    revealed = sum(count for _, count, _ in display.regions)
    assert 0 < revealed < renderer_module.TRANSITION_TOTAL_GROUPS
    assert min(start for start, _, _ in display.regions) > 0
    assert all(len(data) == count * 2 * display.height
               for _, count, data in display.regions)


def test_transition_regions_never_cross_the_content_window(monkeypatch):
    """The hardware wipe leaves the fixed status-panel columns untouched."""
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplayStub()
    nav = main.Nav(display, None, registry)
    nav.enable_optional_resources()
    nav.boot(ScreenStub())
    nav.go_to(ScreenStub())

    display.regions[:] = []
    nav.draw_transition(100 + main.TRANSITION_MS // 2)

    assert display.regions
    assert all(start >= 0 and start + count <=
               renderer_module.TRANSITION_TOTAL_GROUPS
               for start, count, _ in display.regions)


def test_transition_finishes_from_the_captured_live_frame(monkeypatch):
    """The final reveal avoids an expensive duplicate destination redraw."""
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplayStub()
    incoming = ScreenStub()
    nav = main.Nav(display, None, registry)
    nav.enable_optional_resources()
    nav.boot(ScreenStub())
    nav.go_to(incoming)
    captured_draws = incoming.draws

    nav.draw_transition(100 + main.TRANSITION_MS)

    assert incoming.draws == captured_draws
    assert sum(count for _, count, _ in display.regions) == (
        renderer_module.TRANSITION_TOTAL_GROUPS)
    assert nav.is_transitioning() is False


def test_backward_transition_reveals_from_the_left(monkeypatch):
    """Back navigation mirrors the hardware reveal direction."""
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplayStub()
    nav = main.Nav(display, None, registry)
    nav.enable_optional_resources()
    outgoing = ScreenStub()
    incoming = ScreenStub()
    nav.boot(outgoing)
    nav.present_current()
    nav.go_to(incoming)
    nav.draw_transition(100 + main.TRANSITION_MS)
    display.regions[:] = []
    nav.go_back()
    nav.draw_transition(100 + main.TRANSITION_MS // 4)

    assert display.regions
    assert display.regions[0][0] == 0


def test_first_transition_reuses_the_page_already_held_by_display_ram(monkeypatch):
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)
    outgoing = ScreenStub()
    incoming = ScreenStub()
    nav.boot(outgoing)
    nav.present_current()
    outgoing_draws = outgoing.draws
    nav.enable_optional_resources()

    nav.go_to(incoming)

    assert outgoing.draws == outgoing_draws
    assert incoming.draws == 1


def test_transition_reuses_the_last_presented_outgoing_page_after_allocation(monkeypatch):
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)
    nav.enable_optional_resources()
    first = ScreenStub()
    outgoing = ScreenStub()
    incoming = ScreenStub()
    nav.boot(first)
    nav.present_current()
    nav.go_to(outgoing)
    nav.draw_transition(100 + main.TRANSITION_MS)
    outgoing_draws = outgoing.draws

    nav.go_to(incoming)

    assert outgoing.draws == outgoing_draws
    assert incoming.draws == 1


def test_renderer_reports_present_time_for_diagnostics(monkeypatch):
    times = iter((1_000, 1_275))
    monkeypatch.setattr(renderer_module.time, "ticks_us",
                        lambda: next(times), raising=False)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)
    nav.boot(ScreenStub())

    nav.present_current()

    assert nav.last_present_us == 275


def test_hardware_transition_preserves_sidebar_without_redrawing_it(monkeypatch):
    """Incremental region uploads must not resample fixed chrome per frame."""
    display = DisplayStub()
    sidebar = SidebarSpy()
    renderer = renderer_module.Renderer(display, sidebar)
    renderer.enable_transition_buffers()

    renderer.present_transition(0.5, True)

    assert sidebar.battery_refreshes == []
    assert display.regions


def test_page_animation_cancel_does_not_cancel_another_page():
    first = UIElement()
    child = UIElement()
    first.animation_children = lambda: (child,)
    second = UIElement()
    engine.insert_animation(child, "x", 0, 10, 100)
    engine.insert_animation(second, "x", 0, 10, 100)

    engine.cancel_animations(first)

    assert engine.is_animating(child) is False
    assert engine.is_animating(second) is True
    engine.cancel_all_animations()


def test_animation_easing_has_exact_smooth_endpoints():
    assert engine.easing_indent(0.0) == 0.0
    assert engine.easing_indent(1.0) == 1.0
    assert engine.easing_smooth(0.0) == 0.0
    assert engine.easing_smooth(0.5) == 0.5
    assert engine.easing_smooth(1.0) == 1.0
    samples = [engine.easing_smooth(i / 10) for i in range(11)]
    assert samples == sorted(samples)


def test_quadratic_ease_out_is_responsive_without_a_stalled_tail():
    samples = [engine.easing_out_quad(i / 4) for i in range(5)]
    assert samples[0] == 0.0
    assert samples[-1] == 1.0
    assert 0.4 < samples[1] < 0.5
    deltas = [samples[i + 1] - samples[i] for i in range(4)]
    assert deltas == sorted(deltas, reverse=True)

    # At the configured frame cadence every transition frame moves at least
    # one pixel, instead of spending the final frames apparently frozen.
    frame_count = main.TRANSITION_MS // ACTIVE_FRAME_MS
    positions = [int(CONTENT_W * engine.easing_out_quad(i / frame_count))
                 for i in range(frame_count + 1)]
    assert len(set(positions)) == len(positions)


def test_page_transition_has_enough_visible_motion_samples():
    """A full-width reveal must not turn into a handful of large jumps."""
    positions = list(range(0, main.TRANSITION_MS, ACTIVE_FRAME_MS))
    if positions[-1] != main.TRANSITION_MS:
        positions.append(main.TRANSITION_MS)

    offsets = [int(CONTENT_W * engine.easing_out_quad(
        elapsed / main.TRANSITION_MS)) for elapsed in positions]
    steps = [offsets[index + 1] - offsets[index]
             for index in range(len(offsets) - 1)]

    assert len(steps) >= 12
    assert max(steps) <= CONTENT_W // 6


def test_thousand_navigation_cycles_keep_animation_buffers_bounded(
        monkeypatch, tmp_path):
    now = [100]
    monkeypatch.setattr(main.time, "ticks_ms", lambda: now[0])
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    memory = MemoryManager()
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(
        DisplayStub(), None, registry, memory=memory,
        residency=PageResidency(swap=swap, memory=memory))

    class Stateful(ScreenStub):
        def __init__(self, key):
            super().__init__()
            self.swap_key = key
            self.value = 0

        def snapshot_state(self):
            return {"value": self.value}

        def reset_state(self):
            self.value = 0

        def restore_state(self, state):
            self.value = int(state.get("value", 0))

    root = Stateful("stress_root")
    children = [Stateful("stress_child_" + str(index)) for index in range(4)]
    nav.enable_optional_resources()
    nav.boot(root)
    nav.present_current()

    for index in range(1000):
        child = children[index % len(children)]
        root.value = index
        nav.go_to(child)
        assert nav.is_transitioning()
        now[0] += main.TRANSITION_MS
        nav.draw_transition(now[0])
        while nav.settle_current():
            pass

        child.value = index
        nav.go_back()
        assert nav.is_transitioning()
        now[0] += main.TRANSITION_MS
        nav.draw_transition(now[0])
        while nav.settle_current():
            pass

    assert set(memory._buffers) == {"transition_strip"}
    assert nav.is_transitioning() is False


def test_delayed_animation_stays_registered_until_start(monkeypatch):
    now = [100]
    monkeypatch.setattr(engine.time, "ticks_ms", lambda: now[0])
    monkeypatch.setattr(engine.time, "ticks_diff", lambda a, b: a - b)
    target = UIElement()
    animation = engine.insert_animation(target, "x", 0, 10, 100, delay=50)

    now[0] = 120
    engine.animate_all()
    assert engine.is_animating(target) is True
    assert animation.started is False

    now[0] = 150
    engine.animate_all()
    assert animation.started is True
    engine.cancel_all_animations()
