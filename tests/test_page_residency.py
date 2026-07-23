import json

import pytest

from ui.residency import (PageResidency, SETTLE_MORE, SETTLE_REDRAW,
                          SessionSwap, SwapError)
from calc.functions import build_registry
from screens.calculator import CalculatorScreen
from screens.plot import PlotScreen
from screens.settings import SettingsScreen
from screens.stopwatch import StopwatchScreen
from screens import stopwatch as stopwatch_module


def test_session_swap_round_trips_one_page_and_rejects_corruption(tmp_path):
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

    for page in (calculator, plot, stopwatch, settings):
        page.draw_transition_default(display)

    rendered = " ".join(display.text)
    assert "PRIVATE" not in rendered
    assert "Lap1" not in rendered
    assert "70" not in rendered
