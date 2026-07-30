import sys
import types

import pytest

from screens import function_panel_scenario as function_panel_scenario_module
from screens.function_panel import FunctionPanel
from ui.menu import Menu


def _panel(monkeypatch):
    calls = []
    catalog = [("alpha", "alpha.py"), ("beta", "beta.py")]
    settings = {"enabled_functions": ["basic", "plugin:alpha"]}
    functions = {"alpha": ("alpha_fn",), "beta": ("beta_fn",)}
    dependencies = {"alpha": (), "beta": ()}

    panel = FunctionPanel(
        None, settings, dependencies, catalog)
    panel.activate()
    return panel, settings, functions, dependencies, calls


def _menu_state(menu):
    cursor = menu.cursor
    return (
        menu.cursor_pos, menu.view_offset,
        menu._state[6],
        cursor.x, cursor.y, cursor.width, cursor.height,
        cursor.mode, cursor.is_visible, cursor.gs,
    )


def _finish(transaction):
    while not transaction.step():
        pass


_CLOSED_TRANSACTION_REFERENCE_FIELDS = (
    "_panel", "_settings", "_enabled", "_toggled", "_pending_enabled",
    "_save_error", "_load_error", "_dependency_notice",
    "_plugin_dependencies", "_plugin_files", "_groups",
    "_group_labels", "_default_groups", "_items", "_menu",
)


def _assert_closed_transaction_releases_snapshots(transaction):
    assert transaction._closed is True
    for field in _CLOSED_TRANSACTION_REFERENCE_FIELDS:
        assert getattr(transaction, field) is None


def test_target_lazily_imports_function_panel_scenario_transaction(
        monkeypatch):
    class LazyTransaction:
        def __init__(self, panel):
            self.panel = panel

    lazy_module = types.ModuleType("screens.function_panel_scenario")
    lazy_module.FunctionPanelScenarioTransaction = LazyTransaction
    monkeypatch.setitem(
        sys.modules, "screens.function_panel_scenario", lazy_module)
    panel, _, _, _, _ = _panel(monkeypatch)

    transaction = panel.open_scenario_transaction()

    assert type(transaction) is LazyTransaction
    assert transaction.panel is panel


def test_function_panel_scenario_transaction_preserves_semantic_references(
        monkeypatch):
    panel, settings, _, dependencies, _ = _panel(monkeypatch)
    menu = panel._menu
    items = panel._items
    enabled = settings["enabled_functions"]
    plugin_files = panel._state[2][1]
    toggled = {"trig": False}
    pending = ["basic", "trig"]
    panel._state[0][2] = toggled
    panel._flags |= 1
    panel._state[0][3] = pending
    panel._state[1][0] = "Not saved - check SD"
    panel._state[1][1] = ("alpha", "Saved add-on error")
    panel._state[1][2] = "Saved dependency notice"
    panel._flags |= 8 | 16
    menu.cursor_pos = 3
    menu.view_offset = 1
    menu._state[6] = -78
    menu.cursor.x = 8
    menu.cursor.y = 26
    menu.cursor.width = 42
    menu.cursor.height = 9
    menu.cursor.mode = 1
    menu.cursor.is_visible = False
    menu.cursor.gs = 10
    menu_state = _menu_state(menu)

    transaction = panel.open_scenario_transaction()
    _finish(transaction)

    assert transaction.close() is True
    assert transaction.close() is True
    assert panel._state[1][3] is None
    assert panel._menu is menu
    assert panel._items is items
    assert panel._state[0][1] is settings
    assert settings["enabled_functions"] is enabled
    assert panel._state[0][2] is toggled
    assert panel._state[0][3] is pending
    assert panel._state[2][0] is dependencies
    assert panel._state[2][1] is plugin_files
    assert panel._flags & 1
    assert not panel._flags & 2
    assert panel._state[1][0] == "Not saved - check SD"
    assert panel._state[1][1] == ("alpha", "Saved add-on error")
    assert panel._state[1][2] == "Saved dependency notice"
    assert panel._flags & 8
    assert panel._flags & 16
    assert _menu_state(menu) == menu_state
    _assert_closed_transaction_releases_snapshots(transaction)


def test_function_panel_scenario_transaction_rebuilds_at_most_one_row_per_step(
        monkeypatch):
    panel, _, _, _, _ = _panel(monkeypatch)
    row_operations = []
    replace_item = Menu.replace_item
    add_item = Menu.add_item

    def count_replace(menu, index, label, target):
        row_operations.append(("replace", index))
        return replace_item(menu, index, label, target)

    def count_add(menu, label, target):
        row_operations.append(("add", len(menu._state[5])))
        return add_item(menu, label, target)

    monkeypatch.setattr(Menu, "replace_item", count_replace)
    monkeypatch.setattr(Menu, "add_item", count_add)

    transaction = panel.open_scenario_transaction()
    while True:
        before = len(row_operations)
        complete = transaction.step()
        assert len(row_operations) - before <= 1
        if complete:
            break

    assert len(row_operations) == len(panel._items)
    assert transaction.close() is True


def test_function_panel_scenario_transaction_never_scans_or_reads_settings(
        monkeypatch):
    panel, _, _, _, calls = _panel(monkeypatch)
    calls_before = len(calls)

    def unexpected_refresh(_panel):
        raise AssertionError("scenario transaction must not use one-shot refresh")

    monkeypatch.setattr(FunctionPanel, "_refresh", unexpected_refresh)
    transaction = panel.open_scenario_transaction()
    _finish(transaction)

    assert len(calls) == calls_before
    assert transaction.close() is True


def test_function_panel_scenario_transaction_oom_restores_semantics_and_drops_menu(
        monkeypatch):
    panel, settings, _, dependencies, _ = _panel(monkeypatch)
    enabled = settings["enabled_functions"]
    toggled = panel._state[0][2]
    plugin_files = panel._state[2][1]
    original_error = MemoryError("injected function panel row OOM")

    def exhaust_row(_menu, _index, _label, _target):
        raise original_error

    monkeypatch.setattr(Menu, "replace_item", exhaust_row)
    transaction = panel.open_scenario_transaction()

    with pytest.raises(MemoryError) as caught:
        transaction.step()

    assert caught.value is original_error
    assert panel._state[1][3] is None
    assert panel._state[0][1] is settings
    assert settings["enabled_functions"] is enabled
    assert panel._state[0][2] is toggled
    assert panel._state[2][1] is plugin_files
    assert panel._state[2][0] is dependencies
    assert panel._items == ()
    assert panel._menu is None
    assert not panel._flags & 4
    assert transaction.close() is True
    _assert_closed_transaction_releases_snapshots(transaction)


def test_function_panel_scenario_close_retains_snapshots_until_retry_succeeds(
        monkeypatch):
    panel, settings, _, _, _ = _panel(monkeypatch)
    pending = ["basic", "plugin:alpha"]
    detail = "retry detail"
    panel._state[0][3] = pending
    panel._state[1][1] = ("alpha", detail)
    transaction = panel.open_scenario_transaction()
    _finish(transaction)
    original_restore = (
        function_panel_scenario_module.FunctionPanelScenarioTransaction
        ._restore_semantic_state)
    primary = MemoryError("injected close restore OOM")
    restore_attempts = [0]

    def fail_first_restore(current, current_panel):
        restore_attempts[0] += 1
        if restore_attempts[0] == 1:
            raise primary
        return original_restore(current, current_panel)

    monkeypatch.setattr(
        function_panel_scenario_module.FunctionPanelScenarioTransaction,
        "_restore_semantic_state", fail_first_restore)

    with pytest.raises(MemoryError) as caught:
        transaction.close()

    assert caught.value is primary
    assert transaction._closed is False
    assert panel._state[1][3] is transaction
    assert transaction._panel is panel
    assert transaction._settings is settings
    assert transaction._pending_enabled is pending
    assert transaction._load_error[1] is detail

    assert transaction.close() is True
    assert restore_attempts == [2]
    assert panel._state[1][3] is None
    assert panel._state[0][1] is settings
    assert panel._state[0][3] is pending
    assert panel._state[1][1][1] is detail
    _assert_closed_transaction_releases_snapshots(transaction)


def test_function_panel_scenario_transaction_rejects_nesting_and_closed_steps(
        monkeypatch):
    panel, _, _, _, _ = _panel(monkeypatch)
    transaction = panel.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="already active"):
        panel.open_scenario_transaction()

    assert transaction.close() is True
    assert transaction.close() is True
    with pytest.raises(RuntimeError, match="closed"):
        transaction.step()

    next_transaction = panel.open_scenario_transaction()
    _finish(next_transaction)
    assert next_transaction.close() is True
