import main
from anim import engine
from ui import renderer as renderer_module
from ui.element import UIElement
from ui.theme import CONTENT_W
from ui.motion import (PAGE_TRANSITION_MS, PANEL_SLIDE_MS,
                       MENU_CURSOR_MS, TEXT_CURSOR_MS)


class DisplayStub:
    width = 256
    height = 64
    buffer_length = 8

    def __init__(self):
        self.gs4_buf = bytearray(self.buffer_length)
        self.blits = []
        self.fills = []
        self.gs4_fb = type(
            "FB", (),
            {"blit": lambda _, source, x, y: self.blits.append(
                (source, x, y))})()
        self.present_count = 0

    def clear_buffers(self, color=0):
        for index in range(len(self.gs4_buf)):
            self.gs4_buf[index] = color

    def present(self):
        self.present_count += 1

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


def test_navigation_transition_is_non_blocking_and_locks_trigger_key(monkeypatch):
    times = iter((100, 400))
    monkeypatch.setattr(main.time, "ticks_ms", lambda: next(times))
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)
    first = ScreenStub()
    second = ScreenStub()
    nav.boot(first)

    nav.go_to(second)

    assert nav.current is second
    assert nav.is_transitioning() is True
    assert nav.filter_event(KeyboardStub(), (1, 1, False)) is None
    nav.draw_transition(400)
    assert nav.is_transitioning() is False
    assert nav.filter_event(KeyboardStub(pressed=True), (1, 1, False)) is None
    assert nav.filter_event(KeyboardStub(), (1, 1, False)) == (1, 1, False)


def test_page_transition_stays_within_responsive_motion_budget():
    assert 160 <= main.TRANSITION_MS <= 200
    assert main.TRANSITION_MS == PAGE_TRANSITION_MS
    assert TEXT_CURSOR_MS < MENU_CURSOR_MS < PANEL_SLIDE_MS < PAGE_TRANSITION_MS


def test_transition_moves_only_content_with_balanced_deceleration(monkeypatch):
    """The sidebar stays fixed and the page enters without a violent jump."""
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplayStub()
    nav = main.Nav(display, None, registry)
    nav.boot(ScreenStub())
    nav.go_to(ScreenStub())

    display.blits[:] = []
    nav.draw_transition(100 + main.TRANSITION_MS // 4)

    assert len(display.blits) == 2
    assert all(source.width == CONTENT_W
               for source, _, _ in display.blits)
    incoming = max(x for _, x, _ in display.blits)
    assert CONTENT_W // 2 < incoming < CONTENT_W * 2 // 3


def test_transition_erases_every_pixel_outside_content_before_sidebar(monkeypatch):
    """Sliding page pixels cannot remain behind the fixed status panel."""
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplayStub()
    nav = main.Nav(display, None, registry)
    nav.boot(ScreenStub())
    nav.go_to(ScreenStub())

    display.fills[:] = []
    nav.draw_transition(100 + main.TRANSITION_MS // 2)

    assert (CONTENT_W, 0, display.width - CONTENT_W, display.height, 0) in display.fills


def test_transition_finishes_with_a_live_canonical_page_frame(monkeypatch):
    """The last transition frame cannot linger until the idle refresh."""
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 100)
    registry = type("Registry", (), {"angle_mode": 0})()
    display = DisplayStub()
    incoming = ScreenStub()
    nav = main.Nav(display, None, registry)
    nav.boot(ScreenStub())
    nav.go_to(incoming)
    captured_draws = incoming.draws

    nav.draw_transition(100 + main.TRANSITION_MS)

    assert incoming.draws == captured_draws + 1
    assert nav.is_transitioning() is False


def test_renderer_reports_present_time_for_diagnostics(monkeypatch):
    times = iter((1_000, 1_275))
    monkeypatch.setattr(renderer_module.time, "ticks_us",
                        lambda: next(times), raising=False)
    registry = type("Registry", (), {"angle_mode": 0})()
    nav = main.Nav(DisplayStub(), None, registry)
    nav.boot(ScreenStub())

    nav.present_current()

    assert nav.last_present_us == 275


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
    frame_count = main.TRANSITION_MS // 20
    positions = [int(CONTENT_W * engine.easing_out_quad(i / frame_count))
                 for i in range(frame_count + 1)]
    assert len(set(positions)) == len(positions)


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
