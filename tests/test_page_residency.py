import json

import pytest

from ui.residency import (PageResidency, SETTLE_MORE, SETTLE_REDRAW,
                          SessionSwap, SwapError)
from ui import residency as residency_module
from calc.functions import build_registry
from screens.calculator import CalculatorScreen
from screens.plot import PlotScreen
from screens.settings import SettingsScreen
from screens.stopwatch import StopwatchScreen
from screens import stopwatch as stopwatch_module
from screens.function_panel import FunctionPanel
from screens.function_picker import FunctionPicker
from screens.letter_panel import LetterPanel
from screens.main_menu import MainMenu
from screens.variable_panel import VariablePanel


def test_session_swap_round_trips_one_page_and_rejects_wrong_checksum(tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()

    assert swap.write("calculator", {"input": "2+2", "cursor": 3}) is True
    assert swap.read("calculator") == {"input": "2+2", "cursor": 3}

    path = tmp_path / "calculator.swp"
    header, _ = path.read_text(encoding="utf-8").split("\n", 1)
    path.write_text(
        header + '\n{"input":"corrupt"}', encoding="utf-8")

    with pytest.raises(SwapError):
        swap.read("calculator")
    assert not path.exists()


def test_session_swap_rejects_unknown_version_and_discards_only_that_record(
        tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    assert swap.write("unknown", {"value": 1}) is True
    assert swap.write("other", {"value": 2}) is True
    path = tmp_path / "unknown.swp"
    header, payload = path.read_text(encoding="utf-8").split("\n", 1)
    parts = header.split("|")
    parts[1] = "999"
    path.write_text("|".join(parts) + "\n" + payload, encoding="utf-8")

    with pytest.raises(SwapError):
        swap.read("unknown")

    assert not path.exists()
    assert swap.read("other") == {"value": 2}


def test_session_swap_rejects_truncated_record(tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    path = tmp_path / "truncated.swp"
    path.write_text("SCI-CALC-PAGE|1|20", encoding="utf-8")

    with pytest.raises(SwapError):
        swap.read("truncated")

    assert not path.exists()


def test_start_session_clears_only_page_swap_records(tmp_path):
    swap_directory = tmp_path / "swap"
    swap_directory.mkdir()
    for name in ("old.swp", "old.tmp", "old.bak"):
        (swap_directory / name).write_text("old session", encoding="utf-8")
    marker = swap_directory / "keep.txt"
    marker.write_text("unrelated", encoding="utf-8")
    settings = tmp_path / "settings.json"
    variables = tmp_path / "variables.json"
    settings.write_text('{"brightness": 80}', encoding="utf-8")
    variables.write_text('{"x": 42}', encoding="utf-8")

    swap = SessionSwap(str(swap_directory))
    assert swap.start_session() is True

    assert marker.read_text(encoding="utf-8") == "unrelated"
    assert settings.read_text(encoding="utf-8") == '{"brightness": 80}'
    assert variables.read_text(encoding="utf-8") == '{"x": 42}'
    assert not list(swap_directory.glob("*.swp"))
    assert not list(swap_directory.glob("*.tmp"))
    assert not list(swap_directory.glob("*.bak"))


def test_session_swap_counts_payload_without_materializing_a_second_copy(
        tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    state = {"input": "正弦🙂"}
    payload = swap.pack(state)

    assert swap.write_packed("unicode", payload) is True

    record = (tmp_path / "unicode.swp").read_bytes()
    header, _ = record.split(b"\n", 1)
    assert int(header.split(b"|")[2]) == len(payload.encode("utf-8"))
    assert len(record) <= 4096
    assert swap.read("unicode") == state


def test_session_swap_rejects_payload_that_only_fits_without_record_header(
        tmp_path):
    swap = SessionSwap(str(tmp_path), max_snapshot_bytes=128)
    swap.start_session()

    assert swap.write("bounded", {"value": "x" * 110}) is False
    assert not (tmp_path / "bounded.swp").exists()


def test_session_swap_failed_atomic_replace_restores_previous_record(
        monkeypatch, tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    assert swap.write("calculator", {"input": "old"}) is True
    real_rename = residency_module.os.rename

    def fail_temporary_commit(source, target):
        if str(source).endswith(".tmp") and str(target).endswith(".swp"):
            raise OSError("injected atomic replace failure")
        return real_rename(source, target)

    monkeypatch.setattr(residency_module.os, "rename", fail_temporary_commit)

    assert swap.write("calculator", {"input": "new"}) is False
    assert not (tmp_path / "calculator.tmp").exists()
    assert (tmp_path / "calculator.swp").exists()
    swap.available = True
    assert swap.read("calculator") == {"input": "old"}


class Page:
    def __init__(self, key, value=""):
        self.swap_key = key
        self.value = value
        self.events = []
        self.error = ""

    def snapshot_state(self):
        self.events.append("snapshot")
        return {"value": self.value}

    def reset_state(self):
        self.events.append("reset")
        self.value = ""

    def restore_state(self, state):
        self.events.append("restore")
        self.value = state["value"]

    def release_memory(self):
        self.events.append("release")
        return True

    def deactivate(self):
        self.events.append("deactivate")

    def activate_default(self):
        self.events.append("activate_default")

    def settle_step(self):
        self.events.append("settle")
        return False

    def show_residency_error(self, message):
        self.error = message


def _settle_all(residency, page):
    flags = SETTLE_MORE
    redraw = False
    while flags & SETTLE_MORE:
        flags = residency.settle(page)
        redraw = redraw or bool(flags & SETTLE_REDRAW)
    return redraw


def test_page_residency_releases_before_default_view_and_restores_after_settle(
        tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    first = Page("first", "kept on disk")
    second = Page("second")

    residency.leave(first)

    assert first.value == ""
    assert first.events == ["snapshot", "release", "deactivate", "reset"]
    assert not (tmp_path / "first.swp").exists()

    residency.prepare(second)
    assert second.events == ["activate_default"]
    assert _settle_all(residency, second) is False
    assert (tmp_path / "first.swp").exists()

    residency.leave(second)
    residency.prepare(first)
    assert first.value == ""
    assert _settle_all(residency, first) is True
    assert first.value == "kept on disk"
    assert first.events[-2:] == ["restore", "settle"]


def test_corrupt_snapshot_resets_only_the_page_being_opened(tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    first = Page("first", "first state")
    second = Page("second", "second state")

    residency.leave(first)
    residency.prepare(second)
    _settle_all(residency, second)
    residency.leave(second)
    residency.prepare(first)

    path = tmp_path / "first.swp"
    path.write_text("{broken", encoding="utf-8")
    _settle_all(residency, first)

    assert first.value == ""
    assert first.error
    assert swap.read("second") == {"value": "second state"}


def test_missing_expected_snapshot_resets_only_the_page_being_opened(tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    first = Page("first", "first state")
    second = Page("second", "second state")

    residency.leave(first)
    residency.prepare(second)
    _settle_all(residency, second)
    residency.leave(second)
    residency.prepare(first)
    (tmp_path / "first.swp").unlink()

    _settle_all(residency, first)

    assert first.value == ""
    assert first.error
    assert swap.read("second") == {"value": "second state"}


def test_read_exception_resets_only_the_page_being_opened(
        monkeypatch, tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    first = Page("first", "first state")
    second = Page("second", "second state")

    residency.leave(first)
    residency.prepare(second)
    _settle_all(residency, second)
    residency.leave(second)
    residency.prepare(first)
    real_read = swap.read

    def fail_first_read(key):
        if key == "first":
            raise OSError("injected read failure")
        return real_read(key)

    monkeypatch.setattr(swap, "read", fail_first_read)
    _settle_all(residency, first)

    assert first.value == ""
    assert first.error == "injected read failure"
    assert swap.read("second") == {"value": "second state"}


def test_missing_sd_reports_error_and_resets_the_destination(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("blocked", encoding="utf-8")
    swap = SessionSwap(str(blocker / "swap"))
    assert swap.start_session() is False
    residency = PageResidency(swap=swap)
    first = Page("first", "cannot persist")
    destination = Page("destination", "must reset")

    residency.leave(first)
    residency.prepare(destination)
    _settle_all(residency, destination)

    assert destination.value == ""
    assert destination.error


def test_only_one_bounded_packed_write_is_retained_in_ram(tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)

    residency.leave(Page("first", "older"))
    residency.leave(Page("second", "latest"))

    assert residency._pending_key == "first"
    assert residency._pending_state == {"value": "older"}
    assert residency._pending_payload is None
    residency.prepare(Page("destination"))
    assert residency.settle(residency._current) & SETTLE_MORE
    assert isinstance(residency._pending_payload, str)
    assert len(residency._pending_payload.encode("utf-8")) <= 4096
    assert "first" in residency._expected
    assert "second" not in residency._expected


def test_immediate_back_keeps_the_outgoing_page_pending_snapshot(tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    first = Page("first", "latest edit")
    second = Page("second", "default only")

    residency.leave(first)
    residency.prepare(second)
    residency.leave(second)
    residency.prepare(first)
    _settle_all(residency, first)

    assert first.value == "latest edit"
    assert second.error == ""


def test_one_page_write_failure_does_not_reset_an_unrelated_page(
        monkeypatch, tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    first = Page("first", "cannot write")
    destination = Page("destination", "still usable")
    monkeypatch.setattr(swap, "write_packed", lambda key, payload: False)
    swap.last_error = "record write failed"

    residency.leave(first)
    residency.prepare(destination)
    _settle_all(residency, destination)

    assert destination.error == ""
    assert destination.value == "still usable"

    residency.prepare(first)
    _settle_all(residency, first)
    assert first.error == "record write failed"


def test_settle_step_failure_is_contained_to_the_active_page(tmp_path):
    class BrokenPage(Page):
        def settle_step(self):
            raise ValueError("invalid restored state")

    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    page = BrokenPage("broken", "unsafe")

    residency.prepare(page)
    flags = residency.settle(page)

    assert flags == SETTLE_REDRAW
    assert page.value == ""
    assert page.error == "invalid restored state"


def test_leave_lifecycle_failure_is_deferred_to_only_that_page(tmp_path):
    class BrokenLeavePage(Page):
        def release_memory(self):
            self.events.append("release")
            raise MemoryError("injected release failure")

    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    broken = BrokenLeavePage("broken", "saved first")
    destination = Page("destination", "still usable")

    residency.leave(broken)
    residency.prepare(destination)
    _settle_all(residency, destination)

    assert destination.error == ""
    assert destination.value == "still usable"

    residency.prepare(broken)
    _settle_all(residency, broken)
    assert broken.error == "injected release failure"


def test_prepare_failure_is_reported_after_default_transition(tmp_path):
    class BrokenPreparePage(Page):
        def activate_default(self):
            self.events.append("activate_default")
            raise MemoryError("injected activation failure")

    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    page = BrokenPreparePage("broken", "unsafe")

    residency.prepare(page)
    flags = residency.settle(page)

    assert flags == SETTLE_REDRAW
    assert page.value == ""
    assert page.error == "injected activation failure"


def test_dirty_page_snapshot_is_encoded_and_written_only_during_settle(
        tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()
    residency = PageResidency(swap=swap)
    page = Page("live", "before")
    residency.prepare(page)
    residency.settle(page)

    page.value = "after"
    residency.mark_dirty(page)

    assert residency._pending_payload is None
    assert not (tmp_path / "live.swp").exists()
    assert residency.settle(page) & SETTLE_MORE
    assert residency._pending_key == "live"
    assert residency._pending_state == {"value": "after"}
    assert residency._pending_payload is None
    assert not (tmp_path / "live.swp").exists()
    assert residency.settle(page) & SETTLE_MORE
    assert isinstance(residency._pending_payload, str)
    assert not (tmp_path / "live.swp").exists()
    assert residency.settle(page) & SETTLE_MORE
    assert swap.read("live") == {"value": "after"}


def test_running_stopwatch_counts_time_spent_outside_the_page(monkeypatch):
    now = [1000]
    monkeypatch.setattr(stopwatch_module.time, "ticks_ms", lambda: now[0])
    stopwatch = StopwatchScreen(None)
    stopwatch._start()
    now[0] = 2500
    state = stopwatch.snapshot_state()

    stopwatch.reset_state()
    now[0] = 4000
    stopwatch.restore_state(state)

    assert stopwatch._running is True
    assert stopwatch._get_elapsed() == 3000


class DefaultFrameDisplay:
    def __init__(self):
        self.text = []

    def draw_text8x8(self, x, y, value, **kwargs):
        self.text.append(value)

    def draw_rectangle(self, *args):
        pass

    def draw_hline(self, *args):
        pass

    def draw_vline(self, *args):
        pass


def test_transition_default_frames_never_include_saved_data_or_parameters():
    display = DefaultFrameDisplay()
    calculator = CalculatorScreen(None, registry=build_registry(), variables={})
    calculator.input_box.set_str("PRIVATE_EXPR")
    calculator.history = [("PRIVATE_HISTORY", 42.0)]
    plot = PlotScreen(None, registry=build_registry())
    plot.expr = "PRIVATE_PLOT"
    stopwatch = StopwatchScreen(None)
    stopwatch._laps = [(1, 1234)]
    settings = SettingsScreen(
        None, type("Display", (), {})(), {"brightness": 70}, object())
    function_panel = FunctionPanel(None, settings={"enabled_functions": []})
    function_panel._toggled = {"plugin:PRIVATE_PLUGIN": True}
    function_panel._plugin_files = (("PRIVATE_PLUGIN", "private.py"),)
    function_picker = FunctionPicker(None, calculator)
    function_picker._names = ["PRIVATE_FUNCTION"]
    calculator.vars["PRIVATE_VARIABLE"] = 123
    variable_panel = VariablePanel(None, calculator)
    variable_panel._names = ["PRIVATE_VARIABLE"]
    letter_panel = LetterPanel(None, calculator.input_box)
    letter_panel.text = "PRIVATE_DRAFT"
    main_menu = MainMenu(None)
    main_menu.add_screen("Calculator", calculator)
    main_menu.menu.cursor_pos = 1

    for page in (calculator, plot, stopwatch, settings, function_panel,
                 function_picker, variable_panel, letter_panel, main_menu):
        page.draw_transition_default(display)

    rendered = " ".join(display.text)
    assert "PRIVATE" not in rendered
    assert "Lap1" not in rendered
    assert "70" not in rendered
