import sys
import types

import pytest

from screens import function_picker as function_picker_module
from screens.function_picker import FunctionPicker


class FontStub:
    width = 5
    height = 7

    def measure_text(self, text, spacing=1):
        return len(text) * (self.width + spacing)


class InputStub:
    def __init__(self, accepts=True):
        self.accepts = accepts
        self.values = []

    def try_insert(self, value):
        self.values.append(value)
        return self.accepts


class CalculatorStub:
    def __init__(self, count=32, accepts=True, names=None):
        if names is None:
            names = ["function_" + str(index) for index in range(count)]
        registry = {name: (None, None, "prefix") for name in names}
        self.context = type("Context", (), {"registry": registry})()
        self.input_box = InputStub(accepts)


class LowMemoryDisplay:
    """Simulate the bounded string-frame cache available on the device."""

    def __init__(self, dynamic_limit=8):
        self.dynamic_limit = dynamic_limit
        self.dynamic_draws = 0
        self.direct_draws = 0
        self.direct_text = []
        self.fills = []

    def draw_text(self, x, y, text, font, **kwargs):
        if 15 <= y < 54:
            self.dynamic_draws += 1
            if self.dynamic_draws > self.dynamic_limit:
                raise MemoryError("function label cache exhausted")

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct_draws += 1
        self.direct_text.append((x, y, text, gs))

    def draw_text8x8(self, x, y, text, gs=15):
        self.direct_draws += 1
        self.direct_text.append((x, y, text, gs))

    def draw_hline(self, *args):
        pass

    def draw_rectangle(self, *args):
        pass

    def fill_rectangle(self, *args):
        self.fills.append(args)


class ClippedDisplay(LowMemoryDisplay):
    def __init__(self):
        super().__init__()
        self.content_draws = []

    def begin_content_draw(self, x_offset, clip_x0, clip_x1):
        self.content_draws.append((x_offset, clip_x0, clip_x1))

    def end_content_draw(self):
        pass


def test_target_lazily_imports_function_picker_scenario_transaction(
        monkeypatch):
    class LazyTransaction:
        def __init__(self, picker):
            self.picker = picker

    lazy_module = types.ModuleType("screens.function_picker_scenario")
    lazy_module.FunctionPickerScenarioTransaction = LazyTransaction
    monkeypatch.setitem(
        sys.modules, "screens.function_picker_scenario", lazy_module)
    picker = FunctionPicker(None, CalculatorStub(count=1))

    transaction = picker.open_scenario_transaction()

    assert type(transaction) is LazyTransaction
    assert transaction.picker is picker


def test_maximum_function_picker_scenario_finishes_within_thirteen_steps():
    picker = FunctionPicker(None, CalculatorStub(count=192))
    transaction = picker.open_scenario_transaction()

    complete = False
    for _ in range(13):
        complete = transaction.step()
        if complete:
            break

    assert complete is True
    assert picker._state[1] == sorted(picker._state[1])
    assert transaction.close() is True


def test_distinct_rapid_right_edges_are_never_throttled():
    picker = FunctionPicker(FontStub(), CalculatorStub())
    picker.activate()

    for _ in range(3):
        picker.update(None, (2, 2, False))

    assert picker._state[2] == 12


def test_repeated_right_page_changes_do_not_grow_string_frame_cache():
    picker = FunctionPicker(FontStub(), CalculatorStub())
    display = LowMemoryDisplay()
    picker.activate()

    for _ in range(5):
        picker.draw(display)
        picker.update(None, (2, 2, False))

    assert display.dynamic_draws == 0
    assert display.direct_draws > 0


def test_picker_selection_moves_with_nonlinear_cursor_animation(monkeypatch):
    clock = [100]
    monkeypatch.setattr(function_picker_module.time, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        function_picker_module.time, "ticks_diff",
        lambda newer, older: newer - older)
    picker = FunctionPicker(FontStub(), CalculatorStub())
    picker.activate()

    assert picker.update(None, (3, 1, False)) == "REDRAW"
    assert picker.motion_active is True
    display = LowMemoryDisplay()
    picker.draw(display)
    first_y = [item[1] for item in display.fills if item[3] == 8][-1]
    assert 15 < first_y < 25

    clock[0] = 148
    assert picker.advance_motion(clock[0]) is True
    display = LowMemoryDisplay()
    picker.draw(display)
    middle_y = [item[1] for item in display.fills if item[3] == 8][-1]
    assert first_y < middle_y < 25

    clock[0] = 196
    assert picker.advance_motion(clock[0]) is True
    assert picker.motion_active is False
    assert all(isinstance(value, int) for value in picker._state[6:8])


def test_picker_page_change_slides_old_and_new_pages_horizontally(monkeypatch):
    clock = [100]
    monkeypatch.setattr(function_picker_module.time, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        function_picker_module.time, "ticks_diff",
        lambda newer, older: newer - older)
    picker = FunctionPicker(FontStub(), CalculatorStub(count=16))
    picker.activate()
    picker._state[2] = 4
    picker._state[3] = 0

    assert picker.update(None, (2, 2, False)) == "REDRAW"
    assert picker._state[2] == 8
    assert picker.motion_active is True
    clock[0] = 148
    assert picker.advance_motion(clock[0]) is True
    display = LowMemoryDisplay()
    picker.draw(display)
    page_x = [x for x, y, _text, _gs in display.direct_text
              if 15 <= y < 54]

    assert any(x < 4 for x in page_x)
    assert any(x > 105 for x in page_x)

    clipped = ClippedDisplay()
    picker.draw(clipped)
    assert len(clipped.content_draws) == 2
    assert all(item[1:] == (0, 210) for item in clipped.content_draws)
    assert clipped.content_draws[0][0] < 0
    assert clipped.content_draws[1][0] > 0


def test_picker_partial_page_above_255_never_draws_empty_cache_slots(
        monkeypatch):
    clock = [100]
    monkeypatch.setattr(function_picker_module.time, "ticks_ms", lambda: clock[0])
    picker = FunctionPicker(FontStub(), CalculatorStub(count=258))
    picker.activate()
    picker._state[2] = 256
    picker._state[3] = 256
    picker._cache_labels(256, 0)
    picker._snap_motion()

    assert picker.update(None, (2, 0, False)) == "REDRAW"
    display = LowMemoryDisplay(dynamic_limit=16)
    picker.draw(display)

    page_labels = [text for _x, y, text, _gs in display.direct_text
                   if 15 <= y < 54]
    assert page_labels
    assert all(label is not None for label in page_labels)


def test_picker_animation_reuses_visible_labels_and_footer(monkeypatch):
    clock = [100]
    monkeypatch.setattr(function_picker_module.time, "ticks_ms", lambda: clock[0])
    monkeypatch.setattr(
        function_picker_module.time, "ticks_diff",
        lambda newer, older: newer - older)
    names = [
        "long_function_label_" + str(index)
        for index in range(16)
    ]
    picker = FunctionPicker(FontStub(), CalculatorStub(names=names))
    picker.activate()
    picker._state[2] = 4
    picker._state[3] = 0
    assert picker.update(None, (2, 2, False)) == "REDRAW"
    clock[0] = 148
    assert picker.advance_motion(clock[0]) is True
    footer_right = []

    def draw_footer(_display, _hint, _hint_bytes, _font, right):
        footer_right.append(right)

    monkeypatch.setattr(
        function_picker_module._theme, "draw_footer_fast", draw_footer)
    first = LowMemoryDisplay()
    second = LowMemoryDisplay()
    picker.draw(first)
    picker.draw(second)
    first_labels = [text for _x, y, text, _gs in first.direct_text
                    if 15 <= y < 54]
    second_labels = [text for _x, y, text, _gs in second.direct_text
                     if 15 <= y < 54]

    assert len(first_labels) == len(second_labels) == 16
    assert all(len(label) <= 12 for label in first_labels)
    assert all(left is right
               for left, right in zip(first_labels, second_labels))
    assert footer_right[0] is footer_right[1]


def test_picker_draws_only_the_visible_bounded_page_with_direct_text():
    names = [
        "long_function_label_" + str(index) + "_with_more_text"
        for index in range(16)
    ]
    picker = FunctionPicker(FontStub(), CalculatorStub(names=names))
    display = LowMemoryDisplay()
    picker.activate()

    picker.draw(display)
    labels = [text for _x, y, text, _gs in display.direct_text
              if 15 <= y < 54]

    assert len(picker._state[1]) == 16
    assert len(labels) == 8
    assert all(label is not None and len(label) <= 24 for label in labels)
    assert any(text == "Functions"
               for _x, _y, text, _gs in display.direct_text)

    picker.draw(display)
    assert display.dynamic_draws == 0

    picker._state[2] = 8
    picker.draw(display)
    page = [text for _x, y, text, _gs in display.direct_text[-12:]
            if 15 <= y < 54]
    assert picker._state[1][8][:12] in page


def test_picker_keeps_its_selection_open_when_the_target_input_is_full():
    calc = CalculatorStub(count=1, accepts=False)
    picker = FunctionPicker(FontStub(), calc)
    picker.activate()

    assert picker.update(None, (3, 3, False)) == "REDRAW"
    assert picker._state[4] == "Input full"
    assert calc.input_box.values == ["function_0("]


def test_picker_ignores_repeated_full_insert_after_the_notice_is_visible():
    calc = CalculatorStub(count=1, accepts=False)
    picker = FunctionPicker(FontStub(), calc)
    picker.activate()

    assert picker.update(None, (3, 3, False)) == "REDRAW"
    assert picker.update(None, (3, 3, False)) is None
    assert picker._state[4] == "Input full"
    assert calc.input_box.values == ["function_0(", "function_0("]


@pytest.mark.parametrize(
    "event",
    ((3, 3, False), (4, 3, False), (0, 0, False)),
    ids=("ent", "del", "esc"),
)
def test_picker_active_lease_ignores_input_and_navigation_events(event):
    calc = CalculatorStub(names=[
        "function_" + str(index) for index in range(191, -1, -1)
    ])
    picker = FunctionPicker(FontStub(), calc)
    transaction = picker.open_scenario_transaction()
    assert transaction.step() is False
    picker._state[2] = 0
    picker._state[3] = 0
    picker._state[4] = "lease notice"
    names = picker._state[1]
    state = (picker._state[2], picker._state[3], picker._state[4])
    registry_before = dict(calc.context.registry)

    assert picker.update(None, event) is None

    assert (picker._state[2], picker._state[3], picker._state[4]) == state
    assert picker._state[1] is names
    assert calc.input_box.values == []
    assert calc.context.registry == registry_before
    assert picker._state[5] is transaction
    assert transaction.close() is True
    assert picker.update(None, (0, 0, False)) == "FUNC_PICKER_DONE"


def test_picker_propagates_memory_error_from_target_insertion():
    calc = CalculatorStub(count=1)
    picker = FunctionPicker(FontStub(), calc)
    picker.activate()

    def exhaust_heap(value):
        raise MemoryError("injected insertion failure")

    calc.input_box.try_insert = exhaust_heap

    with pytest.raises(MemoryError, match="injected insertion failure"):
        picker.update(None, (3, 3, False))


def test_picker_keeps_and_refills_its_boot_catalog_in_place(monkeypatch):
    picker = FunctionPicker(FontStub(), CalculatorStub(count=3))
    old_names = picker._state[1]
    picker._state[4] = "Input full"

    assert picker.release_memory() is True
    assert picker._state[1] == ["function_0", "function_1", "function_2"]
    assert not hasattr(picker, "_registry_revision")

    def late_snapshot_forbidden(*_args, **_kwargs):
        raise MemoryError("late function catalog")

    monkeypatch.setattr(
        function_picker_module, "sorted", late_snapshot_forbidden,
        raising=False)
    picker.activate()
    assert picker._state[1] == ["function_0", "function_1", "function_2"]
    assert picker._state[1] is old_names
