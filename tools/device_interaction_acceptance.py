"""Five-round captured-edge-to-OLED tracer for the resident application."""
import gc
import time

from ui.element import SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW


TOTAL_ROUNDS = 5
MAX_INPUT_FRAME_US = 20_000
MAX_BLOCKING_STEP_US = 40_000
MAX_HEAP_DRIFT_BYTES = 512
MAX_MOTION_FRAMES = 16
MENU_FRAME_MS = 16
PAGE_FRAME_MS = 14

_MENU_DOWN = ((3, 1, False),)
_MENU_UP = ((1, 1, False),)
_PAGE_TRIGGER = (3, 3, False)
_DIGITS = (
    (3, 0, False), (3, 1, False), (3, 2, False),
    (2, 0, False), (2, 1, False),
)


class QueuedKeyboard:
    __slots__ = ("events", "index")

    def __init__(self):
        self.events = _MENU_DOWN
        self.index = 0

    def reset(self, events):
        self.events = events
        self.index = 0
        return self

    def pop_key_event(self):
        index = self.index
        if index >= len(self.events):
            return None
        self.index = index + 1
        return self.events[index]

    def is_pressed(self, row, col):
        return False

    def get_hold_time(self, row, col):
        return 0

    def consume_long_press(self, row, col, threshold):
        return False


class _Dispatch:
    __slots__ = ("keyboard", "screen")

    def __init__(self, keyboard):
        self.keyboard = keyboard
        self.screen = None

    def update(self, event):
        return self.screen.update(self.keyboard, event)


def _record(stats, started, edge, phase):
    elapsed = time.ticks_diff(time.ticks_us(), started)
    _record_elapsed(stats, elapsed, edge, phase)
    return elapsed


def _record_elapsed(stats, elapsed, edge, phase):
    stats[3] += 1
    if elapsed > stats[4]:
        stats[4] = elapsed
        stats[6] = phase
    if edge and elapsed > stats[5]:
        stats[5] = elapsed
        stats[7] = phase


def _record_motion(stats, elapsed, phase, allocation_delta):
    _record_elapsed(stats, elapsed, False, phase)
    stats[13] += 1
    if elapsed > stats[14]:
        stats[14] = elapsed
        stats[15] = phase
    if allocation_delta:
        stats[16] += 1
        stats[17] += allocation_delta
        slot = 18 + (phase - 7) * 2
        stats[slot] += 1
        stats[slot + 1] += allocation_delta


def _motion_allocation_delta(mem_alloc, before):
    return mem_alloc() - before if mem_alloc is not None else 0


def _drive_menu_motion(nav, screen, stats, phase):
    advance = getattr(type(screen), "advance_motion", None)
    if advance is None:
        return
    mem_alloc = getattr(gc, "mem_alloc", None)
    if not callable(mem_alloc):
        mem_alloc = None
    frames = 0
    while getattr(screen, "motion_active", False):
        if frames >= MAX_MOTION_FRAMES:
            raise RuntimeError("Menu animation exceeded its frame bound")
        time.sleep_ms(MENU_FRAME_MS)
        before = mem_alloc() if mem_alloc is not None else 0
        started = time.ticks_us()
        advance(screen, time.ticks_ms())
        nav.present_current()
        elapsed = time.ticks_diff(time.ticks_us(), started)
        _record_motion(
            stats, elapsed, phase,
            _motion_allocation_delta(mem_alloc, before))
        frames += 1


def _drive_page_motion(nav, stats, phase):
    mem_alloc = getattr(gc, "mem_alloc", None)
    if not callable(mem_alloc):
        mem_alloc = None
    frames = 0
    while getattr(nav, "motion_active", False):
        if frames >= MAX_MOTION_FRAMES:
            raise RuntimeError("Page fade exceeded its frame bound")
        time.sleep_ms(PAGE_FRAME_MS)
        before = mem_alloc() if mem_alloc is not None else 0
        started = time.ticks_us()
        nav.present_current()
        elapsed = time.ticks_diff(time.ticks_us(), started)
        _record_motion(
            stats, elapsed, phase,
            _motion_allocation_delta(mem_alloc, before))
        frames += 1


def _settle(nav, stats, phase):
    redraw = False
    for _ in range(256):
        started = time.ticks_us()
        flags = nav.settle_current()
        if flags & SETTLE_COLLECT:
            gc.collect()
        if flags & SETTLE_REDRAW:
            redraw = True
        if not flags & SETTLE_MORE and redraw:
            nav.present_current()
        _record(stats, started, False, phase)
        if not flags & SETTLE_MORE:
            return
    raise RuntimeError("Interaction settle exceeded its bound")


def _free():
    reporter = getattr(gc, "mem_free", None)
    return reporter() if reporter is not None else -1


def _exercise_round(
        nav, root, keyboard, dispatch, handler, drain, stats,
        round_index, saved_input):
    menu = root.menu
    cursor_before = menu.cursor_pos
    events = _MENU_DOWN if cursor_before == 0 else _MENU_UP
    dispatch.keyboard = keyboard.reset(events)
    dispatch.screen = root
    started = time.ticks_us()
    if drain(nav, keyboard, handler) != 1:
        raise RuntimeError("Menu edge was lost")
    if menu.cursor_pos == cursor_before:
        raise RuntimeError("Menu edge did not move")
    nav.present_current()
    _record(stats, started, True, 1)
    _drive_menu_motion(nav, root, stats, 7)
    _settle(nav, stats, 2)

    started = time.ticks_us()
    calculator = nav.open(1, _PAGE_TRIGGER)
    calculator.input_box.clear_str()
    nav.present_current()
    _record(stats, started, True, 3)
    _drive_page_motion(nav, stats, 8)

    dispatch.keyboard = keyboard.reset(_DIGITS)
    dispatch.screen = calculator
    started = time.ticks_us()
    if drain(nav, keyboard, handler) != 3:
        raise RuntimeError("Calculator edges were lost")
    if calculator.input_box.get_str() != "123":
        raise RuntimeError("Calculator edge value is wrong")
    nav.present_current()
    first_batch_us = _record(stats, started, True, 4)

    started = time.ticks_us()
    if drain(nav, keyboard, handler) != 2:
        raise RuntimeError("Calculator queued edges were lost")
    if calculator.input_box.get_str() != "12345":
        raise RuntimeError("Calculator queued edge value is wrong")
    nav.present_current()
    second_batch_us = _record(stats, started, True, 4)
    stats[8 + round_index] = max(first_batch_us, second_batch_us)
    _settle(nav, stats, 5)

    started = time.ticks_us()
    calculator.input_box.set_str(saved_input, immediate=True)
    nav.back(_PAGE_TRIGGER)
    nav.present_current()
    _record(stats, started, True, 6)
    _drive_page_motion(nav, stats, 9)
    collector = getattr(nav, "collect_pending", None)
    if collector is not None:
        collector()
    dispatch.screen = root
    stats[2] = round_index + 1


def run(runtime=None, emit=print):
    from input.keyboard import DEBOUNCE_MS, SCAN_INTERVAL
    from main import _drain_input_batch

    if runtime is None:
        from runtime_materialize import get_resident_runtime

        runtime = get_resident_runtime()
    if runtime is None or getattr(runtime, "mode", None) != "resident":
        raise RuntimeError("Release mode requires a resident runtime")
    nav = runtime.nav
    root = runtime.root
    menu = root.menu
    if not menu._state[5]:
        raise RuntimeError("Resident interaction state is unavailable")

    if nav.current is not root:
        nav.reset(root)
    calculator = nav.open(1)
    if getattr(calculator, "mode", None) != 0:
        nav.back()
        raise RuntimeError("Resident interaction state is unavailable")
    saved_cursor = menu.cursor_pos
    saved_offset = menu.view_offset
    saved_input = calculator.input_box.get_str()
    nav.back()
    nav.collect_pending()
    calculator = None
    keyboard = QueuedKeyboard()
    dispatch = _Dispatch(keyboard)
    handler = dispatch.update
    # Memory errors, other errors, rounds, steps, blocking max, edge max,
    # blocking phase, edge phase, five Calculator edge samples, animation
    # frames/max/phase, and animation allocation count/sum.
    stats = [0] * 24
    gc.collect()
    heap_before = _free()
    emit("INTERACTION_SCREEN_TRACER_START mode=resident rounds=5"
         + " coverage=captured_edge_to_screen_update_present"
         + " oled=awake"
         + " scan_interval_us=" + str(SCAN_INTERVAL * 1000)
         + " debounce_us=" + str(DEBOUNCE_MS * 1000)
         + " heap_before=" + str(heap_before))

    display = nav.renderer.display
    display.wake()
    try:
        for round_index in range(TOTAL_ROUNDS):
            _exercise_round(
                nav, root, keyboard, dispatch, handler,
                _drain_input_batch, stats, round_index, saved_input)
    except MemoryError:
        stats[0] += 1
    except Exception:
        stats[1] += 1
    finally:
        try:
            current = dispatch.screen
            if current is not None and current is not root:
                input_box = getattr(current, "input_box", None)
                if input_box is not None:
                    input_box.set_str(saved_input, immediate=True)
            menu.cursor_pos = saved_cursor
            menu.view_offset = saved_offset
            if nav.current is not root:
                nav.reset(root)
            else:
                cancel_motion = getattr(nav, "cancel_motion", None)
                if cancel_motion is not None:
                    cancel_motion()
            root.activate()
            nav.present_current()
        except MemoryError:
            stats[0] += 1
        except Exception:
            stats[1] += 1
        finally:
            display.sleep()

    gc.collect()
    heap_after = _free()
    heap_delta = (
        heap_after - heap_before
        if heap_before >= 0 and heap_after >= 0 else -1)
    failure_mask = 0
    if stats[0]:
        failure_mask |= 1
    if stats[1]:
        failure_mask |= 2
    if stats[4] >= MAX_BLOCKING_STEP_US:
        failure_mask |= 4
    if stats[5] >= MAX_INPUT_FRAME_US:
        failure_mask |= 8
    if heap_delta != -1 and abs(heap_delta) > MAX_HEAP_DRIFT_BYTES:
        failure_mask |= 16
    if stats[2] != TOTAL_ROUNDS:
        failure_mask |= 32
    if stats[14] >= MAX_BLOCKING_STEP_US:
        failure_mask |= 64
    if not stats[13]:
        failure_mask |= 128
    if stats[16]:
        failure_mask |= 256

    emit("INTERACTION_SCREEN_TRACER_END rounds_completed=" + str(stats[2])
         + " runtime_steps=" + str(stats[3])
         + " memory_errors=" + str(stats[0])
         + " errors=" + str(stats[1])
         + " edge_to_present_max_us=" + str(stats[5])
         + " edge_phase=" + str(stats[7])
         + " blocking_max_us=" + str(stats[4])
         + " blocking_phase=" + str(stats[6])
         + " animation_frames=" + str(stats[13])
         + " animation_max_us=" + str(stats[14])
         + " animation_phase=" + str(stats[15])
         + " animation_alloc_nonzero=" + str(stats[16])
         + " animation_alloc_delta=" + str(stats[17])
         + " animation_menu_alloc=" + str(stats[18]) + ":" + str(stats[19])
         + " animation_open_alloc=" + str(stats[20]) + ":" + str(stats[21])
         + " animation_back_alloc=" + str(stats[22]) + ":" + str(stats[23])
         + " heap_after=" + str(heap_after)
         + " heap_delta=" + str(heap_delta)
         + " calc_edge_round_us=" + str(stats[8]) + ","
         + str(stats[9]) + "," + str(stats[10]) + ","
         + str(stats[11]) + "," + str(stats[12]))
    emit("INTERACTION_SCREEN_TRACER_RESULT "
         + ("PASS" if failure_mask == 0 else "FAIL")
         + " failure_mask=" + str(failure_mask))
    if failure_mask:
        raise RuntimeError("Device interaction screen tracer failed")
    return stats


if __name__ == "__main__":
    run()
