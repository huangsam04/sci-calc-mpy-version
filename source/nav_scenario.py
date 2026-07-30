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
    """One retryable round trip over a single Nav-owned lazy page."""

    __slots__ = (
        "_nav", "_root", "_prepared_screen",
        "_child_lease", "_action", "_phase", "_closed")

    def __init__(self, nav):
        root = nav.current
        if (len(nav.stack) != 1 or nav.current is not root
                or nav.stack[0] is not root):
            raise RuntimeError("Page scenario requires the root")

        self._nav = nav
        self._root = root
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

    def _validate_action(self, action):
        if isinstance(action, bool):
            raise ValueError("Unknown page scenario action")
        if (not isinstance(action, int)
                or action < PAGE_SCENARIO_CALCULATOR
                or action > PAGE_SCENARIO_VARIABLE_PANEL):
            raise ValueError("Unknown page scenario action")
        return action

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
        screen = self._prepared_screen
        action = self._action
        if screen is not None:
            self._nav.release_scenario_page(action, screen)
        self._prepared_screen = None
        self._child_lease = None
        self._action = 0
        self._phase = _PAGE_SCENARIO_READY

    def step(self, action):
        """Advance one physical action for one requested auxiliary page."""
        nav = self._require_open()
        self._validate_action(action)
        phase = self._phase

        if phase == _PAGE_SCENARIO_READY:
            if not self._at_root(nav):
                raise RuntimeError("Page scenario requires the root")
            # The child allocates its one bounded scratch view before Nav
            # deactivates the root, so an OOM leaves the visible root intact.
            child = nav.acquire_scenario_page(action)
            try:
                lease = self._open_child_lease(child, action)
            except BaseException:
                nav.release_scenario_page(action, child)
                raise
            self._prepared_screen = child
            self._child_lease = lease
            self._action = action
            self._phase = _PAGE_SCENARIO_LEASE_OPEN
            return False

        child = self._prepared_screen
        if action != self._action or child is None:
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
        self._closed = True
        if primary_error is not None:
            raise primary_error
        return True

    def close(self):
        """Close a partial action without a preceding action failure."""
        return self.close_with_primary(None)
