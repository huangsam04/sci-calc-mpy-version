"""Low-memory real-device acceptance benchmark for interactive UI frames."""
import gc
import sys
import time


if "/sd" not in sys.path:
    sys.path.insert(0, "/sd")


SAMPLE_COUNT = 8


def _summary(values):
    total = 0
    maximum = 0
    for value in values:
        total += value
        if value > maximum:
            maximum = value
    return total // max(1, len(values)), maximum


def _frame(renderer, screen):
    started = time.ticks_us()
    renderer.present(screen)
    return (time.ticks_diff(time.ticks_us(), started),
            renderer.last_present_us)


def run(emit=print):
    gc.collect()
    from anim.engine import (animate_all, cancel_all_animations,
                             has_active_animations)
    from display.xglcd_font import XglcdFont
    from main import Nav, _init_display, _needs_render
    from screens.calculator import CalculatorScreen
    from screens.main_menu import MainMenu
    from ui.renderer import Renderer
    from ui.sidebar import Sidebar

    display = _init_display()
    font = XglcdFont("/sd/fonts/Bally7x9.xglcd", 7, 9)
    for char in "0123456789/":
        font.get_letter(char)
    registry = type("Registry", (), {"angle_mode": 0})()
    sidebar = Sidebar(font, registry)
    renderer = Renderer(display, sidebar)

    menu = MainMenu(font)
    for label in ("Calculator", "Plot", "Function Panel", "Stopwatch",
                  "Settings"):
        menu.add_screen(label, None)
    menu.activate()
    _frame(renderer, menu)
    menu.menu.move_cursor_down()
    menu_frames = []
    menu_terminal_frames = 0
    menu_last_render = time.ticks_ms()
    for _ in range(SAMPLE_COUNT + 2):
        time.sleep_ms(16)
        was_active = has_active_animations()
        animate_all()
        active = has_active_animations()
        animation_finished = was_active and not active
        now = time.ticks_ms()
        if _needs_render(now, menu_last_render, active, False, False,
                         False, animation_finished):
            menu_frames.append(_frame(renderer, menu))
            menu_terminal_frames += int(animation_finished)
            menu_last_render = now
        if not active and not animation_finished:
            break

    class HeldDownKeyboard:
        def __init__(self):
            self.hold_ms = 0

        def is_pressed(self, row, col):
            return (row, col) == (3, 1)

        def get_hold_time(self, row, col):
            return self.hold_ms if (row, col) == (3, 1) else 0

    hold_menu = MainMenu(font)
    for label in ("A", "B", "C", "D"):
        hold_menu.add_screen(label, None)
    hold_menu.activate()
    held_keyboard = HeldDownKeyboard()
    hold_menu.update(held_keyboard, (3, 1, False))
    hold_first = hold_menu.menu.cursor_pos
    held_keyboard.hold_ms = 400
    hold_menu.update(held_keyboard, None)
    hold_second = hold_menu.menu.cursor_pos
    held_keyboard.hold_ms = 500
    hold_menu.update(held_keyboard, None)
    hold_third = hold_menu.menu.cursor_pos
    if (hold_first, hold_second, hold_third) != (1, 2, 3):
        raise AssertionError("held menu repeat failed")

    rapid_menu = MainMenu(font)
    for label in ("A", "B", "C"):
        rapid_menu.add_screen(label, None)
    rapid_menu.activate()
    rapid_keyboard = HeldDownKeyboard()
    rapid_menu.update(rapid_keyboard, (3, 1, False))
    rapid_menu.update(rapid_keyboard, (3, 1, False))
    rapid_position = rapid_menu.menu.cursor_pos
    if rapid_position != 2:
        raise AssertionError("rapid menu retarget failed")
    cancel_all_animations()

    class ProbeScreen:
        def activate(self):
            pass

        def activate_default(self):
            pass

        def draw(self, target):
            target.fill_rectangle(0, 0, 210, 64, 0)

        def draw_transition_default(self, target):
            self.draw(target)

    class QueuedKeyboard:
        def __init__(self):
            self.events = [
                (1, 1, False),
                (3, 1, False),
                (3, 1, False),
            ]
            self.pops = 0

        def pop_key_event(self):
            self.pops += 1
            return self.events.pop(0) if self.events else None

        def pop_key_event_at(self, row, col):
            for index, event in enumerate(self.events):
                if (event[0], event[1]) == (row, col):
                    self.pops += 1
                    return self.events.pop(index)
            return None

        def is_pressed(self, row, col):
            return False

        def any_pressed(self):
            return False

    nav = Nav(display, font, registry)
    nav.boot(ProbeScreen())
    nav.present_current()
    nav.enable_optional_resources()
    queued = QueuedKeyboard()
    nav.go_to(ProbeScreen(), (3, 3, False))
    if nav.poll_event(queued) is not None or queued.pops:
        raise AssertionError("transition consumed queued input")
    while nav.is_transitioning():
        nav.draw_transition(time.ticks_ms())
        if nav.is_transitioning():
            time.sleep_ms(16)
    while nav.settle_current():
        pass
    replayed = (
        nav.poll_event(queued),
        nav.poll_event(queued),
        nav.poll_event(queued),
    )
    if replayed != (
            (1, 1, False), (3, 1, False), (3, 1, False)):
        raise AssertionError("transition input replay failed")
    nav_replayed = len(replayed)
    del nav, queued, replayed, ProbeScreen, QueuedKeyboard
    gc.collect()

    calc = CalculatorScreen(font, registry=registry, variables={})
    calc.activate()
    _frame(renderer, calc)
    gc.collect()
    gc.disable()
    input_frames = []
    for char in "12345678":
        calc.input_box.insert_str(char)
        input_frames.append(_frame(renderer, calc))
    gc.enable()

    menu_totals = [frame[0] for frame in menu_frames]
    menu_presents = [frame[1] for frame in menu_frames]
    menu_draws = [frame[0] - frame[1] for frame in menu_frames]
    input_totals = [frame[0] for frame in input_frames]
    input_presents = [frame[1] for frame in input_frames]
    input_draws = [frame[0] - frame[1] for frame in input_frames]
    menu_avg, menu_max = _summary(menu_totals)
    input_avg, input_max = _summary(input_totals)
    reporter = getattr(gc, "mem_free", None)
    heap_free = reporter() if reporter is not None else -1
    emit("INTERACTION_ACCEPTANCE menu_avg_us=" + str(menu_avg)
         + " menu_max_us=" + str(menu_max)
         + " input_avg_us=" + str(input_avg)
         + " input_max_us=" + str(input_max)
         + " menu_us=" + ",".join(str(value) for value in menu_totals)
         + " menu_draw_us=" + ",".join(str(value) for value in menu_draws)
         + " menu_present_us=" + ",".join(str(value) for value in menu_presents)
         + " menu_frames=" + str(len(menu_frames))
         + " menu_terminal_frames=" + str(menu_terminal_frames)
         + " menu_hold_positions=" + str(hold_first) + ","
         + str(hold_second) + "," + str(hold_third)
         + " menu_rapid_position=" + str(rapid_position)
         + " nav_replayed=" + str(nav_replayed)
         + " input_us=" + ",".join(str(value) for value in input_totals)
         + " input_draw_us=" + ",".join(str(value) for value in input_draws)
         + " input_present_us=" + ",".join(str(value) for value in input_presents)
         + " heap_free=" + str(heap_free))
    cancel_all_animations()


if __name__ == "__main__":
    run()
