from pathlib import Path
import sys
import types

import pytest

import main as main_module
import ui.renderer
import ui.sidebar

from main import (
    Nav,
    PAGE_SCENARIO_CALCULATOR,
    PAGE_SCENARIO_PLOT,
    PAGE_SCENARIO_ADDONS,
    PAGE_SCENARIO_STOPWATCH,
    PAGE_SCENARIO_SETTINGS,
    PAGE_SCENARIO_ABOUT,
    PAGE_SCENARIO_LETTERS,
    PAGE_SCENARIO_FUNCTION_PICKER,
    PAGE_SCENARIO_VARIABLE_PANEL,
)
from runtime_scenarios import APPLICATION_MATRIX_DEVICE_READY


SOURCE = Path(__file__).parents[1] / "source"


class _RendererStub:
    def __init__(self, display, sidebar, memory=None):
        self.last_present_us = 0

    def invalidate(self):
        pass


class _MemoryStub:
    def release_plot_workspace(self):
        return False

    def collect(self):
        pass


class _Screen:
    def __init__(self, name, events=None):
        self.name = name
        self.calls = []
        self.events = events if events is not None else []

    def _record(self, call):
        self.calls.append(call)
        self.events.append(self.name + ":" + call)

    def activate(self):
        self._record("activate")

    def deactivate(self):
        self._record("deactivate")

    def release_memory(self):
        self._record("release_memory")
        return False


class _FaultingScreen(_Screen):
    def __init__(self, name, events=None, release_errors=(),
                 activate_errors=(), deactivate_errors=()):
        super().__init__(name, events)
        self.release_errors = list(release_errors)
        self.activate_errors = list(activate_errors)
        self.deactivate_errors = list(deactivate_errors)

    def activate(self):
        self._record("activate")
        if self.activate_errors:
            raise self.activate_errors.pop(0)

    def deactivate(self):
        self._record("deactivate")
        if self.deactivate_errors:
            raise self.deactivate_errors.pop(0)

    def release_memory(self):
        self._record("release_memory")
        if self.release_errors:
            raise self.release_errors.pop(0)
        return False


class _Lease:
    def __init__(self, screen, kind, steps_before_done=1, step_error=None,
                 close_error=None):
        self.screen = screen
        self.kind = kind
        self.steps_before_done = steps_before_done
        self.step_error = step_error
        self.close_error = close_error
        self.step_calls = 0
        self.close_calls = 0
        self.closed = False

    def step(self):
        self.step_calls += 1
        if self.step_error is not None:
            raise self.step_error
        return self.step_calls >= self.steps_before_done

    def _close_once(self):
        self.close_calls += 1
        error = self.close_error
        if error is not None:
            self.close_error = None
            raise error
        self.screen.events.append(self.screen.name + ":lease.close")
        self.screen._lease = None
        self.closed = True
        return True

    def close(self):
        return self._close_once()


class _PrimaryAwareLease(_Lease):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.primary_errors = []

    def close_with_primary(self, primary_error):
        self.primary_errors.append(primary_error)
        try:
            restored = self._close_once()
        except MemoryError:
            if isinstance(primary_error, MemoryError):
                raise primary_error
            raise
        except BaseException:
            if primary_error is not None:
                raise primary_error
            raise
        if primary_error is not None:
            raise primary_error
        return restored

    def close(self):
        raise AssertionError("primary-aware close contract was bypassed")


class _ScenarioScreen(_Screen):
    def __init__(self, name, events=None, steps_before_done=1,
                 step_error=None, close_error=None):
        super().__init__(name, events)
        self.steps_before_done = steps_before_done
        self.step_error = step_error
        self.close_error = close_error
        self._lease = None
        self.open_calls = 0
        self.lease = None
        self.lease_type = _Lease

    def activate(self):
        self._record("activate")
        if self._lease is not None:
            raise AssertionError("prepared navigation must skip activate")

    def _open(self, kind):
        if self._lease is not None:
            raise RuntimeError("already active")
        self.open_calls += 1
        lease = self.lease_type(
            self,
            kind,
            steps_before_done=self.steps_before_done,
            step_error=self.step_error,
            close_error=self.close_error,
        )
        self._lease = lease
        self.lease = lease
        return lease

    def open_scenario_transaction(self):
        return self._open("standard")


class _PageScenarioScreen(_ScenarioScreen):
    def open_scenario_transaction(self):
        raise AssertionError("page screens must use the page lease")

    def open_page_scenario_transaction(self):
        return self._open("page")


class _Keyboard:
    def __init__(self, events):
        self.events = list(events)
        self.pop_calls = 0
        self.any_pressed_calls = 0

    def any_pressed(self):
        self.any_pressed_calls += 1
        return False

    def pop_key_event(self):
        self.pop_calls += 1
        if not self.events:
            return None
        return self.events.pop(0)


class _TrackingNav(Nav):
    __slots__ = ("prepared_go_to_calls", "go_back_calls")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prepared_go_to_calls = 0
        self.go_back_calls = 0

    def _go_to_prepared(self, screen, transaction, trigger_event=None):
        self.prepared_go_to_calls += 1
        return super()._go_to_prepared(screen, transaction, trigger_event)

    def _go_back_prepared(self, transaction, trigger_event=None):
        self.go_back_calls += 1
        return super()._go_back_prepared(transaction, trigger_event)

    def go_back(self, trigger_event=None):
        self.go_back_calls += 1
        return super().go_back(trigger_event)


def _nav(monkeypatch):
    monkeypatch.setattr(ui.renderer, "Renderer", _RendererStub)
    monkeypatch.setattr(ui.sidebar, "Sidebar", lambda font, registry: object())
    return _TrackingNav(None, None, object(), memory=_MemoryStub())


def _canonical_pages(events=None):
    root = _Screen("root", events)
    calculator = _PageScenarioScreen("calculator", events)
    plot = _PageScenarioScreen("plot", events)
    addons = _ScenarioScreen("addons", events)
    stopwatch = _PageScenarioScreen("stopwatch", events)
    settings = _ScenarioScreen("settings", events)
    about = _ScenarioScreen("about", events)
    letters = _ScenarioScreen("letters", events)
    catalog = _ScenarioScreen("catalog", events)
    variables = _ScenarioScreen("variables", events)
    canonical = (
        root, calculator, plot, addons, stopwatch, settings, about, letters,
        catalog, variables)
    cases = (
        (PAGE_SCENARIO_CALCULATOR, calculator, "page"),
        (PAGE_SCENARIO_PLOT, plot, "page"),
        (PAGE_SCENARIO_ADDONS, addons, "standard"),
        (PAGE_SCENARIO_STOPWATCH, stopwatch, "page"),
        (PAGE_SCENARIO_SETTINGS, settings, "standard"),
        (PAGE_SCENARIO_ABOUT, about, "standard"),
        (PAGE_SCENARIO_LETTERS, letters, "standard"),
        (PAGE_SCENARIO_FUNCTION_PICKER, catalog, "standard"),
        (PAGE_SCENARIO_VARIABLE_PANEL, variables, "standard"),
    )
    return root, canonical, cases


def _open_at_child(nav, transaction, action, root, child, lease_kind):
    open_calls_before = child.open_calls
    prepared_go_to_calls_before = nav.prepared_go_to_calls
    go_back_calls_before = nav.go_back_calls
    child_calls_before = list(child.calls)
    assert transaction.step(action) is False
    assert child.open_calls == open_calls_before + 1
    assert child.lease.kind == lease_kind
    assert nav.current is root

    assert transaction.step(action) is False
    assert nav.current is child
    assert nav.prepared_go_to_calls == prepared_go_to_calls_before + 1
    assert nav.go_back_calls == go_back_calls_before
    assert child.calls == child_calls_before


def _finish_round_trip(transaction, action, nav, root, child):
    while child.lease.step_calls < child.steps_before_done:
        assert transaction.step(action) is False
    assert transaction.step(action) is False
    assert child.lease.close_calls == 1
    assert transaction.step(action) is True
    assert nav.current is root


def _fail_next_release(screen, error):
    original = screen.release_memory
    pending = [error]

    def release_memory():
        released = original()
        if pending:
            raise pending.pop(0)
        return released

    screen.release_memory = release_memory


def test_page_action_ids_follow_canonical_binding_order():
    assert (
        PAGE_SCENARIO_CALCULATOR,
        PAGE_SCENARIO_PLOT,
        PAGE_SCENARIO_ADDONS,
        PAGE_SCENARIO_STOPWATCH,
        PAGE_SCENARIO_SETTINGS,
        PAGE_SCENARIO_ABOUT,
        PAGE_SCENARIO_LETTERS,
        PAGE_SCENARIO_FUNCTION_PICKER,
        PAGE_SCENARIO_VARIABLE_PANEL,
    ) == tuple(range(1, 10))


def test_target_lazily_imports_page_scenario_transaction(monkeypatch):
    class LazyTransaction:
        def __init__(self, nav, canonical_screens):
            self.nav = nav
            self.canonical_screens = canonical_screens

    lazy_module = types.ModuleType("nav_scenario")
    lazy_module._NavPageScenarioTransaction = LazyTransaction
    monkeypatch.setitem(sys.modules, "nav_scenario", lazy_module)
    monkeypatch.setattr(
        main_module, "_NavPageScenarioTransaction", None)
    nav = _nav(monkeypatch)
    root, canonical, _cases = _canonical_pages()
    nav.boot(root)

    transaction = nav.open_page_scenario_transaction(canonical)

    assert type(transaction) is LazyTransaction
    assert transaction.nav is nav
    assert transaction.canonical_screens is canonical
    assert nav._page_scenario_transaction is transaction
    assert main_module._NavPageScenarioTransaction is LazyTransaction


@pytest.mark.parametrize("case_index", range(9))
def test_every_canonical_page_uses_one_prepared_round_trip(
        monkeypatch, case_index):
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    action, child, lease_kind = cases[case_index]
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)

    _open_at_child(nav, transaction, action, root, child, lease_kind)
    _finish_round_trip(transaction, action, nav, root, child)

    assert child.lease.closed is True
    assert child._lease is None
    assert nav.go_back_calls == 1
    assert "activate" not in child.calls
    for _, other, _ in cases:
        assert other.open_calls == (1 if other is child else 0)
    assert transaction.close() is True


def test_page_scenario_requires_the_canonical_root_and_distinct_pages(
        monkeypatch):
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    detour = _Screen("detour")
    nav.boot(root)
    nav.go_to(detour)

    with pytest.raises(RuntimeError, match="canonical root"):
        nav.open_page_scenario_transaction(canonical)

    assert all(child.open_calls == 0 for _, child, _ in cases)
    assert nav._page_scenario_transaction is None

    nav.go_back()
    with pytest.raises(RuntimeError, match="Canonical resident screens"):
        nav.open_page_scenario_transaction(canonical[:-1])

    duplicate = list(canonical)
    duplicate[2] = duplicate[1]
    with pytest.raises(RuntimeError, match="Canonical resident screens"):
        nav.open_page_scenario_transaction(tuple(duplicate))

    assert all(child.open_calls == 0 for _, child, _ in cases)


def test_page_scenario_rejects_unknown_and_changed_actions_without_navigation(
        monkeypatch):
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    calculator = cases[0][1]
    plot = cases[1][1]
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)

    with pytest.raises(ValueError, match="Unknown page scenario action"):
        transaction.step(True)
    with pytest.raises(ValueError, match="Unknown page scenario action"):
        transaction.step(0)

    assert transaction.step(PAGE_SCENARIO_CALCULATOR) is False
    with pytest.raises(RuntimeError, match="action changed"):
        transaction.step(PAGE_SCENARIO_PLOT)

    assert calculator.open_calls == 1
    assert plot.open_calls == 0
    assert nav.current is root
    assert transaction.close() is True


def test_prepared_navigation_requires_the_active_transaction_and_screen(
        monkeypatch):
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    calculator = cases[0][1]
    plot = cases[1][1]
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)
    assert transaction.step(PAGE_SCENARIO_CALCULATOR) is False

    with pytest.raises(RuntimeError, match="Prepared page scenario lease"):
        nav._go_to_prepared(plot, transaction)
    with pytest.raises(RuntimeError, match="Prepared page scenario lease"):
        nav._go_to_prepared(calculator, object())

    assert nav.current is root
    assert calculator.calls == []
    assert root.calls == ["activate"]
    assert transaction.step(PAGE_SCENARIO_CALCULATOR) is False
    assert nav.current is calculator
    assert transaction.close() is True


def test_normal_go_to_stays_normal_without_a_page_transaction(monkeypatch):
    nav = _nav(monkeypatch)
    root = _Screen("root")
    normal = _Screen("normal")
    nav.boot(root)

    nav.go_to(normal)

    assert nav.current is normal
    assert normal.calls == ["activate"]
    assert nav.prepared_go_to_calls == 0


def test_page_scenario_blocks_ordinary_navigation_and_input_until_close(
        monkeypatch):
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    calculator = cases[0][1]
    foreign = _Screen("foreign")
    keyboard = _Keyboard(((3, 2, False),))
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)

    assert transaction.step(PAGE_SCENARIO_CALCULATOR) is False
    lease = calculator.lease
    assert nav.poll_event(keyboard) is None
    with pytest.raises(RuntimeError, match="owns navigation"):
        nav.go_to(foreign)
    with pytest.raises(RuntimeError, match="owns navigation"):
        nav.go_back()

    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is root
    assert transaction._child_lease is lease
    assert calculator._lease is lease
    assert lease.close_calls == 0
    assert keyboard.pop_calls == 0
    assert keyboard.any_pressed_calls == 0

    assert transaction.close() is True
    assert nav._page_scenario_transaction is None
    assert lease.close_calls == 1
    assert nav.poll_event(keyboard) == (3, 2, False)

    nav.go_to(foreign)
    nav.go_back()
    assert nav.current is root


@pytest.mark.parametrize(
    ("primary_type", "cleanup_type", "cleanup_wins"),
    (
        (MemoryError, RuntimeError, False),
        (MemoryError, MemoryError, False),
        (RuntimeError, MemoryError, True),
        (RuntimeError, RuntimeError, False),
    ),
)
def test_go_to_deactivate_failure_clears_stale_active_screen_and_retries(
        monkeypatch, primary_type, cleanup_type, cleanup_wins):
    primary = primary_type("injected departure deactivate failure")
    cleanup = cleanup_type("injected departure rollback failure")
    nav = _nav(monkeypatch)
    root = _FaultingScreen("root", deactivate_errors=(primary,))
    child = _Screen("child")
    nav.boot(root)
    root.activate_errors.append(cleanup)

    expected = cleanup if cleanup_wins else primary
    with pytest.raises(type(expected)) as raised:
        nav.go_to(child)

    assert raised.value is expected
    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is None
    assert root.calls == ["activate", "deactivate", "activate"]
    assert child.calls == []

    nav.go_to(child)
    assert nav.current is child
    assert nav._active_screen is child


@pytest.mark.parametrize(
    ("primary_type", "cleanup_type", "cleanup_wins"),
    (
        (MemoryError, RuntimeError, False),
        (MemoryError, MemoryError, False),
        (RuntimeError, MemoryError, True),
        (RuntimeError, RuntimeError, False),
    ),
)
def test_go_back_deactivate_failure_clears_stale_active_screen_and_retries(
        monkeypatch, primary_type, cleanup_type, cleanup_wins):
    primary = primary_type("injected return deactivate failure")
    cleanup = cleanup_type("injected return rollback failure")
    nav = _nav(monkeypatch)
    root = _Screen("root")
    child = _FaultingScreen("child")
    nav.boot(root)
    nav.go_to(child)
    child.deactivate_errors.append(primary)
    child.activate_errors.append(cleanup)

    expected = cleanup if cleanup_wins else primary
    with pytest.raises(type(expected)) as raised:
        nav.go_back()

    assert raised.value is expected
    assert nav.stack == [root, child]
    assert nav.current is child
    assert nav._active_screen is None
    assert child.calls == ["activate", "deactivate", "activate"]

    nav.go_back()
    assert nav.current is root
    assert nav._active_screen is root


@pytest.mark.parametrize("error_type", (RuntimeError, MemoryError))
def test_go_to_release_failure_restores_the_departing_screen_and_retries(
        monkeypatch, error_type):
    primary = error_type("injected departure release failure")
    nav = _nav(monkeypatch)
    root = _FaultingScreen("root", release_errors=(primary,))
    child = _Screen("child")
    nav.boot(root)

    with pytest.raises(error_type) as raised:
        nav.go_to(child)

    assert raised.value is primary
    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is root
    assert root.calls == [
        "activate", "deactivate", "release_memory", "activate"]
    assert child.calls == []

    nav.go_to(child)
    assert nav.current is child
    assert nav._active_screen is child


def test_go_to_keeps_primary_memory_error_when_reactivation_also_fails(
        monkeypatch):
    primary = MemoryError("injected departure OOM")
    cleanup_error = RuntimeError("injected rollback activation failure")
    nav = _nav(monkeypatch)
    root = _FaultingScreen("root", release_errors=(primary,))
    child = _Screen("child")
    nav.boot(root)
    root.activate_errors.append(cleanup_error)

    with pytest.raises(MemoryError) as raised:
        nav.go_to(child)

    assert raised.value is primary
    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is None
    assert root.calls == [
        "activate", "deactivate", "release_memory", "activate"]

    nav.go_to(child)
    assert nav.current is child
    assert nav._active_screen is child


@pytest.mark.parametrize("error_type", (RuntimeError, MemoryError))
def test_go_back_release_failure_restores_the_child_and_retries(
        monkeypatch, error_type):
    primary = error_type("injected return release failure")
    nav = _nav(monkeypatch)
    root = _Screen("root")
    child = _FaultingScreen("child", release_errors=(primary,))
    nav.boot(root)
    nav.go_to(child)

    with pytest.raises(error_type) as raised:
        nav.go_back()

    assert raised.value is primary
    assert nav.stack == [root, child]
    assert nav.current is child
    assert nav._active_screen is child
    assert child.calls == [
        "activate", "deactivate", "release_memory", "activate"]

    nav.go_back()
    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is root


@pytest.mark.parametrize("error_type", (RuntimeError, MemoryError))
def test_normal_activation_failure_restores_the_departing_screen_and_retries(
        monkeypatch, error_type):
    primary = error_type("injected destination activation failure")
    nav = _nav(monkeypatch)
    root = _Screen("root")
    child = _FaultingScreen("child", activate_errors=(primary,))
    nav.boot(root)

    with pytest.raises(error_type) as raised:
        nav.go_to(child)

    assert raised.value is primary
    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is root
    assert child.calls == ["activate"]
    assert root.calls == [
        "activate", "deactivate", "release_memory", "activate"]

    nav.go_to(child)
    assert nav.current is child
    assert nav._active_screen is child


@pytest.mark.parametrize(
    ("primary_type", "cleanup_wins"),
    (
        (RuntimeError, True),
        (MemoryError, False),
    ),
)
def test_go_back_parent_activation_failure_preserves_oom_and_retries(
        monkeypatch, primary_type, cleanup_wins):
    primary = primary_type("injected parent activation failure")
    cleanup = MemoryError("injected parent rollback OOM")
    child_restore_failure = RuntimeError("injected child rollback failure")
    nav = _nav(monkeypatch)
    root = _FaultingScreen("root")
    child = _FaultingScreen("child")
    nav.boot(root)
    nav.go_to(child)
    root.activate_errors.extend((primary, cleanup))
    child.activate_errors.append(child_restore_failure)

    expected = cleanup if cleanup_wins else primary
    with pytest.raises(type(expected)) as raised:
        nav.go_back()

    assert raised.value is expected
    assert nav.stack == [root, child]
    assert nav.current is child
    assert nav._active_screen is None
    assert root.calls[-2:] == ["activate", "activate"]
    assert child.calls[-3:] == ["deactivate", "release_memory", "activate"]

    nav.go_back()
    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is root


def test_prepared_departure_release_failure_keeps_lease_for_close_retry(
        monkeypatch):
    primary = MemoryError("injected prepared departure OOM")
    nav = _nav(monkeypatch)
    original_root, canonical, cases = _canonical_pages()
    root = _FaultingScreen("root", release_errors=(primary,))
    canonical = (root,) + canonical[1:]
    calculator = cases[0][1]
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)

    assert transaction.step(PAGE_SCENARIO_CALCULATOR) is False
    lease = calculator.lease
    with pytest.raises(MemoryError) as raised:
        transaction.step(PAGE_SCENARIO_CALCULATOR)

    assert raised.value is primary
    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is root
    assert transaction._child_lease is lease
    assert nav._page_scenario_transaction is transaction
    assert transaction.close() is True
    assert lease.close_calls == 1


@pytest.mark.parametrize("error_type", (RuntimeError, MemoryError))
def test_page_scenario_return_release_failure_keeps_close_retryable(
        monkeypatch, error_type):
    primary = error_type("injected page return release failure")
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    settings = cases[4][1]
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)
    _open_at_child(
        nav, transaction, PAGE_SCENARIO_SETTINGS, root, settings,
        "standard")
    lease = settings.lease
    _fail_next_release(settings, primary)

    with pytest.raises(error_type) as raised:
        transaction.close()

    assert raised.value is primary
    assert lease.close_calls == 1
    assert transaction._child_lease is None
    assert transaction._closed is False
    assert nav._page_scenario_transaction is transaction
    assert nav.stack == [root, settings]
    assert nav.current is settings
    assert nav._active_screen is settings

    assert transaction.close() is True
    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is root


@pytest.mark.parametrize("error_type", (RuntimeError, MemoryError))
def test_page_scenario_root_activation_failure_is_not_false_restoration(
        monkeypatch, error_type):
    primary = error_type("injected root activation failure")
    nav = _nav(monkeypatch)
    original_root, canonical, cases = _canonical_pages()
    root = _FaultingScreen("root")
    canonical = (root,) + canonical[1:]
    settings = cases[4][1]
    nav.boot(root)
    root.activate_errors.append(primary)
    transaction = nav.open_page_scenario_transaction(canonical)
    _open_at_child(
        nav, transaction, PAGE_SCENARIO_SETTINGS, root, settings,
        "standard")

    with pytest.raises(error_type) as raised:
        transaction.close()

    assert raised.value is primary
    assert transaction._closed is False
    assert transaction._child_lease is None
    assert nav._page_scenario_transaction is transaction
    assert nav.stack == [root]
    assert nav.current is root
    assert nav._active_screen is root
    assert root.calls[-3:] == ["release_memory", "activate", "activate"]

    assert transaction.close() is True
    assert nav._page_scenario_transaction is None


def test_page_scenario_preserves_memory_error_then_allows_a_fresh_retry(
        monkeypatch):
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    calculator = cases[0][1]
    primary = MemoryError("injected child OOM")
    calculator.step_error = primary
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)

    _open_at_child(
        nav, transaction, PAGE_SCENARIO_CALCULATOR, root, calculator, "page")
    failed_lease = calculator.lease
    with pytest.raises(MemoryError) as raised:
        transaction.step(PAGE_SCENARIO_CALCULATOR)

    assert raised.value is primary
    assert nav.current is calculator
    assert transaction.close() is True
    assert failed_lease.close_calls == 1
    assert nav.current is root

    calculator.step_error = None
    retry = nav.open_page_scenario_transaction(canonical)
    _open_at_child(
        nav, retry, PAGE_SCENARIO_CALCULATOR, root, calculator, "page")
    _finish_round_trip(
        retry, PAGE_SCENARIO_CALCULATOR, nav, root, calculator)
    assert calculator.open_calls == 2
    assert retry.close() is True


def test_page_scenario_propagates_child_error_then_allows_a_fresh_retry(
        monkeypatch):
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    addons = cases[2][1]
    primary = RuntimeError("injected child error")
    addons.step_error = primary
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)

    _open_at_child(
        nav, transaction, PAGE_SCENARIO_ADDONS, root, addons, "standard")
    failed_lease = addons.lease
    with pytest.raises(RuntimeError) as raised:
        transaction.step(PAGE_SCENARIO_ADDONS)

    assert raised.value is primary
    assert transaction.close() is True
    assert failed_lease.close_calls == 1
    assert nav.current is root

    addons.step_error = None
    retry = nav.open_page_scenario_transaction(canonical)
    _open_at_child(
        nav, retry, PAGE_SCENARIO_ADDONS, root, addons, "standard")
    _finish_round_trip(retry, PAGE_SCENARIO_ADDONS, nav, root, addons)
    assert addons.open_calls == 2
    assert retry.close() is True


def test_page_scenario_child_close_failure_is_retryable(monkeypatch):
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    catalog = cases[7][1]
    retry_error = RuntimeError("injected close failure")
    catalog.close_error = retry_error
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)

    _open_at_child(
        nav, transaction, PAGE_SCENARIO_FUNCTION_PICKER, root, catalog,
        "standard")
    with pytest.raises(RuntimeError) as raised:
        transaction.close()

    assert raised.value is retry_error
    assert catalog.lease.close_calls == 1
    assert catalog._lease is catalog.lease
    assert nav.current is catalog
    assert nav.go_back_calls == 0
    assert nav._page_scenario_transaction is transaction

    assert transaction.close() is True
    assert catalog.lease.close_calls == 2
    assert nav.current is root
    assert nav.go_back_calls == 1
    assert transaction.close() is True


@pytest.mark.parametrize(
    ("primary_type", "cleanup_type", "cleanup_wins"),
    (
        (RuntimeError, MemoryError, True),
        (MemoryError, MemoryError, False),
        (RuntimeError, RuntimeError, False),
        (RuntimeError, None, False),
    ),
)
def test_page_scenario_close_with_primary_selects_zero_arg_lease_error(
        monkeypatch, primary_type, cleanup_type, cleanup_wins):
    primary = primary_type("injected page action failure")
    cleanup = (cleanup_type("injected zero-arg lease cleanup failure")
               if cleanup_type is not None else None)
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    calculator = cases[0][1]
    calculator.step_error = primary
    calculator.close_error = cleanup
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)
    _open_at_child(
        nav, transaction, PAGE_SCENARIO_CALCULATOR, root, calculator, "page")
    lease = calculator.lease

    with pytest.raises(primary_type) as action_raised:
        transaction.step(PAGE_SCENARIO_CALCULATOR)

    expected = cleanup if cleanup_wins else primary
    with pytest.raises(type(expected)) as close_raised:
        transaction.close_with_primary(action_raised.value)

    assert action_raised.value is primary
    assert close_raised.value is expected
    assert lease.close_calls == 1
    assert transaction._child_lease is lease
    assert nav.current is calculator

    assert transaction.close() is True
    assert lease.close_calls == 2
    assert nav.current is root


@pytest.mark.parametrize(
    ("primary_type", "cleanup_type", "cleanup_wins"),
    (
        (RuntimeError, MemoryError, True),
        (MemoryError, MemoryError, False),
        (RuntimeError, RuntimeError, False),
        (RuntimeError, None, False),
    ),
)
def test_page_scenario_close_with_primary_uses_public_lease_contract(
        monkeypatch, primary_type, cleanup_type, cleanup_wins):
    primary = primary_type("injected page action failure")
    cleanup = (cleanup_type("injected primary-aware lease cleanup failure")
               if cleanup_type is not None else None)
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    calculator = cases[0][1]
    calculator.lease_type = _PrimaryAwareLease
    calculator.step_error = primary
    calculator.close_error = cleanup
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)
    _open_at_child(
        nav, transaction, PAGE_SCENARIO_CALCULATOR, root, calculator, "page")
    lease = calculator.lease

    with pytest.raises(primary_type) as action_raised:
        transaction.step(PAGE_SCENARIO_CALCULATOR)

    expected = cleanup if cleanup_wins else primary
    with pytest.raises(type(expected)) as close_raised:
        transaction.close_with_primary(action_raised.value)

    assert action_raised.value is primary
    assert close_raised.value is expected
    assert lease.primary_errors == [primary]
    assert transaction._child_lease is lease
    assert nav.current is calculator

    assert transaction.close() is True
    assert lease.primary_errors == [primary, None]
    assert nav.current is root


def test_page_scenario_close_restores_child_before_returning_to_root(
        monkeypatch):
    events = []
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages(events)
    settings = cases[4][1]
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)

    _open_at_child(
        nav, transaction, PAGE_SCENARIO_SETTINGS, root, settings,
        "standard")
    assert transaction.close() is True

    assert events == [
        "root:activate",
        "root:deactivate",
        "root:release_memory",
        "settings:lease.close",
        "settings:deactivate",
        "settings:release_memory",
        "root:activate",
    ]


def test_page_scenario_rejects_an_unexpected_stack_without_cleanup(
        monkeypatch):
    nav = _nav(monkeypatch)
    root, canonical, cases = _canonical_pages()
    calculator = cases[0][1]
    nav.boot(root)
    transaction = nav.open_page_scenario_transaction(canonical)

    _open_at_child(
        nav, transaction, PAGE_SCENARIO_CALCULATOR, root, calculator, "page")
    foreign = _Screen("foreign")
    nav.stack.append(foreign)

    with pytest.raises(RuntimeError, match="navigation state is unexpected"):
        transaction.close()

    assert calculator.lease.close_calls == 0
    assert nav.go_back_calls == 0
    assert nav.current is foreign
    assert nav.stack == [root, calculator, foreign]


def test_page_scenario_static_contract_keeps_controller_and_device_gate_separate():
    main_source = (SOURCE / "main.py").read_text(encoding="utf-8")
    scenario_source = (SOURCE / "nav_scenario.py").read_text(encoding="utf-8")
    transaction_start = scenario_source.index(
        "class _NavPageScenarioTransaction:")
    transaction = scenario_source[transaction_start:]
    runtime_start = main_source.index(
        "runtime = application_binding if run_loop else RuntimeHandle(")
    runtime_end = main_source.index("try:", runtime_start)
    runtime_block = main_source[runtime_start:runtime_end]

    for forbidden in (
            "find_target", "transition_title", "_managed", ".reset(",
            "stack[:]", "scenario_adapter", "getattr(screen"):
        assert forbidden not in transaction
    assert "open_page_scenario_transaction" in transaction
    assert "open_scenario_transaction" in transaction
    assert "class _NavPageScenarioTransaction" not in main_source
    assert "scenario_adapter=None" in runtime_block
    assert "build_resident_application_scenario_adapter" not in runtime_block
    assert "prepare_trusted_resident_scenario_adapter" not in runtime_block
    assert APPLICATION_MATRIX_DEVICE_READY is True
