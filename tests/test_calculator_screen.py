from calc.functions import build_registry
from screens.calculator import CalculatorScreen


class KeyboardStub:
    def is_pressed(self, row, col):
        return False

    def get_hold_time(self, row, col):
        return 0

    def consume_long_press(self, row, col, threshold):
        return False


def test_calculator_consumes_supplied_event_and_records_result():
    keyboard = KeyboardStub()
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.activate()

    for event in ((3, 1, False), (1, 3, False), (3, 2, False), (3, 3, False)):
        screen.update(keyboard, event)

    assert screen.history == [("2+3", 5.0)]
    assert screen.input_box.get_str() == ""


def test_assignment_marks_context_for_persistence():
    screen = CalculatorScreen(None, registry=build_registry(), variables={})
    screen.input_box.set_str("x=7")

    screen.update(KeyboardStub(), (3, 3, False))

    assert screen.vars == {"x": 7.0}
    assert screen.context.consume_dirty() is True
