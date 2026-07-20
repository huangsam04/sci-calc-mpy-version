import main
from anim import engine
from ui.element import UIElement


class DisplayStub:
    width = 256
    height = 64
    buffer_length = 8

    def __init__(self):
        self.gs4_buf = bytearray(self.buffer_length)
        self.gs4_fb = type("FB", (), {"blit": lambda *args: None})()
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


class ScreenStub:
    def __init__(self):
        self.activations = 0
        self.deactivations = 0

    def activate(self):
        self.activations += 1

    def deactivate(self):
        self.deactivations += 1

    def draw(self, display):
        pass


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
