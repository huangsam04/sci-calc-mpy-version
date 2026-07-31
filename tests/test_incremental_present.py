from ui.renderer import Renderer
from ui.motion import FrameScheduler


class DisplaySpy:
    height = 64

    def __init__(self):
        self.clears = 0
        self.content_clears = 0
        self.full_presents = 0
        self.row_presents = []
        self.shifts = []
        self.windows = []

    def clear_buffers(self, color=0):
        self.clears += 1

    def fill_rectangle(self, *args):
        self.content_clears += 1

    def present(self):
        self.full_presents += 1

    def present_rows(self, rows):
        # Renderer owns a reusable fixed backing store.  Snapshot only its
        # active bands; retaining the mutable backing would make earlier
        # presents appear to change as later frames reuse it.
        self.row_presents.append(
            tuple((row_start, row_count)
                  for row_start, row_count in rows if row_count))

    def shift_content(self, delta, width):
        self.shifts.append((delta, width))

    def begin_content_draw(self, offset, clip_start, clip_end):
        self.windows.append((offset, clip_start, clip_end))

    def end_content_draw(self):
        self.windows.append((0, 0, 256))


class SidebarSpy:
    def __init__(self):
        self.draws = 0
        self.refresh_checks = 0
        self.refreshes = []

    def refresh_needed(self):
        self.refresh_checks += 1
        return True

    def draw(self, display, refresh=True):
        self.draws += 1
        self.refreshes.append(refresh)
        return False


class PartialScreen:
    def __init__(self):
        self.draws = 0
        self.partial_draws = 0
        self.marks = 0

    def draw(self, display):
        self.draws += 1

    def draw_present_rows(self, display):
        self.partial_draws += 1

    def collect_present_damage(self, damage):
        if self.marks:
            damage.add(15, 21)
            return 1
        return 0

    def mark_presented(self):
        self.marks += 1


class ExplicitDamageScreen:
    def __init__(self):
        self.draws = 0
        self.partial_draws = 0
        self.marks = 0

    def draw(self, display):
        self.draws += 1

    def draw_present_rows(self, display):
        self.partial_draws += 1

    def collect_present_damage(self, damage):
        if self.marks == 1:
            damage.add(4, 5)
            damage.add(28, 6)
            return 1
        return 0

    def mark_presented(self):
        self.marks += 1


class FailingSlideScreen:
    def draw(self, display):
        raise MemoryError("slide draw")


_SCREEN_HOOK_NAMES = (
    "collect_present_damage", "draw_present_rows",
    "draw", "mark_presented")


class UnboundHookScreen:
    """Fails if Renderer obtains a bound hook from this screen."""

    __slots__ = ("draws", "partial_draws", "marks")

    def __init__(self):
        self.draws = 0
        self.partial_draws = 0
        self.marks = 0

    def __getattribute__(self, name):
        if name in _SCREEN_HOOK_NAMES:
            raise AssertionError("steady renderer path must use class hooks")
        return object.__getattribute__(self, name)

    def collect_present_damage(self, damage):
        if self.marks == 1:
            damage.add(6, 4)
            return 1
        return 0

    def draw_present_rows(self, display):
        self.partial_draws += 1

    def draw(self, display):
        self.draws += 1

    def mark_presented(self):
        self.marks += 1


def test_renderer_uses_rows_only_after_the_screen_has_a_canonical_frame():
    display = DisplaySpy()
    screen = PartialScreen()
    sidebar = SidebarSpy()
    renderer = Renderer(display, sidebar)

    renderer.present(screen)
    renderer.present(screen)

    assert display.full_presents == 1
    assert display.row_presents == [((15, 21),)]
    assert display.clears == 1
    assert screen.draws == 1
    assert screen.partial_draws == 1
    assert screen.marks == 2
    assert sidebar.draws == 1


def test_partial_present_defers_sidebar_polling_until_an_idle_full_frame():
    """Typing must not turn a due battery poll into a full OLED upload."""
    display = DisplaySpy()
    screen = PartialScreen()
    sidebar = SidebarSpy()
    renderer = Renderer(display, sidebar)

    renderer.present(screen)
    renderer.present(screen)

    assert display.full_presents == 1
    assert display.row_presents == [((15, 21),)]
    assert sidebar.draws == 1
    assert sidebar.refresh_checks == 0


def test_first_ui_frame_draws_cached_sidebar_without_an_adc_poll():
    display = DisplaySpy()
    sidebar = SidebarSpy()
    renderer = Renderer(display, sidebar)

    assert renderer.present(PartialScreen()) is True

    assert sidebar.refresh_checks == 0
    assert sidebar.refreshes == [False]


def test_scheduler_allows_sidebar_poll_only_from_a_quiet_interval():
    display = DisplaySpy()
    sidebar = SidebarSpy()
    renderer = Renderer(display, sidebar)
    scheduler = FrameScheduler(now=100, sidebar_refresh_ms=10)

    renderer.present(PartialScreen())

    assert scheduler.sidebar_poll_due(110, quiet=False) is False
    assert sidebar.refresh_checks == 0
    assert scheduler.sidebar_poll_due(110, quiet=True) is True
    assert renderer.poll_sidebar() is True
    assert sidebar.refresh_checks == 1


def test_page_switch_preserves_sidebar_pixels_until_explicit_invalidation():
    display = DisplaySpy()
    first = PartialScreen()
    second = PartialScreen()
    sidebar = SidebarSpy()
    renderer = Renderer(display, sidebar)

    renderer.present(first)
    renderer.present(second)

    assert display.clears == 1
    assert display.content_clears == 1
    assert display.full_presents == 2
    assert sidebar.draws == 1

    renderer.invalidate_sidebar()
    renderer.present(second)

    assert display.clears == 2
    assert display.content_clears == 1
    assert sidebar.draws == 2


def test_renderer_composes_forward_slide_in_exposed_single_buffer_strip():
    display = DisplaySpy()
    first = PartialScreen()
    second = PartialScreen()
    renderer = Renderer(display, SidebarSpy())
    renderer.present(first)

    assert renderer.present_slide(second, -1, 42, 42) is True

    assert display.shifts == [(-42, 210)]
    assert display.windows == [(168, 168, 210), (0, 0, 256)]
    assert display.full_presents == 2
    assert renderer._visible_screen is first
    assert second.draws == 1
    assert second.marks == 0

    assert renderer.present_slide(second, -1, 210, 168) is True

    assert display.shifts[-1] == (-168, 210)
    assert display.windows[-2:] == [(0, 0, 210), (0, 0, 256)]
    assert renderer._visible_screen is second
    assert second.marks == 1


def test_renderer_first_two_pixel_slide_frame_draws_exact_exposed_strip():
    display = DisplaySpy()
    first = PartialScreen()
    second = PartialScreen()
    renderer = Renderer(display, SidebarSpy())
    renderer.present(first)

    assert renderer.present_slide(second, -1, 2, 2) is True

    assert display.shifts == [(-2, 210)]
    assert display.windows == [(208, 208, 210), (0, 0, 256)]
    assert display.full_presents == 2
    assert second.draws == 1


def test_renderer_composes_back_slide_from_left_exposed_strip():
    display = DisplaySpy()
    first = PartialScreen()
    parent = PartialScreen()
    renderer = Renderer(display, SidebarSpy())
    renderer.present(first)

    renderer.present_slide(parent, 1, 42, 42)

    assert display.shifts == [(42, 210)]
    assert display.windows == [(-168, 0, 42), (0, 0, 256)]


def test_renderer_always_restores_full_draw_window_after_slide_error():
    display = DisplaySpy()
    renderer = Renderer(display, SidebarSpy())
    renderer.present(PartialScreen())

    try:
        renderer.present_slide(FailingSlideScreen(), -1, 42, 42)
    except MemoryError as error:
        assert str(error) == "slide draw"
    else:
        raise AssertionError("slide draw error was not raised")

    assert display.windows == [(168, 168, 210), (0, 0, 256)]


def test_renderer_reuses_one_damage_backing_store_for_explicit_partial_bands():
    display = DisplaySpy()
    screen = ExplicitDamageScreen()
    renderer = Renderer(display, SidebarSpy())
    backing = renderer._damage.ranges

    assert renderer.present(screen) is True
    assert renderer.present(screen) is True

    assert renderer._damage.ranges is backing
    assert display.row_presents == [((4, 5), (28, 6))]
    assert screen.partial_draws == 1


def test_renderer_caches_unbound_class_hooks_for_steady_partial_frames():
    display = DisplaySpy()
    screen = UnboundHookScreen()
    renderer = Renderer(display, SidebarSpy())

    assert renderer.present(screen) is True
    assert renderer.present(screen) is True

    assert renderer._hook_screen is screen
    assert renderer._collect_hook is UnboundHookScreen.collect_present_damage
    assert renderer._partial_draw_hook is UnboundHookScreen.draw_present_rows
    assert renderer._draw_hook is UnboundHookScreen.draw
    assert renderer._mark_hook is UnboundHookScreen.mark_presented
    assert screen.draws == 1
    assert screen.partial_draws == 1
    assert screen.marks == 2

    renderer.invalidate()
    assert renderer.present(screen) is True
    assert renderer._hook_screen is screen


def test_sidebar_is_polled_only_by_the_scheduler_then_drawn_from_that_sample():
    display = DisplaySpy()
    sidebar = SidebarSpy()
    screen = PartialScreen()
    renderer = Renderer(display, sidebar)

    renderer.present(screen)
    assert renderer.poll_sidebar() is True
    renderer.present(screen)

    assert sidebar.refresh_checks == 1
    # The first frame and the repaint after a scheduler poll both reuse the
    # cached voltage.  ADC work occurs in refresh_needed(), never in draw().
    assert sidebar.refreshes == [False, False]
