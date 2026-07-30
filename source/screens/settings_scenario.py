"""Settings-page acceptance transaction, imported only on demand."""


class SettingsScenarioTransaction:
    """A no-persistence checkpoint around Settings' visible Menu state."""

    __slots__ = (
        "_screen", "_menu", "_closed", "_complete", "_cursor_pos",
        "_view_offset", "_cursor_x", "_cursor_y", "_cursor_width",
        "_cursor_height", "_cursor_mode", "_cursor_visible", "_cursor_gs",
        "_repeat_state",)

    def __init__(self, screen):
        if screen._state[6] is not None:
            raise RuntimeError(
                "Settings scenario transaction is already active")
        if not isinstance(screen._state[4], dict):
            raise RuntimeError("Settings scenario state is unavailable")
        if screen._state[1] is None:
            raise RuntimeError("Settings scenario About target is unavailable")
        menu = screen._state[5]
        if menu is None:
            raise RuntimeError("Settings scenario menu is unavailable")
        cursor = menu.cursor

        self._screen = screen
        self._menu = menu
        self._closed = False
        self._complete = False
        self._cursor_pos = menu.cursor_pos
        self._view_offset = menu.view_offset
        self._cursor_x = cursor.x
        self._cursor_y = cursor.y
        self._cursor_width = cursor.width
        self._cursor_height = cursor.height
        self._cursor_mode = cursor.mode
        self._cursor_visible = cursor.is_visible
        self._cursor_gs = cursor.gs
        self._repeat_state = menu._state[6]
        screen._state[6] = self

    def _require_open(self):
        screen = self._screen
        if self._closed or screen is None:
            raise RuntimeError("Settings scenario transaction is closed")
        if screen._state[6] is not self:
            raise RuntimeError("Settings scenario transaction is not active")
        if screen._state[5] is not self._menu:
            raise RuntimeError("Settings scenario menu changed")
        return screen

    def step(self):
        """Run only Menu's bounded visible-state preparation."""
        screen = self._require_open()
        if self._complete:
            return True
        # This is the same non-persistent work normal activation performs.
        # A font/cache OOM remains primary and close retains this checkpoint.
        screen._state[5].activate()
        self._complete = True
        return True

    def close(self):
        """Restore logical selection and invalidate only derived pixels."""
        if self._closed:
            return True
        screen = self._require_open()
        menu = self._menu
        cursor = menu.cursor
        menu.cursor_pos = self._cursor_pos
        menu.view_offset = self._view_offset
        cursor.x = self._cursor_x
        cursor.y = self._cursor_y
        cursor.width = self._cursor_width
        cursor.height = self._cursor_height
        cursor.mode = self._cursor_mode
        cursor.is_visible = self._cursor_visible
        cursor.gs = self._cursor_gs
        menu._state[6] = self._repeat_state
        # Pixel/footer caches are derived.  Invalidating them avoids retaining
        # stale scenario presentation while preserving every user setting.
        screen._invalidate_scenario_visible_state()
        screen._state[6] = None
        self._screen = None
        self._menu = None
        self._closed = True
        return True
