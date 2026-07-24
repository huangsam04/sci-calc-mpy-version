"""Five-round real-device acceptance for menu and calculator input."""
import gc
import sys
import time


if "/sd" not in sys.path:
    sys.path.insert(0, "/sd")


SAMPLE_COUNT = 5
MAX_FRAME_US = 32_000
MIN_HEAP_FREE_BYTES = 8 * 1024


def _frame(renderer, screen):
    started = time.ticks_us()
    renderer.present(screen)
    return time.ticks_diff(time.ticks_us(), started)


class QueuedKeyboard:
    def __init__(self, events):
        self.events = list(events)

    def pop_key_event(self):
        return self.events.pop(0) if self.events else None

    def any_pressed(self):
        return False

    def is_pressed(self, row, col):
        return False

    def get_hold_time(self, row, col):
        return 0

    def consume_long_press(self, row, col, threshold):
        return False


def run(emit=print):
    gc.collect()
    from main import _drain_input_batch
    from performance import metrics

    runtime = metrics.runtime()
    if runtime is None:
        raise RuntimeError("SCI-CALC runtime is unavailable")
    nav, root, targets = runtime
    calculator = None
    for target in targets:
        if getattr(target, "transition_title", "") == "Calculator":
            calculator = target
            break
    if calculator is None or not hasattr(root, "menu"):
        raise RuntimeError("Resident menu or Calculator screen is unavailable")
    if nav.current is not root:
        nav.reset(root)

    menu = root.menu
    if len(menu.items) < SAMPLE_COUNT:
        raise RuntimeError("Main menu has fewer than five entries")
    saved_cursor = menu.cursor_pos
    saved_offset = menu.view_offset
    saved_input = calculator.input_box.get_str()
    saved_mode = calculator.mode

    menu.cursor_pos = 0
    menu.view_offset = 0
    root.activate()
    nav.renderer.invalidate()
    _frame(nav.renderer, root)
    moves = ((3, 1, False), (3, 1, False), (3, 1, False),
             (3, 1, False), (1, 1, False))
    menu_frames = []
    for event in moves:
        root.update(QueuedKeyboard(()), event)
        menu_frames.append(_frame(nav.renderer, root))
    if len(menu_frames) != SAMPLE_COUNT:
        raise AssertionError("menu sample count changed")

    menu.cursor_pos = 0
    menu.view_offset = 0
    menu.activate()
    rapid_keyboard = QueuedKeyboard(moves)
    rapid_handled = _drain_input_batch(
        nav, rapid_keyboard,
        lambda event: root.update(rapid_keyboard, event))
    if rapid_handled != SAMPLE_COUNT or menu.cursor_pos != 3:
        raise AssertionError("five rapid menu presses were not applied")

    calculator.input_box.clear_str()
    nav.go_to(calculator)
    nav.present_current()
    digit_events = [
        (3, 0, False),
        (3, 1, False),
        (3, 2, False),
        (2, 0, False),
        (2, 1, False),
    ]
    input_keyboard = QueuedKeyboard(digit_events)
    handled = _drain_input_batch(
        nav, input_keyboard,
        lambda event: calculator.update(input_keyboard, event))
    if handled != SAMPLE_COUNT:
        raise AssertionError("input batch did not handle five edges")
    if calculator.input_box.get_str() != "12345":
        raise AssertionError("five-digit batch was lost")
    input_frame = _frame(nav.renderer, calculator)

    frame_max = max(max(menu_frames), input_frame)
    heap_free = gc.mem_free()
    emit("INTERACTION_ACCEPTANCE rounds=" + str(SAMPLE_COUNT)
         + " menu_us=" + ",".join(
             str(value) for value in menu_frames)
         + " input_batch_us=" + str(input_frame)
         + " frame_max_us=" + str(frame_max)
         + " heap_free=" + str(heap_free))
    if frame_max > MAX_FRAME_US:
        raise RuntimeError("Interactive frame exceeded deadline")
    if heap_free < MIN_HEAP_FREE_BYTES:
        raise RuntimeError("Interactive heap headroom is too low")

    nav.go_back()
    calculator.input_box.set_str(saved_input, immediate=True)
    calculator.mode = saved_mode
    menu.cursor_pos = saved_cursor
    menu.view_offset = saved_offset
    root.activate()
    nav.renderer.invalidate()
    nav.present_current()


if __name__ == "__main__":
    run()
