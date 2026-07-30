"""Bounded scratch-variable transaction for resident acceptance work.

This module deliberately owns no persistence path.  Its one scratch table is
only a short-lived substitute for the Calculator context's live variables,
so quota and restart pressure can be exercised without copying or writing the
user's table.
"""

from calc.limits import (MAX_VARIABLES, MAX_VARIABLE_TEXT_LENGTH,
                         is_ascii_identifier)
from calc.number import Number


VARIABLES_SCENARIO_FILL = 1
VARIABLES_SCENARIO_QUOTA = 2
VARIABLES_SCENARIO_RESTART = 3
VARIABLES_SCENARIO_DELETE = 4
VARIABLES_SCENARIO_REFILL = 5

# The resident controller advances this exact scalar-only sequence one
# physical step at a time: 32 fills, one rejected quota write, one restart,
# then refill/delete/refill.  Keep its observable total aligned with the
# application acceptance matrix without retaining a per-operation plan.
VARIABLES_SCENARIO_OPERATION_COUNT = MAX_VARIABLES + 5


class VariablesScenarioTransaction:
    """One reversible, bounded variable workload over a Calculator screen.

    The transaction retains the user table by identity and never snapshots its
    entries.  It first allocates an empty scratch dict, then reserves the
    Calculator transaction before swapping ``context.variables``.  This keeps
    an allocation failure before all visible or durable state changes.
    """

    __slots__ = (
        "_calculator", "_calculator_transaction", "_context",
        "_user_variables", "_user_dirty", "_scratch",
        "_variables_restored", "_canonical_operations", "_closed")

    def __init__(self, calculator):
        try:
            context = calculator.context
        except AttributeError:
            raise RuntimeError("Calculator context is unavailable")
        variables = context.variables
        if not isinstance(variables, dict):
            raise RuntimeError("Calculator variables are unavailable")
        if len(variables) > MAX_VARIABLES:
            raise RuntimeError("Calculator variables exceed the fixed limit")
        user_dirty = context.dirty

        # Reserve the only scenario-owned collection before opening the
        # Calculator lease.  No user variable entries are copied here.
        scratch = {}
        calculator_transaction = calculator.open_scenario_transaction()

        self._calculator = calculator
        self._calculator_transaction = calculator_transaction
        self._context = context
        self._user_variables = variables
        self._user_dirty = user_dirty
        self._scratch = scratch
        self._variables_restored = False
        self._canonical_operations = 0
        self._closed = False
        context.variables = scratch
        context.dirty = False

    def _require_open(self):
        context = self._context
        if self._closed or context is None:
            raise RuntimeError("Variables scenario transaction is closed")
        if self._variables_restored:
            raise RuntimeError("Variables scenario transaction is closing")
        scratch = self._scratch
        if context.variables is not scratch:
            raise RuntimeError("Variables scenario scratch state changed")
        if len(scratch) > MAX_VARIABLES:
            raise RuntimeError("Variables scenario scratch exceeds its limit")
        return context, scratch

    @staticmethod
    def _require_quota_candidate(name, value):
        """Reject malformed input before treating a ValueError as quota full."""
        if not is_ascii_identifier(name):
            raise ValueError("Variables scenario name is invalid")
        if isinstance(value, bool):
            raise ValueError("Variables scenario value is invalid")
        if not isinstance(value, (Number, int, float, str)):
            raise ValueError("Variables scenario value is invalid")
        if isinstance(value, str) and len(value) > MAX_VARIABLE_TEXT_LENGTH:
            raise ValueError("Variables scenario value is invalid")

    def _insert(self, name, value, action):
        context, scratch = self._require_open()
        if name in scratch:
            raise RuntimeError("Variables scenario " + action
                               + " requires a new scratch variable")
        if len(scratch) >= MAX_VARIABLES:
            raise RuntimeError("Variables scenario scratch is full")
        # EvalContext owns validation, coercion and dirty-state semantics.  A
        # MemoryError remains primary and only the scratch table can change.
        context.set_var(name, value)
        if name not in scratch:
            raise RuntimeError("Variables scenario " + action
                               + " did not create a scratch variable")
        # Scratch mutations must never turn into an idle persistence request.
        context.dirty = False
        return True

    def _quota(self, name, value):
        context, scratch = self._require_open()
        if len(scratch) != MAX_VARIABLES or name in scratch:
            raise RuntimeError("Variables scenario quota requires a full table")
        self._require_quota_candidate(name, value)
        try:
            context.set_var(name, value)
        except ValueError:
            if name in scratch or len(scratch) != MAX_VARIABLES:
                raise RuntimeError("Variables scenario quota changed scratch")
            return True
        raise RuntimeError("Variables scenario quota accepted an extra item")

    def _restart(self):
        context, scratch = self._require_open()
        # ``clear`` has a fixed maximum of MAX_VARIABLES entries and retains
        # this transaction's one scratch allocation for the refill phase.
        scratch.clear()
        context.dirty = False
        return True

    def _delete(self, name):
        context, scratch = self._require_open()
        if name not in scratch:
            raise RuntimeError("Variables scenario delete requires an entry")
        context.delete_var(name)
        if name in scratch:
            raise RuntimeError("Variables scenario delete did not remove entry")
        context.dirty = False
        return True

    def step(self, action, name=None, value=None):
        """Perform exactly one bounded scratch-table action."""
        if action == VARIABLES_SCENARIO_FILL:
            return self._insert(name, value, "fill")
        if action == VARIABLES_SCENARIO_QUOTA:
            return self._quota(name, value)
        if action == VARIABLES_SCENARIO_RESTART:
            return self._restart()
        if action == VARIABLES_SCENARIO_DELETE:
            return self._delete(name)
        if action == VARIABLES_SCENARIO_REFILL:
            return self._insert(name, value, "refill")
        raise ValueError("Unknown variables scenario action")

    @property
    def canonical_operations_completed(self):
        """Return the fixed-controller operations proved by this transaction."""
        return self._canonical_operations

    @property
    def canonical_complete(self):
        """Return whether every fixed controller operation completed."""
        return (self._canonical_operations
                == VARIABLES_SCENARIO_OPERATION_COUNT)

    def _require_canonical_terminal(self):
        """Reject a disturbed scratch table before certifying the sequence."""
        _context, scratch = self._require_open()
        if (len(scratch) != 1
                or "replacement" not in scratch
                or scratch.get("replacement") != 100):
            raise RuntimeError("Variables scenario canonical proof changed")

    def step_canonical_operation(self, operation_index):
        """Advance one fixed 37-operation controller primitive.

        The caller supplies the physical operation index, but this transaction
        owns the scalar progress proof and refuses skips, repeats, or a second
        terminal step.  It deliberately delegates every mutation to the
        existing public ``step`` primitive, keeping the scratch/no-persistence
        and close-with-primary contracts in one place.
        """
        self._require_open()
        if (isinstance(operation_index, bool)
                or not isinstance(operation_index, int)
                or operation_index < 0
                or operation_index >= VARIABLES_SCENARIO_OPERATION_COUNT):
            raise ValueError("Variables scenario operation index is invalid")

        expected = self._canonical_operations
        if expected == VARIABLES_SCENARIO_OPERATION_COUNT:
            raise RuntimeError("Variables scenario canonical sequence is complete")
        if operation_index != expected:
            raise RuntimeError("Variables scenario canonical order changed")

        if expected < MAX_VARIABLES:
            completed = self.step(
                VARIABLES_SCENARIO_FILL, "v" + str(expected), expected)
        elif expected == MAX_VARIABLES:
            completed = self.step(
                VARIABLES_SCENARIO_QUOTA, "overflow", MAX_VARIABLES)
        elif expected == MAX_VARIABLES + 1:
            completed = self.step(VARIABLES_SCENARIO_RESTART)
        elif expected == MAX_VARIABLES + 2:
            completed = self.step(VARIABLES_SCENARIO_REFILL, "v0", 0)
        elif expected == MAX_VARIABLES + 3:
            completed = self.step(VARIABLES_SCENARIO_DELETE, "v0")
        else:
            completed = self.step(
                VARIABLES_SCENARIO_REFILL, "replacement", 100)

        if completed is not True:
            raise RuntimeError("Variables scenario canonical operation failed")
        if expected + 1 == VARIABLES_SCENARIO_OPERATION_COUNT:
            self._require_canonical_terminal()
        self._canonical_operations = expected + 1
        return True

    def _close(self):
        """Restore user state, then close the Calculator lease retry-safely."""
        if self._closed:
            return True
        context = self._context
        if context is None:
            raise RuntimeError("Variables scenario transaction is closed")

        # If Calculator cleanup later fails, retain the lease and this scalar
        # checkpoint so a later close retries only that cleanup.  The user
        # table is already restored and cannot be poisoned by scratch work.
        if not self._variables_restored:
            context.variables = self._user_variables
            context.dirty = self._user_dirty
            self._variables_restored = True

        calculator_transaction = self._calculator_transaction
        if calculator_transaction is None:
            raise RuntimeError("Calculator scenario transaction is unavailable")
        # This transaction owns the authoritative primary error.  Passing no
        # primary here makes a nested cleanup fault visible to the wrapper
        # below, which can then preserve an action OOM or promote cleanup OOM
        # over an ordinary action failure while retaining retry state.
        restored = calculator_transaction.close_with_primary(None)
        if restored is not True:
            raise RuntimeError("Calculator scenario transaction restore failed")

        self._calculator_transaction = None
        self._calculator = None
        self._context = None
        self._user_variables = None
        self._scratch = None
        self._closed = True
        return True

    def close_with_primary(self, primary_error):
        """Close without letting cleanup hide the action that caused it.

        A primary ``MemoryError`` remains the exact raised object.  A cleanup
        ``MemoryError`` instead replaces an ordinary primary failure, so a
        caller cannot report restored state after an out-of-memory cleanup
        fault.  Failed cleanup leaves this transaction's guard and retained
        references available for a later retry.
        """
        try:
            self._close()
        except MemoryError:
            if isinstance(primary_error, MemoryError):
                raise primary_error
            raise
        except Exception:
            if primary_error is not None:
                raise primary_error
            raise
        if primary_error is not None:
            raise primary_error
        return True

    def close(self):
        """Restore user state; zero-argument callers stay compatible."""
        return self.close_with_primary(None)


def open_variables_scenario_transaction(calculator):
    """Open the one bounded variable transaction for a resident Calculator."""
    return VariablesScenarioTransaction(calculator)
