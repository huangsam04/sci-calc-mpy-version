"""Variable-panel acceptance transaction, imported only on demand."""

from calc.limits import MAX_VARIABLES
from calc.number import Number


def _bounded_variable_count(variables):
    count = len(variables)
    if count > MAX_VARIABLES:
        raise RuntimeError("Variable panel variables exceed the fixed limit")
    return count


def _variable_fingerprint(variables):
    fingerprint = len(variables)
    for name in variables:
        value = variables[name]
        fingerprint ^= id(name) ^ id(value)
        if isinstance(value, Number):
            fingerprint ^= id(value.coefficient) ^ id(value.exponent)
    return fingerprint


class VariablePanelScenarioTransaction:
    """Incrementally rebuild one variable catalog without a second snapshot."""

    __slots__ = (
        "_screen", "_closed", "_variables", "_fingerprint", "_count",
        "_iterator", "_pending_name", "_insert_at", "_complete",
        "_saved_cursor", "_saved_offset", "_saved_notice")

    @staticmethod
    def _current_variables(screen):
        variables = screen._state[0].vars
        if not isinstance(variables, dict):
            raise RuntimeError("Variable panel variables are unavailable")
        return variables

    def __init__(self, screen):
        state = screen._state
        variables = self._current_variables(screen)
        count = _bounded_variable_count(variables)
        fingerprint = _variable_fingerprint(variables)
        # Allocate the replacement list and iterator before invalidating the
        # resident view.  An OOM here therefore leaves it untouched.
        names = []
        iterator = iter(variables)

        self._screen = screen
        self._closed = False
        self._variables = variables
        self._fingerprint = fingerprint
        self._count = count
        self._iterator = iterator
        self._pending_name = None
        self._insert_at = 0
        self._complete = False
        self._saved_cursor = state[2]
        self._saved_offset = state[3]
        self._saved_notice = state[4]

        # The previous name table and its labels are derived state.  Keeping
        # them during insertion sorting would retain two full catalogs.
        state[5] = self
        state[1] = names
        state[2] = 0
        state[3] = 0
        state[4] = ""

    def _require_open(self):
        screen = self._screen
        if self._closed or screen is None:
            raise RuntimeError(
                "Variable panel scenario transaction is closed")
        if screen._state[5] is not self:
            raise RuntimeError(
                "Variable panel scenario transaction is not active")
        return screen

    def _require_unchanged_source(self, screen):
        variables = self._current_variables(screen)
        if (variables is not self._variables
                or _bounded_variable_count(variables) != self._count
                or _variable_fingerprint(variables) != self._fingerprint):
            raise RuntimeError("Variable panel scenario variables changed")

    def step(self):
        """Consume one name or one insertion comparison per physical step."""
        screen = self._require_open()
        if self._complete:
            return True
        self._require_unchanged_source(screen)
        names = screen._state[1]
        pending = self._pending_name
        if pending is not None:
            position = self._insert_at
            if position > 0 and names[position - 1] > pending:
                names[position] = names[position - 1]
                self._insert_at = position - 1
            else:
                names[position] = pending
                self._pending_name = None
            return False
        try:
            name = next(self._iterator)
        except StopIteration:
            self._complete = True
            return True
        if not isinstance(name, str):
            raise RuntimeError("Variable panel scenario name is invalid")
        if len(names) >= MAX_VARIABLES:
            raise RuntimeError(
                "Variable panel scenario catalog exceeds its limit")
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
            screen._state[1] = ()
        screen._state[2] = self._saved_cursor
        screen._state[3] = self._saved_offset
        screen._state[4] = self._saved_notice
        screen._state[5] = None
        self._screen = None
        self._variables = None
        self._iterator = None
        self._pending_name = None
        self._saved_notice = None
        self._closed = True
        return True
