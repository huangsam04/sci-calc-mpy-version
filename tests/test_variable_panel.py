import sys
import types

import pytest

from screens import variable_panel as variable_panel_module
from screens.variable_panel import VariablePanel
from ui.inputbox import INPUT_FULL_NOTICE


class _Input:
    def __init__(self, accepts):
        self.accepts = accepts
        self.values = []

    def try_insert(self, value):
        self.values.append(value)
        return self.accepts


class _Context:
    def delete_var(self, _name):
        pass


class _Calculator:
    def __init__(self, accepts):
        self.vars = {"x": 1}
        self.input_box = _Input(accepts)
        self.context = _Context()

    def _fmt(self, value):
        return str(value)


class _Font:
    width = 5
    height = 7

    def measure_text(self, text, spacing=1):
        return len(text) * (self.width + spacing)


class _Display:
    def __init__(self):
        self.direct = []
        self.text = []

    def fill_rectangle(self, *_args):
        pass

    def draw_rectangle(self, *_args):
        pass

    def draw_hline(self, *_args):
        pass

    def draw_text(self, *args, **_kwargs):
        self.text.append(args[2])

    def draw_text8x8(self, x, y, text, gs=15):
        self.direct.append((x, y, text, gs))
        self.text.append(text)

    def draw_text_direct(self, x, y, text, font, gs=15):
        self.direct.append((x, y, text, gs))


def test_target_lazily_imports_variable_panel_scenario_transaction(
        monkeypatch):
    class LazyTransaction:
        def __init__(self, panel):
            self.panel = panel

    lazy_module = types.ModuleType("screens.variable_panel_scenario")
    lazy_module.VariablePanelScenarioTransaction = LazyTransaction
    monkeypatch.setitem(
        sys.modules, "screens.variable_panel_scenario", lazy_module)
    panel = VariablePanel(None, _Calculator(accepts=True))

    transaction = panel.open_scenario_transaction()

    assert type(transaction) is LazyTransaction
    assert transaction.panel is panel


def test_variable_panel_stays_open_and_reports_input_full_on_failed_insert():
    calc = _Calculator(accepts=False)
    panel = VariablePanel(None, calc)
    panel.activate()

    assert panel.update(None, (3, 3, False)) == "REDRAW"
    assert panel._state[4] == INPUT_FULL_NOTICE
    assert panel._state[1] == ["x"]
    assert calc.input_box.values == ["x"]


def test_variable_panel_does_not_redraw_for_a_repeated_unchanged_full_input():
    calc = _Calculator(accepts=False)
    panel = VariablePanel(None, calc)
    panel.activate()

    assert panel.update(None, (3, 3, False)) == "REDRAW"
    assert panel.update(None, (3, 3, False)) is None
    assert panel._state[4] == INPUT_FULL_NOTICE
    assert calc.input_box.values == ["x", "x"]


@pytest.mark.parametrize(
    "event",
    ((3, 3, False), (4, 3, False), (0, 0, False)),
    ids=("ent", "del", "esc"),
)
def test_variable_panel_active_lease_ignores_input_delete_and_navigation(
        event, monkeypatch):
    calc = _Calculator(accepts=True)
    deleted = []

    def delete_var(name):
        deleted.append(name)
        del calc.vars[name]

    def unexpected_sort(*_args, **_kwargs):
        raise AssertionError("active lease must not rebuild variables")

    calc.context.delete_var = delete_var
    monkeypatch.setattr(
        variable_panel_module, "sorted", unexpected_sort, raising=False)
    panel = VariablePanel(None, calc)
    transaction = panel.open_scenario_transaction()
    assert transaction.step() is False
    panel._state[2] = 0
    panel._state[3] = 0
    panel._state[4] = "lease notice"
    names = panel._state[1]
    state = (panel._state[2], panel._state[3], panel._state[4])
    variables = calc.vars
    variables_before = dict(variables)

    assert panel.update(None, event) is None

    assert (panel._state[2], panel._state[3], panel._state[4]) == state
    assert panel._state[1] is names
    assert calc.input_box.values == []
    assert calc.vars is variables
    assert calc.vars == variables_before
    assert deleted == []
    assert panel._state[5] is transaction
    assert transaction.close() is True
    assert panel.update(None, (0, 0, False)) == "VAR_PANEL_DONE"


def test_variable_panel_propagates_memory_error_from_variable_insert():
    calc = _Calculator(accepts=True)
    panel = VariablePanel(None, calc)
    panel.activate()

    def raise_oom(_value):
        raise MemoryError("injected variable insert")

    calc.input_box.try_insert = raise_oom

    with pytest.raises(MemoryError, match="injected variable insert"):
        panel.update(None, (3, 3, False))

    assert panel._state[4] == ""


def test_variable_panel_closes_only_after_a_complete_successful_insert():
    calc = _Calculator(accepts=True)
    panel = VariablePanel(None, calc)
    panel.activate()

    assert panel.update(None, (3, 3, False)) == "VAR_PANEL_DONE"
    assert calc.input_box.values == ["x"]


def test_variable_panel_draws_only_the_visible_value_page():
    calc = _Calculator(accepts=True)
    calc.vars = {
        "variable_name_" + str(index): index for index in range(16)
    }
    formatted = []
    calc._fmt = lambda value: formatted.append(value) or str(value)
    panel = VariablePanel(_Font(), calc)
    display = _Display()
    panel.activate()

    panel.draw(display)
    labels = [text for _x, y, text, _gs in display.direct
              if 15 <= y < 54]

    assert len(panel._state[1]) == 16
    assert len(labels) == 8
    assert len(formatted) == 8
    assert all(label is not None and len(label) <= 24 for label in labels)
    assert any(text == "Variables"
               for _x, _y, text, _gs in display.direct)

    panel.draw(display)
    assert len(formatted) == 16

    panel._state[2] = 8
    panel.draw(display)

    assert len(formatted) == 24


def test_variable_panel_draws_its_empty_state():
    calc = _Calculator(accepts=True)
    calc.vars = {}
    panel = VariablePanel(_Font(), calc)
    display = _Display()
    panel.activate()

    panel.draw(display)
    assert "No variables defined" in display.text

    panel.draw(display)
    assert display.text.count("No variables defined") == 2


def test_variable_panel_releases_only_its_sorted_snapshot():
    calc = _Calculator(accepts=True)
    panel = VariablePanel(None, calc)
    panel.activate()
    old_names = panel._state[1]
    panel._state[4] = INPUT_FULL_NOTICE

    assert panel.release_memory() is True
    assert panel._state[1] == ()
    assert calc.vars == {"x": 1}

    panel.activate()
    assert panel._state[1] == ["x"]
    assert panel._state[1] is not old_names
