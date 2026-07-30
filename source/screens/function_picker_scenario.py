"""Function-picker acceptance transaction, imported only on demand."""

# Eight add-ons may each register sixteen functions; sixty-four built-ins leave
# room for the resident catalog without turning a corrupted registry into an
# unbounded scenario allocation.
MAX_SCENARIO_FUNCTION_NAMES = 192
_OPERATIONS_PER_STEP = 1280


class FunctionPickerScenarioTransaction:
    """Incrementally rebuild one function catalog without a second snapshot."""

    __slots__ = (
        "_screen", "_closed", "_registry", "_revision", "_count",
        "_iterator", "_pending_name", "_insert_at", "_complete",
        "_saved_cursor", "_saved_offset", "_saved_notice")

    @staticmethod
    def _current_registry(screen):
        return screen._state[0].context.registry

    def __init__(self, screen):
        state = screen._state
        if state[5] is not None:
            raise RuntimeError(
                "Function picker scenario transaction is already active")
        registry = self._current_registry(screen)
        revision = getattr(registry, "revision", None)
        count = len(registry)
        if count > MAX_SCENARIO_FUNCTION_NAMES:
            raise RuntimeError(
                "Function picker scenario catalog exceeds its limit")
        # Allocate the one replacement list and iterator before invalidating
        # the resident view.  An OOM here leaves the visible panel unchanged.
        names = []
        iterator = iter(registry.keys())

        self._screen = screen
        self._closed = False
        self._registry = registry
        self._revision = revision
        self._count = count
        self._iterator = iterator
        self._pending_name = None
        self._insert_at = 0
        self._complete = False
        self._saved_cursor = state[2]
        self._saved_offset = state[3]
        self._saved_notice = state[4]

        # Replace the derived catalog before incremental insertion so there is
        # never a second full name list.
        state[5] = self
        state[1] = names
        state[2] = 0
        state[3] = 0
        state[4] = ""

    def _require_open(self):
        screen = self._screen
        if self._closed or screen is None:
            raise RuntimeError(
                "Function picker scenario transaction is closed")
        if screen._state[5] is not self:
            raise RuntimeError(
                "Function picker scenario transaction is not active")
        return screen

    def _require_unchanged_source(self, screen):
        registry = self._current_registry(screen)
        if (registry is not self._registry
                or getattr(registry, "revision", None) != self._revision
                or len(registry) != self._count):
            raise RuntimeError("Function picker scenario registry changed")

    def step(self):
        """Run one fixed batch of the allocation-free insertion sort."""
        screen = self._require_open()
        if self._complete:
            return True
        self._require_unchanged_source(screen)
        names = screen._state[1]
        for _ in range(_OPERATIONS_PER_STEP):
            pending = self._pending_name
            if pending is not None:
                position = self._insert_at
                if position > 0 and names[position - 1] > pending:
                    names[position] = names[position - 1]
                    self._insert_at = position - 1
                else:
                    names[position] = pending
                    self._pending_name = None
                continue
            try:
                name = next(self._iterator)
            except StopIteration:
                self._complete = True
                return True
            if not isinstance(name, str):
                raise RuntimeError("Function picker scenario name is invalid")
            if len(names) >= MAX_SCENARIO_FUNCTION_NAMES:
                raise RuntimeError(
                    "Function picker scenario catalog exceeds its limit")
            names.append(name)
            self._pending_name = name
            self._insert_at = len(names) - 1
        return False

    def close(self):
        """Restore panel scalars and discard any incomplete derived catalog."""
        if self._closed:
            return True
        screen = self._require_open()
        if not self._complete:
            # Keep the boot-reserved list backing usable by the next ordinary
            # activation while discarding an incomplete derived catalog.
            screen._state[1][:] = ()
        screen._state[2] = self._saved_cursor
        screen._state[3] = self._saved_offset
        screen._state[4] = self._saved_notice
        screen._state[5] = None
        self._screen = None
        self._registry = None
        self._iterator = None
        self._pending_name = None
        self._saved_notice = None
        self._closed = True
        return True
