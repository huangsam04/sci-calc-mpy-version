"""Plot acceptance transactions, imported only on demand."""

from screens.plot import MAX_PLOT_EXPRESSION_CHARS, _MEMORY_ERROR_TITLE

PLOT_SCENARIO_PROBE_VALID = 1
PLOT_SCENARIO_PROBE_ORDINARY_ERROR = 2
PLOT_SCENARIO_STATUS_IDLE = 0
PLOT_SCENARIO_STATUS_RUNNING = 1
PLOT_SCENARIO_STATUS_TERMINAL = 2
PLOT_SCENARIO_RESULT_NONE = 0
PLOT_SCENARIO_RESULT_COMPLETE = 1
PLOT_SCENARIO_RESULT_ORDINARY_ERROR = 2
PLOT_SCENARIO_RESULT_MEMORY_ERROR = 3
_PLOT_SCENARIO_VALID_EXPR = "x^2"
_PLOT_SCENARIO_ERROR_EXPR = "1+"
_PLOT_SCENARIO_X_MIN = -2.0
_PLOT_SCENARIO_X_MAX = 2.0
_PLOT_SCENARIO_Y_MIN = -1.0
_PLOT_SCENARIO_Y_MAX = 1.0
_PLOT_SCENARIO_SNAPSHOT_TEXT_ERROR = (
    "Plot scenario text snapshot exceeds its bounded limit")


def _require_plot_snapshot_text(value):
    """Reject malformed or oversized retained text before a lease mutates."""
    if (not isinstance(value, str)
            or len(value) > MAX_PLOT_EXPRESSION_CHARS):
        raise RuntimeError(_PLOT_SCENARIO_SNAPSHOT_TEXT_ERROR)

class PlotScenarioTransaction:
    """A no-copy checkpoint around one bounded Plot pipeline exercise.

    Curves, compiled programs, jobs and the shared plot workspace are derived
    state.  Keeping references to them would retain two curve windows while a
    scenario runs, so this transaction saves only the user-visible scalar
    state and always rebuilds a curve lazily after close.
    """

    __slots__ = (
        "_screen", "_closed", "_expr", "_x_min", "_x_max", "_y_min",
        "_y_max", "_mode", "_overlay_y", "_edit_original",
        "_needs_curve_restore", "_curve_restore_auto_scale", "_input_str",
        "_input_cursor_pos", "_input_view_offset", "_cursor_x", "_cursor_y",
        "_cursor_mode", "_cursor_visible", "_popup_expr", "_popup_position",
        "_popup_title", "_popup_detail", "_popup_started", "_popup_active",
        "_status", "_result")

    def __init__(self, screen):
        box = screen.input_box
        cursor = box.cursor
        popup = screen.error_popup
        expr = screen.expr
        edit_original = screen._state[3][3][0][2]
        input_str = box.str
        popup_expr = popup.expr
        popup_title = popup.title
        popup_detail = popup.detail

        # These references outlive the resident curve/runtime release below.
        # Validate every retained string before claiming the screen, changing
        # a field, or releasing a derived buffer, so an invalid checkpoint
        # leaves the visible Plot and its guard exactly as it was.
        _require_plot_snapshot_text(expr)
        _require_plot_snapshot_text(edit_original)
        _require_plot_snapshot_text(input_str)
        _require_plot_snapshot_text(popup_expr)
        _require_plot_snapshot_text(popup_title)
        _require_plot_snapshot_text(popup_detail)

        self._screen = screen
        self._closed = False
        self._expr = expr
        self._x_min = screen._state[0][0]
        self._x_max = screen._state[0][1]
        self._y_min = screen._state[0][2]
        self._y_max = screen._state[0][3]
        self._mode = screen._state[1][3]
        self._overlay_y = screen._state[3][3][0][3]
        self._edit_original = edit_original
        self._needs_curve_restore = screen._state[2][2]
        self._curve_restore_auto_scale = screen._state[2][3]
        self._input_str = input_str
        self._input_cursor_pos = box.cursor_pos
        self._input_view_offset = box.view_offset
        self._cursor_x = cursor.x
        self._cursor_y = cursor.y
        self._cursor_mode = cursor.mode
        self._cursor_visible = cursor.is_visible
        self._popup_expr = popup_expr
        self._popup_position = popup._state[2]
        self._popup_title = popup_title
        self._popup_detail = popup_detail
        self._popup_started = popup._state[3]
        self._popup_active = popup.active
        self._status = PLOT_SCENARIO_STATUS_IDLE
        self._result = PLOT_SCENARIO_RESULT_NONE

        # Claim the screen before dropping derived state so a second caller
        # cannot install a competing checkpoint around a partially released
        # workspace.  The checkpoint itself deliberately holds no derived
        # curve/program/buffer reference.
        screen._state[1][2] = self
        screen._discard_curve_runtime(release_workspace=True)
        screen._state[3][0] = screen.width
        restore_pending = bool(self._expr) and self._mode != 1
        screen._state[2][2] = restore_pending
        screen._state[2][3] = (
            self._curve_restore_auto_scale
            if restore_pending and self._needs_curve_restore else False)

    def _require_open(self):
        screen = self._screen
        if self._closed or screen is None:
            raise RuntimeError("Plot scenario transaction is closed")
        if screen._state[1][2] is not self:
            raise RuntimeError("Plot scenario transaction is not active")
        return screen

    @property
    def status(self):
        """The public lifecycle state of a controller-selected probe."""
        return self._status

    @property
    def result(self):
        """The compact terminal verdict; ``NONE`` until a probe finishes."""
        return self._result

    @property
    def terminal(self):
        """Whether the selected probe has a stable terminal result."""
        return self._status == PLOT_SCENARIO_STATUS_TERMINAL

    def start_probe(self, probe):
        """Install one fixed controller probe without exposing Plot internals."""
        screen = self._require_open()
        if self._status == PLOT_SCENARIO_STATUS_RUNNING:
            raise RuntimeError("Plot scenario probe is already running")
        if self._result == PLOT_SCENARIO_RESULT_MEMORY_ERROR:
            raise RuntimeError("Plot scenario probe must close after MemoryError")

        if probe == PLOT_SCENARIO_PROBE_VALID:
            expression = _PLOT_SCENARIO_VALID_EXPR
            auto_scale = True
        elif probe == PLOT_SCENARIO_PROBE_ORDINARY_ERROR:
            expression = _PLOT_SCENARIO_ERROR_EXPR
            auto_scale = True
        else:
            raise ValueError("Unknown Plot scenario probe")

        # Discard before changing semantic scalars, so a replacement probe
        # never retains its predecessor's program, job, or curve buffer.
        screen._discard_curve_runtime(release_workspace=True)
        screen.error_popup.release_memory()
        screen.expr = expression
        screen._state[0][0] = _PLOT_SCENARIO_X_MIN
        screen._state[0][1] = _PLOT_SCENARIO_X_MAX
        screen._state[0][2] = _PLOT_SCENARIO_Y_MIN
        screen._state[0][3] = _PLOT_SCENARIO_Y_MAX
        screen._state[1][3] = 0
        screen._state[3][3][0][3] = None
        screen._state[3][0] = screen.width
        screen._state[2][2] = True
        screen._state[2][3] = auto_scale
        self._status = PLOT_SCENARIO_STATUS_RUNNING
        self._result = PLOT_SCENARIO_RESULT_NONE
        return True

    def _record_probe_terminal(self, screen):
        if screen._state[1][3] == 2:
            self._status = PLOT_SCENARIO_STATUS_TERMINAL
            if screen.error_popup.title == _MEMORY_ERROR_TITLE:
                self._result = PLOT_SCENARIO_RESULT_MEMORY_ERROR
            else:
                self._result = PLOT_SCENARIO_RESULT_ORDINARY_ERROR
            return
        # A redraw request alone is only a presentation flag.  A valid probe
        # is complete only once Plot owns the actual curve buffer and its
        # framebuffer; otherwise a mocked/stale SETTLE_REDRAW could certify a
        # curve that was never sampled.
        if (not screen._state[2][2]
                and screen._state[3][1] is None
                and screen._state[2][1] is not None
                and screen._state[2][0] is not None):
            self._status = PLOT_SCENARIO_STATUS_TERMINAL
            self._result = PLOT_SCENARIO_RESULT_COMPLETE

    def step(self):
        """Advance exactly one existing quiet Plot phase without OOM recovery."""
        screen = self._require_open()
        if self._status == PLOT_SCENARIO_STATUS_TERMINAL:
            raise RuntimeError("Plot scenario probe is terminal")
        if self._status != PLOT_SCENARIO_STATUS_RUNNING:
            raise RuntimeError("Plot scenario probe is not running")
        try:
            settled = screen._settle_curve_step(propagate_memory=True)
        except MemoryError:
            self._status = PLOT_SCENARIO_STATUS_TERMINAL
            self._result = PLOT_SCENARIO_RESULT_MEMORY_ERROR
            raise
        if self._status == PLOT_SCENARIO_STATUS_RUNNING:
            self._record_probe_terminal(screen)
        return settled

    def _close(self):
        """Restore semantic Plot state and release transaction references."""
        if self._closed:
            return True
        screen = self._require_open()

        # Keep the transaction guard and checkpoint intact until every restore
        # assignment succeeds.  A caller can then retry close after a cleanup
        # failure without losing the original primary exception.
        screen._discard_curve_runtime(release_workspace=True)
        box = screen.input_box
        cursor = box.cursor
        popup = screen.error_popup
        box.str = self._input_str
        box.cursor_pos = self._input_cursor_pos
        box.view_offset = self._input_view_offset
        box.release_memory()
        cursor.x = self._cursor_x
        cursor.y = self._cursor_y
        cursor.mode = self._cursor_mode
        cursor.is_visible = self._cursor_visible
        popup.expr = self._popup_expr
        popup._state[2] = self._popup_position
        popup.title = self._popup_title
        popup.detail = self._popup_detail
        popup._state[3] = self._popup_started
        popup.active = self._popup_active
        screen.expr = self._expr
        screen._state[0][0] = self._x_min
        screen._state[0][1] = self._x_max
        screen._state[0][2] = self._y_min
        screen._state[0][3] = self._y_max
        screen._state[1][3] = self._mode
        screen._state[3][3][0][3] = self._overlay_y
        screen._state[3][3][0][2] = self._edit_original
        screen._state[3][0] = screen.width
        restore_pending = bool(self._expr) and self._mode != 1
        screen._state[2][2] = restore_pending
        screen._state[2][3] = (
            self._curve_restore_auto_scale
            if restore_pending and self._needs_curve_restore else False)
        screen._clear_presented_editor_state()
        screen._state[1][2] = None
        self._screen = None
        self._expr = None
        self._edit_original = None
        self._input_str = None
        self._popup_expr = None
        self._popup_title = None
        self._popup_detail = None
        self._closed = True
        return True

    def close_with_primary(self, primary_error):
        """Close while preserving the action failure that triggered cleanup.

        An action ``MemoryError`` remains the exact raised object even when
        restoration also runs out of memory.  A cleanup ``MemoryError``
        instead upgrades an ordinary primary failure.  ``_close`` leaves the
        guard and every snapshot intact until all restoration succeeds, so a
        zero-argument retry remains possible after either cleanup fault.
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
