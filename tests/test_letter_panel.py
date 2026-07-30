import sys
import types

import pytest

from screens.letter_panel import LetterPanel
from ui.inputbox import INPUT_FULL_NOTICE, InputBox


_ALPHA_KEYS = (
    (0, 1), (0, 2), (0, 3), (0, 4), (0, 5),
    (1, 0), (1, 1), (1, 2), (1, 3), (1, 4), (1, 5),
    (2, 0), (2, 1), (2, 2), (2, 3), (2, 4), (2, 5),
    (3, 0), (3, 1), (3, 2), (3, 3), (3, 4), (3, 5),
    (4, 1), (4, 2), (4, 4),
)


def _press(panel, row, col, shift=False):
    return panel.update(None, (row, col, shift))


class _DraftDisplay:
    def __init__(self):
        self.text = []

    def fill_rectangle(self, *_args):
        pass

    def draw_hline(self, *_args):
        pass

    def draw_text8x8(self, x, y, text, gs=15):
        self.text.append((x, y, text, gs))

    def draw_vline(self, *_args):
        pass


def test_target_lazily_imports_letters_scenario_transaction(monkeypatch):
    class LazyTransaction:
        def __init__(self, panel):
            self.panel = panel

    lazy_module = types.ModuleType("screens.letter_panel_scenario")
    lazy_module.LetterPanelScenarioTransaction = LazyTransaction
    monkeypatch.setitem(
        sys.modules, "screens.letter_panel_scenario", lazy_module)
    panel = LetterPanel(None, InputBox(max_char=16))

    transaction = panel.open_scenario_transaction()

    assert type(transaction) is LazyTransaction
    assert transaction.panel is panel


def test_letter_panel_exposes_every_alpha_key_in_upper_and_lower_layers():
    input_box = InputBox(max_char=64)
    panel = LetterPanel(None, input_box)
    panel.activate()

    for row, col in _ALPHA_KEYS:
        _press(panel, row, col)
    assert panel._state[1] == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    panel.activate()
    _press(panel, 4, 0)
    for row, col in _ALPHA_KEYS:
        _press(panel, row, col)
    assert panel._state[1] == "abcdefghijklmnopqrstuvwxyz"


def test_letter_panel_cycles_to_an_explicit_symbol_layer_with_quote_and_semicolon():
    panel = LetterPanel(None, InputBox(max_char=16))
    panel.activate()

    _press(panel, 4, 0)
    _press(panel, 4, 0)
    assert panel._get_char(0, 1) == '"'
    assert panel._get_char(0, 2) == ';'

    _press(panel, 0, 1)
    _press(panel, 0, 2)
    assert panel._state[1] == '";'


def test_letter_panel_preserves_the_complete_symbol_key_layout():
    panel = LetterPanel(None, InputBox(max_char=64))
    panel.activate()
    _press(panel, 4, 0)
    _press(panel, 4, 0)

    assert "".join(
        panel._get_char(row, col) for row, col in _ALPHA_KEYS
    ) == '";()[]{}+-*/^=,_:.?<>!@#$%'


def test_letter_panel_uses_physical_del_for_backspace_and_keeps_ang_as_z():
    panel = LetterPanel(None, InputBox(max_char=16))
    panel.activate()
    panel._state[1] = "AB"

    _press(panel, 4, 3)
    assert panel._state[1] == "A"

    _press(panel, 4, 4)
    assert panel._state[1] == "AZ"
    assert panel.blocks_global_shortcuts() is True


def test_letter_panel_draws_only_the_fixed_draft_tail():
    panel = LetterPanel(None, InputBox(max_char=64))
    panel.activate()
    panel._state[1] = "012345678901234567890123456789"
    display = _DraftDisplay()

    panel.draw(display)
    assert (10, 1, panel._state[1][-22:], 15) in display.text

    assert _press(panel, 0, 1) == "REDRAW"
    display = _DraftDisplay()
    panel.draw(display)
    assert (10, 1, panel._state[1][-22:], 15) in display.text


def test_letter_panel_draws_its_footer_layer():
    panel = LetterPanel(None, InputBox(max_char=16))
    display = _DraftDisplay()
    panel.activate()

    panel.draw(display)
    assert any(text == "OK insert ESC"
               for _x, _y, text, _gs in display.text)
    assert any(text == "ABC" for _x, _y, text, _gs in display.text)

    assert _press(panel, 4, 0) == "REDRAW"
    display = _DraftDisplay()
    panel.draw(display)
    assert any(text == "abc" for _x, _y, text, _gs in display.text)


def test_letter_panel_keeps_draft_open_when_target_input_is_full():
    input_box = InputBox(max_char=2)
    input_box.set_str("ok", immediate=True)
    panel = LetterPanel(None, input_box)
    panel.activate()
    panel._state[1] = "Z"

    assert _press(panel, 4, 5) == "REDRAW"
    assert panel._state[1] == "Z"
    assert panel._state[3] == INPUT_FULL_NOTICE
    assert input_box.get_str() == "ok"

    # The overlay is already showing the same failure; a held/repeated OK
    # edge must not schedule another otherwise-identical present.
    assert _press(panel, 4, 5) is None
    assert panel._state[1] == "Z"
    assert panel._state[3] == INPUT_FULL_NOTICE
    assert input_box.get_str() == "ok"


def test_letter_panel_commits_complete_draft_only_after_successful_insert():
    input_box = InputBox(max_char=4)
    panel = LetterPanel(None, input_box)
    panel.activate()
    panel._state[1] = "UV"

    assert _press(panel, 4, 5) == "LETTER_DONE"
    assert input_box.get_str() == "UV"
    assert panel._state[1] == ""


def test_letter_panel_propagates_insert_memory_error_without_clearing_draft():
    class ExhaustedInput:
        def try_insert(self, value):
            assert value == "UV"
            raise MemoryError("injected letter insert")

    panel = LetterPanel(None, ExhaustedInput())
    panel.activate()
    panel._state[1] = "UV"
    panel._state[3] = INPUT_FULL_NOTICE

    with pytest.raises(MemoryError, match="injected letter insert"):
        _press(panel, 4, 5)

    assert panel._state[1] == "UV"
    assert panel._state[3] == INPUT_FULL_NOTICE


def test_letter_panel_release_discards_only_the_temporary_draft():
    input_box = InputBox(max_char=8)
    input_box.set_str("x", immediate=True)
    panel = LetterPanel(None, input_box)
    panel._state[1] = "YZ"
    panel._state[3] = INPUT_FULL_NOTICE
    panel._state[2] = 2

    assert panel.release_memory() is True
    assert panel._state[1] == ""
    assert panel._state[3] == ""
    assert panel._state[2] == 0
    assert input_box.get_str() == "x"


def test_letter_panel_draws_legends_at_the_fixed_key_centres():
    panel = LetterPanel(None, InputBox(max_char=16))
    display = _DraftDisplay()

    panel.draw(display)

    legend = [
        (x, y, text) for x, y, text, _gs in display.text
        if 12 <= y <= 44
    ]

    assert len(legend) == 30
    assert legend[:6] == [
        (4, 12, "ESC"),
        (44, 12, "A"),
        (76, 12, "B"),
        (108, 12, "C"),
        (140, 12, "D"),
        (172, 12, "E"),
    ]
    assert legend[-6:] == [
        (12, 44, "Sh"),
        (44, 44, "X"),
        (76, 44, "Y"),
        (108, 44, "Bk"),
        (140, 44, "Z"),
        (172, 44, "OK"),
    ]
