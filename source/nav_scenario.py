"""Acceptance page round trips over the ordinary Nav lifecycle."""

PAGE_SCENARIO_CALCULATOR = 1
PAGE_SCENARIO_PLOT = 2
PAGE_SCENARIO_ADDONS = 3
PAGE_SCENARIO_STOPWATCH = 4
PAGE_SCENARIO_SETTINGS = 5
PAGE_SCENARIO_ABOUT = 6
PAGE_SCENARIO_LETTERS = 7
PAGE_SCENARIO_FUNCTION_PICKER = 8
PAGE_SCENARIO_VARIABLE_PANEL = 9

PAGE_SCENARIO_FUNCTION_PANEL = PAGE_SCENARIO_ADDONS
PAGE_SCENARIO_CATALOG = PAGE_SCENARIO_FUNCTION_PICKER
PAGE_SCENARIO_VARIABLES = PAGE_SCENARIO_VARIABLE_PANEL

_PAGE_SCENARIO_READY = 0
_PAGE_SCENARIO_PARENT_ACTIVE = 1
_PAGE_SCENARIO_CHILD_ACTIVE = 2
_PAGE_SCENARIO_CHILD_RETURNED = 3


class PageLifecycleScenario:
    """Drive one retryable page round trip through open/back/current only."""

    __slots__ = (
        "_nav", "_root", "_parent", "_child", "_action", "_phase",
        "_closed")

    def __init__(self, nav, root):
        if nav is None or root is None or nav.current is not root:
            raise RuntimeError("Page scenario requires the root")
        self._nav = nav
        self._root = root
        self._parent = None
        self._child = None
        self._action = 0
        self._phase = _PAGE_SCENARIO_READY
        self._closed = False

    def _require_open(self):
        if self._closed or self._nav is None:
            raise RuntimeError("Page scenario transaction is closed")
        return self._nav

    @staticmethod
    def _validate_action(action):
        if (isinstance(action, bool)
                or not isinstance(action, int)
                or action < PAGE_SCENARIO_CALCULATOR
                or action > PAGE_SCENARIO_VARIABLE_PANEL):
            raise ValueError("Unknown page scenario action")
        return action

    def _finish_action(self):
        self._parent = None
        self._child = None
        self._action = 0
        self._phase = _PAGE_SCENARIO_READY

    @staticmethod
    def _uses_calculator_parent(action):
        return action in (
            PAGE_SCENARIO_LETTERS,
            PAGE_SCENARIO_FUNCTION_PICKER,
            PAGE_SCENARIO_VARIABLE_PANEL,
        )

    def step(self, action):
        """Perform at most one ordinary navigation operation."""
        nav = self._require_open()
        self._validate_action(action)
        if self._phase == _PAGE_SCENARIO_READY:
            if nav.current is not self._root:
                raise RuntimeError("Page scenario requires the root")
            uses_parent = self._uses_calculator_parent(action)
            page_id = PAGE_SCENARIO_CALCULATOR if uses_parent else action
            screen = nav.open(page_id)
            if screen is None or nav.current is not screen:
                raise RuntimeError("Page scenario open did not select the page")
            self._action = action
            if uses_parent:
                self._parent = screen
                self._phase = _PAGE_SCENARIO_PARENT_ACTIVE
            else:
                self._child = screen
                self._phase = _PAGE_SCENARIO_CHILD_ACTIVE
            return False

        if action != self._action:
            raise RuntimeError("Page scenario action changed while active")

        if self._phase == _PAGE_SCENARIO_PARENT_ACTIVE:
            if nav.current is not self._parent:
                raise RuntimeError(
                    "Page scenario navigation state is unexpected")
            child = nav.open(action)
            if child is None or nav.current is not child:
                raise RuntimeError("Page scenario open did not select the page")
            self._child = child
            self._phase = _PAGE_SCENARIO_CHILD_ACTIVE
            return False

        if self._phase == _PAGE_SCENARIO_CHILD_ACTIVE:
            if nav.current is not self._child:
                raise RuntimeError(
                    "Page scenario navigation state is unexpected")
            returned = nav.back()
            expected = self._parent if self._parent is not None else self._root
            if returned is not expected or nav.current is not expected:
                raise RuntimeError("Page scenario return selected a foreign page")
            if self._parent is not None:
                self._child = None
                self._phase = _PAGE_SCENARIO_CHILD_RETURNED
                return False
            self._finish_action()
            return True

        if (self._phase != _PAGE_SCENARIO_CHILD_RETURNED
                or nav.current is not self._parent):
            raise RuntimeError("Page scenario navigation state is unexpected")
        returned = nav.back()
        if returned is not self._root or nav.current is not self._root:
            raise RuntimeError("Page scenario return did not restore the root")
        self._finish_action()
        return True

    def close_with_primary(self, primary_error):
        """Return to the root without hiding an action failure."""
        if self._closed:
            if primary_error is not None:
                raise primary_error
            return True
        nav = self._require_open()
        try:
            if self._phase == _PAGE_SCENARIO_CHILD_ACTIVE:
                if nav.current is not self._child:
                    raise RuntimeError(
                        "Page scenario navigation state is unexpected")
                returned = nav.back()
                expected = (
                    self._parent if self._parent is not None else self._root)
                if returned is not expected or nav.current is not expected:
                    raise RuntimeError(
                        "Page scenario return selected a foreign page")
                self._child = None
                self._phase = (
                    _PAGE_SCENARIO_CHILD_RETURNED
                    if self._parent is not None else _PAGE_SCENARIO_READY)
            if self._phase in (
                    _PAGE_SCENARIO_PARENT_ACTIVE,
                    _PAGE_SCENARIO_CHILD_RETURNED):
                if nav.current is not self._parent:
                    raise RuntimeError(
                        "Page scenario navigation state is unexpected")
                returned = nav.back()
                if returned is not self._root or nav.current is not self._root:
                    raise RuntimeError(
                        "Page scenario return did not restore the root")
            elif nav.current is not self._root:
                raise RuntimeError(
                    "Page scenario navigation state is unexpected")
        except MemoryError:
            if isinstance(primary_error, MemoryError):
                raise primary_error
            raise
        except BaseException:
            if primary_error is not None:
                raise primary_error
            raise
        self._finish_action()
        self._nav = None
        self._root = None
        self._closed = True
        if primary_error is not None:
            raise primary_error
        return True

    def close(self):
        return self.close_with_primary(None)
