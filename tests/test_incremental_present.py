from ui.renderer import Renderer


class DisplaySpy:
    height = 64

    def __init__(self):
        self.clears = 0
        self.content_clears = 0
        self.full_presents = 0
        self.row_presents = []

    def clear_buffers(self, color=0):
        self.clears += 1

    def fill_rectangle(self, *args):
        self.content_clears += 1

    def present(self):
        self.full_presents += 1

    def present_rows(self, rows):
        self.row_presents.append(rows)


class SidebarSpy:
    def __init__(self):
        self.draws = 0
        self.refresh_checks = 0

    def refresh_needed(self):
        self.refresh_checks += 1
        return True

    def draw(self, display, refresh=True):
        self.draws += 1
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

    def get_present_rows(self):
        return ((15, 21),) if self.marks else None

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
