"""Heavy Calculator acceptance transaction, imported only on demand."""

CALCULATOR_SCENARIO_HISTORY = 1
CALCULATOR_SCENARIO_ERROR_SHOW = 2
CALCULATOR_SCENARIO_ERROR_DISMISS = 3
CALCULATOR_SCENARIO_HISTORY_CURSOR = 4
CALCULATOR_SCENARIO_ERROR_KIND = 5
CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE = -1
CALCULATOR_SCENARIO_HISTORY_CURSOR_FORWARD = 1
_CALCULATOR_SCENARIO_HISTORY_LIMIT = 20
_CALCULATOR_SCENARIO_ERROR_LIMIT = 20
CALCULATOR_SCENARIO_ERROR_KIND_COUNT = 20
_CALCULATOR_SCENARIO_SNAPSHOT_TEXT_LIMIT = 96
_CALCULATOR_SCENARIO_SNAPSHOT_TEXT_ERROR = (
    "Calculator scenario text snapshot exceeds its bounded limit")

# These are real Calculator inputs, rather than host-only error codes.  Every
# item drives the ordinary parser/evaluator and ErrorPopup lifecycle.
_CALCULATOR_SCENARIO_ERROR_SOURCES = (
    ".",
    "\"",
    "1.2.3",
    "x y",
    "1+",
    "(",
    ")",
    "1=2",
    "missing",
    "1/0",
    "0^-1",
    "(-1)^.5",
    "sqrt(-1)",
    "ln(0)",
    "asin(2)",
    "max()",
    "max(1,)",
    "max",
    ",",
    ";",
)

# A kind is proven by exact ErrorPopup content, not by the input ordinal.  The
# tables share repeated display strings so this resident diagnostic contract
# does not retain a second copy for every one of the twenty probes.
_CALCULATOR_SCENARIO_DIAGNOSTIC_TITLES = (
    "Unsupported symbol",
    "Incomplete expression",
    "Calculation error",
    "Unknown variable",
    "Cannot divide by zero",
    "Not enough arguments",
)
_CALCULATOR_SCENARIO_DIAGNOSTIC_DETAILS = (
    "Check the highlighted position",
    "Check brackets or quotes",
    "Invalid number",
    "Expected operator, got 'y'",
    "Unexpected end of expression",
    "Unexpected ')'",
    "Left side of '=' must be a variable name",
    "Define it first, e.g. x=2",
    "Change the denominator",
    "Zero cannot have a negative power",
    "Fractional powers require a positive base",
    "Square root requires a non-negative value",
    "Logarithm requires a positive value",
    "asin requires a value from -1 to 1",
    "Check commas and parameters",
    "'max' requires parentheses",
    "Unexpected ','",
    "Unexpected ';'",
)
# (title index, detail index, parser/evaluator position).  These triples are
# deliberately pairwise distinct, including the two shared lifecycle titles.
_CALCULATOR_SCENARIO_ERROR_DIAGNOSTICS = (
    (0, 0, 0),
    (1, 1, 0),
    (2, 2, 3),
    (2, 3, 2),
    (2, 4, 2),
    (2, 4, 1),
    (2, 5, 0),
    (2, 6, 1),
    (3, 7, 0),
    (4, 8, 1),
    (2, 9, 1),
    (2, 10, 4),
    (2, 11, 0),
    (2, 12, 0),
    (2, 13, 0),
    (5, 14, 0),
    (5, 14, 5),
    (2, 15, 0),
    (2, 16, 0),
    (2, 17, 0),
)


class CalculatorScenarioTransaction:
    """One bounded, reversible diagnostic lease over an existing Calculator.

    The lease never clones history entries, registry state, or a framebuffer.
    It retains at most twenty existing history references and scalar editor/
    popup state.  Derived render caches are intentionally released and rebuilt
    lazily after close so the transaction cannot retain two cache windows.
    """

    __slots__ = (
        "_screen", "_history_owner", "_history", "_history_count",
        "_history_steps", "_history_reverse_steps", "_history_forward_steps",
        "_history_cursor_proof", "_error_steps", "_error_kind",
        "_error_kind_mask", "_error_proof", "_closed", "_input_str",
        "_input_cursor_pos", "_input_view_offset", "_input_height",
        "_cursor_x", "_cursor_y", "_cursor_width", "_cursor_height",
        "_cursor_mode", "_cursor_visible", "_cursor_gs", "_mode",
        "_history_cursor", "_history_offset", "_history_notice",
        "_esc_guard", "_storage_error", "_storage_error_time",
        "_popup_expr", "_popup_position", "_popup_title", "_popup_detail",
        "_popup_started", "_popup_active")

    def __init__(self, screen):
        history = screen._state[0]
        if not isinstance(history, list):
            raise RuntimeError("Calculator history is unavailable")
        count = len(history)
        if count > _CALCULATOR_SCENARIO_HISTORY_LIMIT:
            raise RuntimeError("Calculator history exceeds its bounded limit")

        box = screen.input_box
        cursor = box.cursor
        popup = screen._state[1]
        storage_error = screen._state[3][0][3][0]
        # The transaction retains these existing strings while normal open()
        # clears derived state.  Reject an oversized or malformed checkpoint
        # before allocating a snapshot or changing any resident field.
        self._require_snapshot_text(box.str)
        self._require_snapshot_text(popup.expr)
        self._require_snapshot_text(popup.title)
        self._require_snapshot_text(popup.detail)
        self._require_snapshot_text(storage_error)

        # Allocate the fixed reference window before changing any screen state.
        snapshot = [None] * _CALCULATOR_SCENARIO_HISTORY_LIMIT
        index = 0
        while index < count:
            snapshot[index] = history[index]
            index += 1

        self._screen = screen
        self._history_owner = history
        self._history = snapshot
        self._history_count = count
        self._history_steps = 0
        self._history_reverse_steps = 0
        self._history_forward_steps = 0
        self._history_cursor_proof = None
        self._error_steps = 0
        self._error_kind = None
        self._error_kind_mask = 0
        self._error_proof = None
        self._closed = False
        self._input_str = box.str
        self._input_cursor_pos = box.cursor_pos
        self._input_view_offset = box.view_offset
        self._input_height = box.height
        self._cursor_x = cursor.x
        self._cursor_y = cursor.y
        self._cursor_width = cursor.width
        self._cursor_height = cursor.height
        self._cursor_mode = cursor.mode
        self._cursor_visible = cursor.is_visible
        self._cursor_gs = cursor.gs
        self._mode = screen.mode
        self._history_cursor = screen._state[3][1]
        self._history_offset = screen._state[3][2]
        self._history_notice = screen._state[3][0][1]
        self._esc_guard = screen._state[3][0][0]
        self._storage_error = storage_error
        self._storage_error_time = screen._state[3][0][3][1]
        self._popup_expr = popup.expr
        self._popup_position = popup._state[2]
        self._popup_title = popup.title
        self._popup_detail = popup.detail
        self._popup_started = popup._state[3]
        self._popup_active = popup.active

    @property
    def history_steps(self):
        return self._history_steps

    @property
    def history_reverse_steps(self):
        return self._history_reverse_steps

    @property
    def history_forward_steps(self):
        return self._history_forward_steps

    @property
    def history_cursor(self):
        """Return the cursor selected by the most recent traversal action."""
        return self._history_cursor_proof

    @property
    def error_steps(self):
        return self._error_steps

    @property
    def error_kind(self):
        """Return the currently shown fixed error identity, if any."""
        return self._error_kind

    @property
    def error_kind_mask(self):
        """Return the bit proof for fixed error identities already shown."""
        return self._error_kind_mask

    @property
    def error_diagnostic_proof(self):
        """Return the verified canonical popup proof for the active kind."""
        return self._error_proof

    def _require_open(self):
        if self._closed or self._screen is None:
            raise RuntimeError("Calculator scenario transaction is closed")
        return self._screen

    @staticmethod
    def _require_snapshot_text(value):
        if (not isinstance(value, str)
                or len(value) > _CALCULATOR_SCENARIO_SNAPSHOT_TEXT_LIMIT):
            raise RuntimeError(_CALCULATOR_SCENARIO_SNAPSHOT_TEXT_ERROR)

    @staticmethod
    def _require_expression(screen, expression):
        if (not isinstance(expression, str)
                or not expression
                or len(expression) > (
                    (screen.input_box._state[0] >> 19) & 511)):
            raise ValueError("Calculator scenario expression is invalid")

    def _step_history(self, expression):
        screen = self._require_open()
        if self._history_steps >= _CALCULATOR_SCENARIO_HISTORY_LIMIT:
            raise RuntimeError("Calculator scenario history limit reached")
        if screen.mode != 0 or screen._state[1].active:
            raise RuntimeError("Calculator scenario history requires input mode")
        self._require_expression(screen, expression)
        screen.input_box.set_str(expression, immediate=True)
        screen.input_box.move_cursor_end()
        screen._enter()
        if (screen.mode != 0 or not screen._state[0]
                or screen._state[0][0][0] != expression):
            raise RuntimeError("Calculator scenario history action failed")
        self._history_steps += 1

    def _step_history_cursor(self, direction):
        screen = self._require_open()
        if (not isinstance(direction, int)
                or isinstance(direction, bool)
                or direction not in (
                    CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE,
                    CALCULATOR_SCENARIO_HISTORY_CURSOR_FORWARD)):
            raise ValueError("Calculator scenario cursor direction is invalid")
        if screen._state[1].active or screen.mode == 2:
            raise RuntimeError("Calculator scenario history cursor requires input mode")

        count = len(screen._state[0])
        if count != _CALCULATOR_SCENARIO_HISTORY_LIMIT:
            raise RuntimeError("Calculator scenario history cursor requires 20 entries")

        if direction == CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE:
            if self._history_forward_steps:
                raise RuntimeError("Calculator scenario reverse traversal is complete")
            step = self._history_reverse_steps
            if step >= _CALCULATOR_SCENARIO_HISTORY_LIMIT:
                raise RuntimeError("Calculator scenario reverse traversal limit reached")
            index = count - step - 1
        else:
            if self._history_reverse_steps != _CALCULATOR_SCENARIO_HISTORY_LIMIT:
                raise RuntimeError("Calculator scenario forward traversal requires reverse proof")
            step = self._history_forward_steps
            if step >= _CALCULATOR_SCENARIO_HISTORY_LIMIT:
                raise RuntimeError("Calculator scenario forward traversal limit reached")
            index = step

        # The page owns cursor and viewport mutation; the future controller
        # sees only this transaction action and the scalar proof below.
        if not screen._select_scenario_history_index(index):
            raise RuntimeError("Calculator scenario history cursor action failed")
        if screen._state[3][1] != index:
            raise RuntimeError("Calculator scenario history cursor proof failed")
        self._history_cursor_proof = index
        if direction == CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE:
            self._history_reverse_steps = step + 1
        else:
            self._history_forward_steps = step + 1

    def _show_error(self, expression):
        screen = self._require_open()
        if self._error_steps >= _CALCULATOR_SCENARIO_ERROR_LIMIT:
            raise RuntimeError("Calculator scenario error limit reached")
        if screen.mode != 0 or screen._state[1].active:
            raise RuntimeError("Calculator scenario error is already visible")
        self._require_expression(screen, expression)
        screen.input_box.set_str(expression, immediate=True)
        screen.input_box.move_cursor_end()
        screen._enter()
        if screen.mode != 2 or not screen._state[1].active:
            raise RuntimeError("Calculator scenario error action failed")
        return screen

    def _step_error_show(self, expression):
        self._show_error(expression)
        self._error_steps += 1
        self._error_kind = None
        self._error_proof = None

    def _step_error_kind(self, kind):
        if (not isinstance(kind, int)
                or isinstance(kind, bool)
                or kind < 0
                or kind >= CALCULATOR_SCENARIO_ERROR_KIND_COUNT):
            raise ValueError("Calculator scenario error kind is invalid")
        bit = 1 << kind
        if self._error_kind_mask & bit:
            raise RuntimeError("Calculator scenario error kind already shown")
        expression = _CALCULATOR_SCENARIO_ERROR_SOURCES[kind]
        title_index, detail_index, position = (
            _CALCULATOR_SCENARIO_ERROR_DIAGNOSTICS[kind])
        screen = self._show_error(expression)
        popup = screen._state[1]
        if (popup.expr != expression
                or popup._state[2] != position
                or popup.title != (
                    _CALCULATOR_SCENARIO_DIAGNOSTIC_TITLES[title_index])
                or popup.detail != (
                    _CALCULATOR_SCENARIO_DIAGNOSTIC_DETAILS[detail_index])):
            raise RuntimeError("Calculator scenario diagnostic proof failed")
        # Each field has already matched exactly.  This compact scalar is an
        # injective encoding of the canonical title/detail/position triple,
        # not the caller's source index.
        self._error_proof = ((title_index << 12)
                             | (detail_index << 7)
                             | position)
        self._error_steps += 1
        self._error_kind = kind
        self._error_kind_mask |= bit

    def _step_error_dismiss(self):
        screen = self._require_open()
        if screen.mode != 2 or not screen._state[1].active:
            raise RuntimeError("Calculator scenario error is not visible")
        screen._state[1].dismiss()
        screen.mode = 0
        self._error_kind = None
        self._error_proof = None

    def step(self, action, expression=None):
        """Run exactly one history or error-lifecycle primitive.

        ``expression`` is a caller-owned, already-bounded string except for
        ``CALCULATOR_SCENARIO_ERROR_KIND``, whose scalar selects one fixed
        production Calculator error source.  The screen still owns the error
        lifecycle; this transaction exposes no controller object to normal UI.
        """
        if action == CALCULATOR_SCENARIO_HISTORY:
            self._step_history(expression)
        elif action == CALCULATOR_SCENARIO_HISTORY_CURSOR:
            self._step_history_cursor(expression)
        elif action == CALCULATOR_SCENARIO_ERROR_SHOW:
            self._step_error_show(expression)
        elif action == CALCULATOR_SCENARIO_ERROR_KIND:
            self._step_error_kind(expression)
        elif action == CALCULATOR_SCENARIO_ERROR_DISMISS:
            self._step_error_dismiss()
        else:
            raise ValueError("Unknown calculator scenario action")
        return True

    def _restore_history(self, screen):
        history = self._history_owner
        if screen._state[0] is not history:
            screen._state[0] = history
        count = self._history_count
        while len(history) > count:
            history.pop()
        index = len(history)
        while index < count:
            history.append(self._history[index])
            index += 1
        index = 0
        while index < count:
            history[index] = self._history[index]
            index += 1

    def _restore_input(self, screen):
        box = screen.input_box
        cursor = box.cursor
        # Restore semantic editor state directly, then drop derived window
        # caches.  Reformatting only happens on a later ordinary draw.
        box.str = self._input_str
        box.cursor_pos = self._input_cursor_pos
        box.view_offset = self._input_view_offset
        box.height = self._input_height
        box.release_memory()
        cursor.x = self._cursor_x
        cursor.y = self._cursor_y
        cursor.width = self._cursor_width
        cursor.height = self._cursor_height
        cursor.mode = self._cursor_mode
        cursor.is_visible = self._cursor_visible
        cursor.gs = self._cursor_gs

    def _restore_popup(self, screen):
        popup = screen._state[1]
        # Use the existing popup lifecycle before restoring its captured scalar
        # display state.  No new error formatter or adapter is introduced.
        if self._popup_active:
            popup.show_static(self._popup_title, self._popup_detail)
        else:
            popup.dismiss()
        popup.expr = self._popup_expr
        popup._state[2] = self._popup_position
        popup.title = self._popup_title
        popup.detail = self._popup_detail
        popup._state[3] = self._popup_started
        popup.active = self._popup_active

    def _close(self):
        """Restore the original state and release all transaction references."""
        if self._closed:
            return True
        screen = self._require_open()
        # Do not clear the screen guard or snapshot until every restoration
        # assignment succeeds; callers may retry close after a secondary fault.
        self._restore_history(screen)
        self._restore_input(screen)
        self._restore_popup(screen)
        screen.mode = self._mode
        screen._state[3][1] = self._history_cursor
        screen._state[3][2] = self._history_offset
        screen._state[3][0][1] = self._history_notice
        screen._state[3][0][0] = self._esc_guard
        screen._state[3][0][3][0] = self._storage_error
        screen._state[3][0][3][1] = self._storage_error_time
        screen._clear_history_cache()
        screen._clear_presented_editor_state()
        screen._invalidate_footer_cache()
        if screen._state[3][3] is self:
            screen._state[3][3] = None
        self._screen = None
        self._history_owner = None
        self._history = None
        self._history_cursor_proof = None
        self._input_str = None
        self._history_notice = None
        self._storage_error = None
        self._popup_expr = None
        self._popup_title = None
        self._popup_detail = None
        self._error_kind = None
        self._error_proof = None
        self._closed = True
        return True

    def close_with_primary(self, primary_error):
        """Close while preserving the failure that caused cleanup.

        A primary ``MemoryError`` always remains the exact raised object, even
        when restoration also runs out of memory.  For an ordinary primary,
        a cleanup ``MemoryError`` is promoted instead: the guard and snapshots
        remain installed for retry, so the caller cannot report a restored
        screen after an out-of-memory cleanup fault.
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
        """Restore the original state; zero-argument callers stay compatible."""
        return self.close_with_primary(None)
