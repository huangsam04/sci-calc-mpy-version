"""Regression coverage for A-P2-2 resident object shapes."""

from calc.functions import EvalContext, FunctionRegistry
from performance import PerformanceMetrics, _FixedSampleWindow
from screens.about import AboutScreen
from screens.calculator import CalculatorScreen
from screens.function_panel import FunctionPanel
from screens.function_picker import FunctionPicker
from screens.letter_panel import LetterPanel
from screens.main_menu import MainMenu
from screens.plot import PlotScreen
from screens.settings import SettingsScreen
from screens.stopwatch import StopwatchScreen
from screens.variable_panel import VariablePanel
from ui.error_popup import ErrorPopup
from ui.inputbox import InputBox
from ui.menu import Menu
from ui.renderer import Renderer
from ui.sidebar import Sidebar
from utils.power import DisplayPower
from utils.storage import DeferredStorage


def test_resident_internal_objects_have_no_instance_dictionary():
    resident_types = (
        InputBox, Menu, ErrorPopup, Sidebar, Renderer, PerformanceMetrics,
        _FixedSampleWindow, CalculatorScreen, PlotScreen, StopwatchScreen,
        MainMenu, LetterPanel, FunctionPanel, FunctionPicker, VariablePanel,
        SettingsScreen, AboutScreen, DisplayPower, DeferredStorage,
    )

    for resident_type in resident_types:
        assert "__dict__" not in resident_type.__slots__
        assert not hasattr(object.__new__(resident_type), "__dict__")


def test_main_menu_keeps_only_its_real_menu_items():
    main_menu = MainMenu()
    target = object()

    main_menu.add_screen("Calculator", target)

    assert not hasattr(main_menu, "_items")
    assert main_menu.menu._state[5][0] == ["Calculator", target]
    assert main_menu.menu._state[5][1:] == [
        ["", None], ["", None], ["", None], ["", None]]


def test_error_popup_uses_five_fixed_micropython_instance_keys():
    assert ErrorPopup.__slots__ == (
        "expr", "title", "detail", "active", "_state")
    popup = ErrorPopup()
    assert len(popup._state) == 4


def test_plot_uses_four_keys_and_only_small_fixed_state_tables():
    assert PlotScreen.__slots__ == (
        "input_box", "error_popup", "expr", "_state")
    plot = PlotScreen(None, registry=FunctionRegistry())
    assert len(plot._state) == 4
    assert max(len(table) for table in plot._state) == 4
    tail = plot._state[3][3]
    assert len(tail) == 4
    assert max(len(table) for table in tail[:3]) == 4
    assert not hasattr(tail[3], "__dict__")


def test_calculator_uses_four_keys_and_only_small_fixed_state_tables():
    assert CalculatorScreen.__slots__ == (
        "input_box", "mode", "context", "_state")
    calculator = CalculatorScreen(None, registry=FunctionRegistry())
    assert len(calculator._state) == 4
    assert len(calculator._state[2]) == 4
    assert len(calculator._state[3]) == 4
    assert len(calculator._state[3][0]) == 4
    assert len(calculator._state[3][0][3]) == 2


def test_function_panel_uses_four_keys_and_only_small_fixed_state_tables():
    assert FunctionPanel.__slots__ == ("_menu", "_items", "_flags", "_state")
    panel = FunctionPanel(None, {"enabled_functions": ["basic"]})
    assert len(panel._state) == 3
    assert max(len(table) for table in panel._state) == 4


def test_stopwatch_uses_four_keys_and_only_small_fixed_state_tables():
    assert StopwatchScreen.__slots__ == (
        "_clock", "_render", "_footer", "_runtime")
    stopwatch = StopwatchScreen(None)
    assert len(stopwatch._clock) == 4
    assert max(len(table) for table in stopwatch._clock[2:]) == 4
    assert max(len(table) for table in stopwatch._render) == 4
    assert max(len(table) for table in stopwatch._footer) == 4
    assert max(len(table) for table in stopwatch._runtime) == 4
    assert len(stopwatch._runtime[0][0]) == 4


def test_public_plugin_seams_remain_extensible():
    registry = FunctionRegistry()
    context = EvalContext({}, registry)

    registry.addon_extension_probe = True
    context.addon_extension_probe = True

    assert registry.addon_extension_probe is True
    assert context.addon_extension_probe is True
