"""Letters acceptance transaction, imported only on demand."""

_LAYER_UPPER = 0


class LetterPanelScenarioTransaction:
    """A no-copy checkpoint around one prepared Letters overlay visit."""

    __slots__ = (
        "_panel", "_target", "_closed", "_complete", "_text", "_layer",
        "_notice", "_target_text",
        "_target_cursor_pos", "_target_view_offset", "_target_cursor_x",
        "_target_cursor_y", "_target_cursor_width", "_target_cursor_height",
        "_target_cursor_mode", "_target_cursor_visible", "_target_cursor_gs")

    def __init__(self, panel):
        state = panel._state
        if state[4] is not None:
            raise RuntimeError("Letters scenario transaction is already active")
        target = state[0]
        if target is None:
            raise RuntimeError("Letters scenario input target is unavailable")
        try:
            cursor = target.cursor
            target_text = target.str
            target_cursor_pos = target.cursor_pos
            target_view_offset = target.view_offset
            release_memory = target.release_memory
            target_cursor_x = cursor.x
            target_cursor_y = cursor.y
            target_cursor_width = cursor.width
            target_cursor_height = cursor.height
            target_cursor_mode = cursor.mode
            target_cursor_visible = cursor.is_visible
            target_cursor_gs = cursor.gs
        except AttributeError:
            raise RuntimeError("Letters scenario input target is unavailable")
        if not isinstance(target_text, str) or not callable(release_memory):
            raise RuntimeError("Letters scenario input target is unavailable")

        self._panel = panel
        self._target = target
        self._closed = False
        self._complete = False
        # Overlay text and the target expression are retained by reference;
        # neither user string is copied into scenario-owned storage.
        self._text = state[1]
        self._layer = state[2]
        self._notice = state[3]
        self._target_text = target_text
        self._target_cursor_pos = target_cursor_pos
        self._target_view_offset = target_view_offset
        self._target_cursor_x = target_cursor_x
        self._target_cursor_y = target_cursor_y
        self._target_cursor_width = target_cursor_width
        self._target_cursor_height = target_cursor_height
        self._target_cursor_mode = target_cursor_mode
        self._target_cursor_visible = target_cursor_visible
        self._target_cursor_gs = target_cursor_gs
        state[4] = self

    def _require_open(self):
        panel = self._panel
        if self._closed or panel is None:
            raise RuntimeError("Letters scenario transaction is closed")
        if panel._state[4] is not self:
            raise RuntimeError("Letters scenario transaction is not active")
        return panel

    def step(self):
        """Prepare an empty overlay draft without touching its input target."""
        panel = self._require_open()
        if panel._state[0] is not self._target:
            raise RuntimeError("Letters scenario input target changed")
        if self._complete:
            return True
        # This mirrors ordinary activation's overlay-only work.  It neither
        # formats/presents pixels nor calls try_insert on the user InputBox.
        panel._set_draft("")
        panel._state[2] = _LAYER_UPPER
        panel._state[3] = ""
        self._complete = True
        return True

    def close(self):
        """Restore the original target, draft and cursor state retry-safely."""
        if self._closed:
            return True
        panel = self._require_open()
        target = self._target
        cursor = target.cursor

        # Restore source-of-truth input scalars first, then invalidate only
        # InputBox's bounded render cache.  This creates no formatted text or
        # duplicate copy of the user expression/draft.
        target.str = self._target_text
        target.cursor_pos = self._target_cursor_pos
        target.view_offset = self._target_view_offset
        cursor.x = self._target_cursor_x
        cursor.y = self._target_cursor_y
        cursor.width = self._target_cursor_width
        cursor.height = self._target_cursor_height
        cursor.mode = self._target_cursor_mode
        cursor.is_visible = self._target_cursor_visible
        cursor.gs = self._target_cursor_gs
        target.release_memory()

        panel._state[0] = target
        panel._state[1] = self._text
        panel._state[2] = self._layer
        panel._state[3] = self._notice
        panel._state[4] = None
        self._panel = None
        self._target = None
        self._text = None
        self._notice = None
        self._target_text = None
        self._closed = True
        return True
