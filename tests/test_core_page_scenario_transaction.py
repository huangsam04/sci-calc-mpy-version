import pytest

from calc.functions import build_registry
from screens import calculator as calculator_module
from screens import plot as plot_module
from screens.calculator import CalculatorScreen
from screens.plot import PlotScreen


def _calculator():
    return CalculatorScreen(None, registry=build_registry(), variables={})


def _plot():
    return PlotScreen(None, registry=build_registry())


def _popup_state(screen):
    popup = (screen._state[1]
             if isinstance(screen, CalculatorScreen) else screen.error_popup)
    return (
        popup.expr,
        popup._state[2],
        popup.title,
        popup.detail,
        popup._state[3],
        popup.active,
    )


def test_calculator_page_scenario_prepares_only_normal_activation_state():
    screen = _calculator()
    context = screen.context
    screen.mode = 1
    screen._state[3][0][1] = "retained notice"
    screen.input_box.deactivate()
    screen._state[1].show_static("Retained error", "Retained detail")
    popup_state = _popup_state(screen)

    lease = screen.open_page_scenario_transaction()

    with pytest.raises(RuntimeError, match="already open"):
        screen.open_page_scenario_transaction()
    with pytest.raises(RuntimeError, match="already open"):
        screen.open_scenario_transaction()

    assert lease.step() is True
    assert screen.mode == 0
    assert screen._state[3][0][1] == ""
    assert screen.input_box.cursor.is_visible is True
    assert screen.context is context
    assert _popup_state(screen) == popup_state

    with pytest.raises(RuntimeError, match="prepared"):
        lease.step()

    assert lease.close() is True
    assert lease.close() is True
    assert screen._state[3][3] is None
    assert screen.mode == 1
    assert screen._state[3][0][1] == "retained notice"
    assert screen.input_box.cursor.is_visible is False
    assert screen.context is context
    assert _popup_state(screen) == popup_state


def test_plot_page_scenario_restores_editor_and_deferred_plot_intent():
    screen = _plot()
    context = screen._state[3][3][2][3]
    program = object()
    curve_buffer = bytearray(8)
    curve_job = object()
    screen.expr = "x^2"
    screen._state[0][0] = -3.0
    screen._state[0][1] = 7.0
    screen._state[0][2] = -2.0
    screen._state[0][3] = 8.0
    screen._state[1][3] = 1
    screen._state[3][3][0][3] = 0
    screen.input_box.set_str("")
    screen.input_box.cursor.is_visible = False
    screen._state[2][2] = False
    screen._state[2][3] = True
    screen._state[3][3][1][2] = program
    screen._state[2][1] = curve_buffer
    screen._state[3][1] = curve_job
    screen.error_popup.show_static("Retained error", "Retained detail")
    popup_state = _popup_state(screen)

    lease = screen.open_page_scenario_transaction()

    with pytest.raises(RuntimeError, match="already active"):
        screen.open_page_scenario_transaction()
    with pytest.raises(RuntimeError, match="already active"):
        screen.open_scenario_transaction()

    assert lease.step() is True
    assert screen._state[1][3] == 0
    assert screen._state[3][3][0][3] is None
    assert screen.input_box.get_str() == "x^2"
    assert screen.input_box.cursor.is_visible is True
    assert screen._state[2][2] is True
    assert screen._state[2][3] is False
    assert screen._state[3][3][1][2] is program
    assert screen._state[2][1] is curve_buffer
    assert screen._state[3][1] is curve_job
    assert screen._state[3][3][2][3] is context
    assert _popup_state(screen) == popup_state

    assert lease.close() is True
    assert lease.close() is True
    assert screen._state[1][2] is None
    assert screen._state[1][3] == 1
    assert screen._state[3][3][0][3] == 0
    assert screen.input_box.get_str() == ""
    assert screen.input_box.cursor.is_visible is False
    assert screen._state[2][2] is False
    assert screen._state[2][3] is True
    assert screen._state[3][3][1][2] is program
    assert screen._state[2][1] is curve_buffer
    assert screen._state[3][1] is curve_job
    assert screen._state[3][3][2][3] is context
    assert screen.expr == "x^2"
    assert tuple(screen._state[0]) == (
        -3.0, 7.0, -2.0, 8.0)
    assert _popup_state(screen) == popup_state


def test_calculator_page_scenario_propagates_original_memory_error(monkeypatch):
    screen = _calculator()
    original_error = MemoryError("injected activation OOM")

    def exhaust_heap(_box):
        raise original_error

    monkeypatch.setattr(calculator_module.InputBox, "activate", exhaust_heap)
    lease = screen.open_page_scenario_transaction()

    with pytest.raises(MemoryError) as raised:
        lease.step()

    assert raised.value is original_error
    assert screen._state[3][3] is lease
    assert lease.close() is True
    assert screen._state[3][3] is None


def test_calculator_page_scenario_close_can_retry_after_cleanup_failure(
        monkeypatch):
    screen = _calculator()
    screen.mode = 1
    screen._state[3][0][1] = "retained notice"
    screen.input_box.deactivate()
    lease = screen.open_page_scenario_transaction()
    assert lease.step() is True

    original_clear = CalculatorScreen._clear_presented_editor_state
    failed = [False]

    def fail_once(page):
        if not failed[0]:
            failed[0] = True
            raise RuntimeError("injected cleanup failure")
        return original_clear(page)

    monkeypatch.setattr(
        CalculatorScreen, "_clear_presented_editor_state", fail_once)

    with pytest.raises(RuntimeError, match="cleanup failure"):
        lease.close()

    assert screen._state[3][3] is lease
    assert lease.close() is True
    assert screen._state[3][3] is None
    assert screen.mode == 1
    assert screen._state[3][0][1] == "retained notice"
    assert screen.input_box.cursor.is_visible is False


def test_plot_page_scenario_propagates_original_memory_error(monkeypatch):
    screen = _plot()
    original_error = MemoryError("injected plot activation OOM")

    def exhaust_heap(_box):
        raise original_error

    monkeypatch.setattr(plot_module.InputBox, "activate", exhaust_heap)
    lease = screen.open_page_scenario_transaction()

    with pytest.raises(MemoryError) as raised:
        lease.step()

    assert raised.value is original_error
    assert screen._state[1][2] is lease
    assert lease.close() is True
    assert screen._state[1][2] is None


def test_plot_page_scenario_close_can_retry_after_cleanup_failure(
        monkeypatch):
    screen = _plot()
    screen._state[1][3] = 1
    screen._state[3][3][0][3] = 0
    screen.input_box.deactivate()
    lease = screen.open_page_scenario_transaction()
    assert lease.step() is True

    original_clear = PlotScreen._clear_presented_editor_state
    failed = [False]

    def fail_once(page):
        if not failed[0]:
            failed[0] = True
            raise RuntimeError("injected cleanup failure")
        return original_clear(page)

    monkeypatch.setattr(PlotScreen, "_clear_presented_editor_state", fail_once)

    with pytest.raises(RuntimeError, match="cleanup failure"):
        lease.close()

    assert screen._state[1][2] is lease
    assert lease.close() is True
    assert screen._state[1][2] is None
    assert screen._state[1][3] == 1
    assert screen._state[3][3][0][3] == 0
    assert screen.input_box.cursor.is_visible is False
