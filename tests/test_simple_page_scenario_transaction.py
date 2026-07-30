import pytest

from screens.about import AboutScreen
from screens.letter_panel import LetterPanel
from screens.settings import SettingsScreen
from ui.inputbox import InputBox
from ui.menu import Menu


class _Display:
    def __init__(self):
        self.brightness_values = []

    def set_brightness(self, value):
        self.brightness_values.append(value)


def _settings_screen(request_save=None):
    return SettingsScreen(
        None,
        _Display(),
        {"brightness": 80, "display_digits": 4},
        AboutScreen(None, "1.2.3"),
        request_save=request_save,
    )


def _menu_state(menu):
    cursor = menu.cursor
    return (
        menu.cursor_pos, menu.view_offset, cursor.x, cursor.y, cursor.width,
        cursor.height, cursor.mode, cursor.is_visible, cursor.gs,
        menu._state[6])


def _input_state(input_box):
    cursor = input_box.cursor
    return (
        input_box, input_box.str, input_box.cursor_pos, input_box.view_offset,
        cursor.x, cursor.y, cursor.width, cursor.height, cursor.mode,
        cursor.is_visible, cursor.gs)


def test_settings_scenario_prepares_menu_without_mutating_or_persisting_settings():
    persisted = []
    screen = _settings_screen(
        request_save=lambda value, callback, owner: persisted.append(
            (value, callback, owner)))
    menu = screen._state[5]
    menu.cursor_pos = 2
    menu.cursor.x = 71
    menu.cursor.y = 43
    menu.cursor.width = 19
    menu.cursor.height = 8
    menu.cursor.mode = 1
    menu.cursor.is_visible = False
    menu.cursor.gs = 7
    menu._state[6] = -124
    expected_state = _menu_state(menu)
    settings_identity = screen._state[4]
    settings_before = dict(screen._state[4])

    transaction = screen.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="already active"):
        screen.open_scenario_transaction()

    assert transaction.step() is True
    assert transaction.step() is True
    assert screen._state[4] is settings_identity
    assert screen._state[4] == settings_before
    assert persisted == []
    assert screen._state[0].brightness_values == []

    assert transaction.close() is True
    assert _menu_state(menu) == expected_state
    assert screen._state[6] is None
    assert screen.open_scenario_transaction().close() is True


def test_settings_scenario_propagates_prepare_oom_and_retries_close(monkeypatch):
    screen = _settings_screen()
    transaction = screen.open_scenario_transaction()
    primary = MemoryError("injected settings menu OOM")

    def fail_activate(_menu):
        raise primary

    monkeypatch.setattr(Menu, "activate", fail_activate)

    with pytest.raises(MemoryError) as raised:
        transaction.step()

    assert raised.value is primary
    assert screen._state[6] is transaction
    assert transaction.close() is True
    assert screen._state[6] is None


def test_settings_scenario_close_failure_retains_its_guard_for_retry(monkeypatch):
    screen = _settings_screen()
    transaction = screen.open_scenario_transaction()
    assert transaction.step() is True
    invalidate = SettingsScreen._invalidate_scenario_visible_state
    failures = []

    def fail_once(active_screen):
        failures.append(True)
        if len(failures) == 1:
            raise RuntimeError("injected settings cleanup failure")
        return invalidate(active_screen)

    monkeypatch.setattr(
        SettingsScreen, "_invalidate_scenario_visible_state", fail_once)

    with pytest.raises(RuntimeError, match="cleanup failure"):
        transaction.close()

    assert screen._state[6] is transaction
    assert transaction.close() is True
    assert len(failures) == 2
    assert screen._state[6] is None


def test_about_scenario_is_static_but_still_exclusive_and_oom_safe(monkeypatch):
    screen = AboutScreen(None, "1.2.3")
    transaction = screen.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="already active"):
        screen.open_scenario_transaction()

    primary = MemoryError("injected About preparation OOM")

    def fail_prepare(_transaction, _screen):
        raise primary

    monkeypatch.setattr(
        type(transaction), "_prepare_visible_state", fail_prepare)

    with pytest.raises(MemoryError) as raised:
        transaction.step()

    assert raised.value is primary
    assert transaction.close() is True
    assert screen._scenario_transaction is None
    assert screen.update(None, (0, 0, False)) == "BACK"


def test_about_scenario_rejects_missing_static_context_and_reopens_normally():
    unavailable = AboutScreen(None, None)

    with pytest.raises(RuntimeError, match="version is unavailable"):
        unavailable.open_scenario_transaction()

    screen = AboutScreen(None, "1.2.3")
    transaction = screen.open_scenario_transaction()
    assert transaction.step() is True
    assert transaction.close() is True
    assert screen.open_scenario_transaction().close() is True
    screen.activate()


def test_letters_scenario_restores_actual_input_target_draft_and_cursor_state():
    target = InputBox(max_char=32)
    target.set_str("sin(x)", immediate=True)
    target.cursor_pos = 3
    target.view_offset = 1
    target.cursor.x = 31
    target.cursor.y = 5
    target.cursor.width = 4
    target.cursor.height = 9
    target.cursor.mode = 0
    target.cursor.is_visible = False
    target.cursor.gs = 10
    panel = LetterPanel(None, target)
    panel._set_draft("draft")
    panel._state[2] = 2
    panel._state[3] = "keep"
    expected_input = _input_state(target)
    expected_panel = (
        panel._state[0], panel._state[1], panel._state[2], panel._state[3])

    transaction = panel.open_scenario_transaction()

    with pytest.raises(RuntimeError, match="already active"):
        panel.open_scenario_transaction()

    assert transaction.step() is True
    assert panel._state[0] is target
    assert panel._state[1] == ""
    assert panel._state[2] == 0
    assert panel._state[3] == ""
    assert _input_state(target) == expected_input
    with pytest.raises(RuntimeError, match="scenario transaction is active"):
        panel.update(None, (0, 1, False))

    assert transaction.close() is True
    assert _input_state(target) == expected_input
    assert (
        panel._state[0], panel._state[1], panel._state[2],
        panel._state[3]) == expected_panel
    assert panel.open_scenario_transaction().close() is True


def test_letters_scenario_rejects_missing_or_replaced_input_target_and_recovers():
    unavailable = LetterPanel(None, None)

    with pytest.raises(RuntimeError, match="input target is unavailable"):
        unavailable.open_scenario_transaction()

    target = InputBox(max_char=16)
    panel = LetterPanel(None, target)
    transaction = panel.open_scenario_transaction()
    panel._state[0] = InputBox(max_char=16)

    with pytest.raises(RuntimeError, match="input target changed"):
        transaction.step()

    assert transaction.close() is True
    assert panel._state[0] is target


def test_letters_scenario_preserves_primary_oom_and_retries_target_cleanup(
        monkeypatch):
    target = InputBox(max_char=16)
    target.set_str("x", immediate=True)
    panel = LetterPanel(None, target)
    transaction = panel.open_scenario_transaction()
    primary = MemoryError("injected letters draft OOM")
    original_set_draft = LetterPanel._set_draft

    def fail_draft(_panel, _text):
        raise primary

    monkeypatch.setattr(LetterPanel, "_set_draft", fail_draft)

    with pytest.raises(MemoryError) as raised:
        transaction.step()

    assert raised.value is primary
    assert panel._state[4] is transaction

    original_release = InputBox.release_memory
    failures = []

    def fail_release_once(active_target):
        failures.append(True)
        if len(failures) == 1:
            raise RuntimeError("injected letters cleanup failure")
        return original_release(active_target)

    monkeypatch.setattr(InputBox, "release_memory", fail_release_once)

    with pytest.raises(RuntimeError, match="cleanup failure"):
        transaction.close()

    assert panel._state[4] is transaction
    assert transaction.close() is True
    assert len(failures) == 2
    assert panel._state[4] is None
    monkeypatch.setattr(LetterPanel, "_set_draft", original_set_draft)
    panel.activate()
    assert panel.update(None, (0, 1, False)) == "REDRAW"
