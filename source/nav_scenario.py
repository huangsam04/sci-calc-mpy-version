"""Canonical page acceptance transaction, imported only on demand."""

PAGE_SCENARIO_CALCULATOR = 1
PAGE_SCENARIO_PLOT = 2
PAGE_SCENARIO_ADDONS = 3
PAGE_SCENARIO_STOPWATCH = 4
PAGE_SCENARIO_SETTINGS = 5
PAGE_SCENARIO_ABOUT = 6
PAGE_SCENARIO_LETTERS = 7
PAGE_SCENARIO_FUNCTION_PICKER = 8
PAGE_SCENARIO_VARIABLE_PANEL = 9

# Keep the screen vocabulary available without adding alternate action values.
PAGE_SCENARIO_FUNCTION_PANEL = PAGE_SCENARIO_ADDONS
PAGE_SCENARIO_CATALOG = PAGE_SCENARIO_FUNCTION_PICKER
PAGE_SCENARIO_VARIABLES = PAGE_SCENARIO_VARIABLE_PANEL

_PAGE_SCENARIO_READY = 0
_PAGE_SCENARIO_LEASE_OPEN = 1
_PAGE_SCENARIO_CHILD_ACTIVE = 2
_PAGE_SCENARIO_LEASE_DONE = 3
_PAGE_SCENARIO_LEASE_CLOSED = 4


class _NavPageScenarioTransaction:
    """One no-copy, retryable canonical-page round-trip transaction.

    This keeps direct references to the canonical root and each eligible
    resident page.  It never derives targets from Nav's lifecycle registry or
    takes a navigation-stack snapshot.  A physical ``step`` performs only
    one of: opening a child lease, prepared navigation, one child lease step,
    closing that lease, or the ordinary return navigation.
    """

    __slots__ = (
        "_nav", "_root", "_calculator", "_plot", "_function_panel",
        "_stopwatch", "_settings", "_about", "_letter_panel",
        "_function_picker", "_variables", "_prepared_screen",
        "_child_lease", "_action", "_phase", "_closed")

    def __init__(self, nav, canonical_screens):
        if (not isinstance(canonical_screens, tuple)
                or len(canonical_screens) != 10):
            raise RuntimeError("Canonical resident screens are unavailable")

        root = canonical_screens[0]
        calculator = canonical_screens[1]
        plot = canonical_screens[2]
        function_panel = canonical_screens[3]
        stopwatch = canonical_screens[4]
        settings = canonical_screens[5]
        about = canonical_screens[6]
        letter_panel = canonical_screens[7]
        function_picker = canonical_screens[8]
        variables = canonical_screens[9]
        if (root is None or calculator is None or plot is None
                or function_panel is None or stopwatch is None
                or settings is None or about is None or letter_panel is None
                or function_picker is None or variables is None):
            raise RuntimeError("Canonical resident screens are unavailable")
        for index in range(10):
            screen = canonical_screens[index]
            if screen is None:
                raise RuntimeError("Canonical resident screens are unavailable")
            for earlier in range(index):
                if screen is canonical_screens[earlier]:
                    raise RuntimeError("Canonical resident screens are unavailable")

        # A scenario begins only at the one known root stack.  This is a
        # scalar identity check, not a clone or recovery walk of Nav's stack.
        if (len(nav.stack) != 1 or nav.current is not root
                or nav.stack[0] is not root):
            raise RuntimeError("Page scenario requires the canonical root")

        self._nav = nav
        self._root = root
        self._calculator = calculator
        self._plot = plot
        self._function_panel = function_panel
        self._stopwatch = stopwatch
        self._settings = settings
        self._about = about
        self._letter_panel = letter_panel
        self._function_picker = function_picker
        self._variables = variables
        self._prepared_screen = None
        self._child_lease = None
        self._action = 0
        self._phase = _PAGE_SCENARIO_READY
        self._closed = False

    def _require_open(self):
        nav = self._nav
        if (self._closed or nav is None
                or nav._page_scenario_transaction is not self):
            raise RuntimeError("Page scenario transaction is closed")
        return nav

    def _at_root(self, nav):
        return (len(nav.stack) == 1
                and nav.stack[0] is self._root
                and nav.current is self._root
                and nav._active_screen is self._root)

    def _at_root_stack(self, nav):
        return (len(nav.stack) == 1
                and nav.stack[0] is self._root
                and nav.current is self._root)

    def _at_active_child(self, nav):
        child = self._prepared_screen
        return (child is not None
                and len(nav.stack) == 2
                and nav.stack[0] is self._root
                and nav.current is child
                and nav._active_screen is child)

    def _at_child_stack(self, nav):
        child = self._prepared_screen
        return (child is not None
                and len(nav.stack) == 2
                and nav.stack[0] is self._root
                and nav.current is child)

    def _child_for_action(self, action):
        if isinstance(action, bool):
            raise ValueError("Unknown page scenario action")
        if action == PAGE_SCENARIO_CALCULATOR:
            return self._calculator
        if action == PAGE_SCENARIO_PLOT:
            return self._plot
        if action == PAGE_SCENARIO_ADDONS:
            return self._function_panel
        if action == PAGE_SCENARIO_STOPWATCH:
            return self._stopwatch
        if action == PAGE_SCENARIO_SETTINGS:
            return self._settings
        if action == PAGE_SCENARIO_ABOUT:
            return self._about
        if action == PAGE_SCENARIO_LETTERS:
            return self._letter_panel
        if action == PAGE_SCENARIO_FUNCTION_PICKER:
            return self._function_picker
        if action == PAGE_SCENARIO_VARIABLE_PANEL:
            return self._variables
        raise ValueError("Unknown page scenario action")

    def _uses_page_scenario_lease(self, action):
        return (action == PAGE_SCENARIO_CALCULATOR
                or action == PAGE_SCENARIO_PLOT
                or action == PAGE_SCENARIO_STOPWATCH)

    def _open_child_lease(self, child, action):
        if self._uses_page_scenario_lease(action):
            return child.open_page_scenario_transaction()
        return child.open_scenario_transaction()

    def _close_child_lease_with_primary(self, lease, primary_error):
        """Close through the lease's public primary-aware contract."""
        close_with_primary = getattr(lease, "close_with_primary", None)
        try:
            if callable(close_with_primary):
                restored = close_with_primary(primary_error)
            else:
                restored = lease.close()
        except MemoryError:
            if isinstance(primary_error, MemoryError):
                raise primary_error
            raise
        except BaseException:
            if primary_error is not None:
                raise primary_error
            raise
        if restored is not True:
            if primary_error is not None:
                raise primary_error
            raise RuntimeError("Page scenario child restore failed")
        if primary_error is not None:
            raise primary_error
        return True

    def _require_known_location(self, nav):
        if self._at_root_stack(nav):
            return False
        if self._at_child_stack(nav):
            return True
        raise RuntimeError("Page scenario navigation state is unexpected")

    def _clear_action(self):
        self._prepared_screen = None
        self._child_lease = None
        self._action = 0
        self._phase = _PAGE_SCENARIO_READY

    def step(self, action):
        """Advance one physical action for one requested auxiliary page."""
        nav = self._require_open()
        child = self._child_for_action(action)
        phase = self._phase

        if phase == _PAGE_SCENARIO_READY:
            if not self._at_root(nav):
                raise RuntimeError("Page scenario requires the canonical root")
            # The child allocates its one bounded scratch view before Nav
            # deactivates the root, so an OOM leaves the visible root intact.
            lease = self._open_child_lease(child, action)
            self._prepared_screen = child
            self._child_lease = lease
            self._action = action
            self._phase = _PAGE_SCENARIO_LEASE_OPEN
            return False

        if action != self._action or child is not self._prepared_screen:
            raise RuntimeError("Page scenario action changed while active")

        if phase == _PAGE_SCENARIO_LEASE_OPEN:
            if not self._at_root(nav):
                raise RuntimeError("Page scenario navigation state is unexpected")
            nav._go_to_prepared(child, self)
            if not self._at_active_child(nav):
                raise RuntimeError("Page scenario navigation state is unexpected")
            self._phase = _PAGE_SCENARIO_CHILD_ACTIVE
            return False

        if phase == _PAGE_SCENARIO_CHILD_ACTIVE:
            if not self._at_active_child(nav):
                raise RuntimeError("Page scenario navigation state is unexpected")
            done = self._child_lease.step()
            if done is True:
                self._phase = _PAGE_SCENARIO_LEASE_DONE
            elif done is not False:
                raise RuntimeError("Page scenario child step returned invalid state")
            return False

        if phase == _PAGE_SCENARIO_LEASE_DONE:
            if not self._at_active_child(nav):
                raise RuntimeError("Page scenario navigation state is unexpected")
            restored = self._child_lease.close()
            if restored is not True:
                raise RuntimeError("Page scenario child restore failed")
            self._child_lease = None
            self._phase = _PAGE_SCENARIO_LEASE_CLOSED
            return False

        if phase == _PAGE_SCENARIO_LEASE_CLOSED:
            if self._at_root_stack(nav):
                # A prior return may have restored the root after reporting
                # its original activation failure.  Retry its activation
                # before declaring this transaction complete.
                nav._go_back_prepared(self)
                if not self._at_root(nav):
                    raise RuntimeError("Page scenario navigation state is unexpected")
                self._clear_action()
                return True
            if not self._at_child_stack(nav):
                raise RuntimeError("Page scenario navigation state is unexpected")
            nav._go_back_prepared(self)
            if not self._at_root(nav):
                raise RuntimeError("Page scenario navigation state is unexpected")
            self._clear_action()
            return True

        raise RuntimeError("Page scenario transaction state is invalid")

    def close_with_primary(self, primary_error):
        """Close a partial action without hiding its triggering failure."""
        if self._closed:
            if primary_error is not None:
                raise primary_error
            return True
        nav = self._require_open()
        at_child = self._require_known_location(nav)
        lease = self._child_lease

        # A child must restore its own guard and derived state before ordinary
        # Nav teardown can call its normal release path.  Failed close keeps
        # the same lease and active child for a later retry.
        if lease is not None:
            restored = self._close_child_lease_with_primary(
                lease, primary_error)
            if restored is not True:
                raise RuntimeError("Page scenario child restore failed")
            self._child_lease = None
            self._phase = (_PAGE_SCENARIO_LEASE_CLOSED
                           if at_child else _PAGE_SCENARIO_READY)

        if at_child:
            nav._go_back_prepared(self)
            if not self._at_root(nav):
                raise RuntimeError("Page scenario navigation state is unexpected")

        if self._at_root_stack(nav) and not self._at_root(nav):
            nav._go_back_prepared(self)
        if not self._at_root(nav):
            raise RuntimeError("Page scenario navigation state is unexpected")
        self._clear_action()
        nav._page_scenario_transaction = None
        self._nav = None
        self._root = None
        self._calculator = None
        self._plot = None
        self._function_panel = None
        self._stopwatch = None
        self._settings = None
        self._about = None
        self._letter_panel = None
        self._function_picker = None
        self._variables = None
        self._closed = True
        if primary_error is not None:
            raise primary_error
        return True

    def close(self):
        """Close a partial action without a preceding action failure."""
        return self.close_with_primary(None)
