from pathlib import Path

import pytest

from nav_scenario import (
    PAGE_SCENARIO_CALCULATOR,
    PAGE_SCENARIO_FUNCTION_PICKER,
    PAGE_SCENARIO_LETTERS,
    PAGE_SCENARIO_VARIABLE_PANEL,
    PageLifecycleScenario,
)


SOURCE = Path(__file__).parents[1] / "source"


class _PublicNavigationOnly:
    def __init__(self, root, pages):
        self.current = root
        self._path = [root]
        self._pages = pages
        self.calls = []
        self.open_error = None
        self.back_error = None

    def open(self, page_id):
        error = self.open_error
        if error is not None:
            self.open_error = None
            raise error
        self.calls.append(("open", page_id))
        self.current = self._pages[page_id]
        self._path.append(self.current)
        return self.current

    def back(self):
        error = self.back_error
        if error is not None:
            self.back_error = None
            raise error
        self.calls.append(("back", None))
        if len(self._path) > 1:
            self._path.pop()
        self.current = self._path[-1]
        return self.current


def _finish(transaction, action, limit=8):
    for _ in range(limit):
        if transaction.step(action):
            return
    raise AssertionError("page lifecycle scenario did not finish")


@pytest.mark.parametrize("action", range(1, 10))
def test_every_page_round_trip_uses_only_open_back_and_current(action):
    root = object()
    pages = {page_id: object() for page_id in range(1, 10)}
    nav = _PublicNavigationOnly(root, pages)
    transaction = PageLifecycleScenario(nav, root)

    _finish(transaction, action)

    if action in (
            PAGE_SCENARIO_LETTERS,
            PAGE_SCENARIO_FUNCTION_PICKER,
            PAGE_SCENARIO_VARIABLE_PANEL):
        expected = [
            ("open", PAGE_SCENARIO_CALCULATOR),
            ("open", action),
            ("back", None),
            ("back", None),
        ]
    else:
        expected = [("open", action), ("back", None)]
    assert nav.calls == expected
    assert nav.current is root
    assert transaction.close() is True


def test_action_validation_and_mid_trip_change_leave_current_page_owned():
    root = object()
    pages = {page_id: object() for page_id in range(1, 10)}
    nav = _PublicNavigationOnly(root, pages)
    transaction = PageLifecycleScenario(nav, root)

    for invalid in (True, 0, 10, "1"):
        with pytest.raises(ValueError, match="Unknown page scenario action"):
            transaction.step(invalid)

    assert transaction.step(1) is False
    with pytest.raises(RuntimeError, match="action changed"):
        transaction.step(2)
    assert nav.current is pages[1]
    assert transaction.close() is True
    assert nav.current is root


def test_close_restores_both_levels_of_an_auxiliary_page_path():
    root = object()
    pages = {page_id: object() for page_id in range(1, 10)}
    nav = _PublicNavigationOnly(root, pages)
    transaction = PageLifecycleScenario(nav, root)

    assert transaction.step(PAGE_SCENARIO_LETTERS) is False
    assert transaction.step(PAGE_SCENARIO_LETTERS) is False
    assert nav.current is pages[PAGE_SCENARIO_LETTERS]

    assert transaction.close() is True
    assert transaction.close() is True
    assert nav.current is root
    assert nav.calls[-2:] == [("back", None), ("back", None)]


@pytest.mark.parametrize(
    ("primary", "cleanup", "expected"),
    (
        (MemoryError("action oom"), RuntimeError("cleanup"), "primary"),
        (MemoryError("action oom"), MemoryError("cleanup oom"), "primary"),
        (RuntimeError("action"), MemoryError("cleanup oom"), "cleanup"),
        (RuntimeError("action"), RuntimeError("cleanup"), "primary"),
    ),
)
def test_close_preserves_memory_error_precedence_and_remains_retryable(
        primary, cleanup, expected):
    root = object()
    pages = {page_id: object() for page_id in range(1, 10)}
    nav = _PublicNavigationOnly(root, pages)
    transaction = PageLifecycleScenario(nav, root)
    assert transaction.step(1) is False
    nav.back_error = cleanup

    with pytest.raises(BaseException) as caught:
        transaction.close_with_primary(primary)

    wanted = cleanup if expected == "cleanup" else primary
    assert caught.value is wanted
    assert nav.current is pages[1]
    assert transaction.close() is True
    assert nav.current is root


def test_product_navigation_has_no_acceptance_lifecycle_entrypoints():
    forbidden = (
        "acquire_scenario_page",
        "release_scenario_page",
        "open_page_scenario_transaction",
        "_page_scenario_transaction",
    )
    for relative in (
            "main.py",
            "runtime_handle.py",
            "screens/calculator.py",
            "screens/plot.py",
            "screens/stopwatch.py"):
        source = (SOURCE / relative).read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in source


def test_acceptance_page_adapter_does_not_read_nav_private_state():
    source = (SOURCE / "nav_scenario.py").read_text(encoding="utf-8")

    assert ".stack" not in source
    assert "._active_screen" not in source
    assert "acquire_scenario_page" not in source
    assert "release_scenario_page" not in source
