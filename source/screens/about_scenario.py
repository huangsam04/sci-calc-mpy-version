"""About-page acceptance transaction, imported only on demand."""


class AboutScenarioTransaction:
    """A tiny prepared-visibility lease for the static About page."""

    __slots__ = ("_screen", "_closed", "_complete")

    def __init__(self, screen):
        if screen._scenario_transaction is not None:
            raise RuntimeError("About scenario transaction is already active")
        if not isinstance(screen.version, str):
            raise RuntimeError("About scenario version is unavailable")
        self._screen = screen
        self._closed = False
        self._complete = False
        screen._scenario_transaction = self

    def _require_open(self):
        screen = self._screen
        if self._closed or screen is None:
            raise RuntimeError("About scenario transaction is closed")
        if screen._scenario_transaction is not self:
            raise RuntimeError("About scenario transaction is not active")
        return screen

    def _prepare_visible_state(self, screen):
        if not isinstance(screen.version, str):
            raise RuntimeError("About scenario version is unavailable")
        return True

    def step(self):
        """Prepare the fixed visible page state without rendering it."""
        screen = self._require_open()
        if self._complete:
            return True
        self._prepare_visible_state(screen)
        self._complete = True
        return True

    def close(self):
        """Release only the scenario marker; static content remains resident."""
        if self._closed:
            return True
        screen = self._require_open()
        screen._scenario_transaction = None
        self._screen = None
        self._closed = True
        return True
