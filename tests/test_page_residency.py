import json

import pytest

from ui.residency import (PageResidency, SETTLE_MORE, SETTLE_REDRAW,
                          SessionSwap, SwapError)
from calc.functions import build_registry
from screens.calculator import CalculatorScreen
from screens.plot import PlotScreen
from screens.settings import SettingsScreen
from screens.stopwatch import StopwatchScreen


def test_session_swap_round_trips_one_page_and_rejects_corruption(tmp_path):
    swap = SessionSwap(str(tmp_path))
    swap.start_session()

    assert swap.write("calculator", {"input": "2+2", "cursor": 3}) is True
    assert swap.read("calculator") == {"input": "2+2", "cursor": 3}

    path = tmp_path / "calculator.swp"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["payload"] = '{"input":"corrupt"}'
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(SwapError):
        swap.read("calculator")
    assert not path.exists()


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
    assert first.events == ["release", "snapshot", "deactivate", "reset"]
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
