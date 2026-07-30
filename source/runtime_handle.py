"""Minimal resident runtime identity shared with on-demand acceptance tools."""


_resident_runtime = None


_CANONICAL_SCREEN_COUNT = 10


class ApplicationBinding:
    """Immutable references to the one already-constructed application state."""

    __slots__ = ("_binding_state",)
    _sealed = True

    def __init__(self, screens, registry, settings, persistence, nav=None):
        if not isinstance(screens, tuple):
            raise TypeError("Resident screens must be an existing tuple")
        if (registry is None or settings is None or persistence is None):
            raise ValueError("Application binding requires resident state")
        # One immutable tuple is both smaller and stronger than duplicating ten
        # writable page slots. Public names are absent from __slots__, so an
        # external assignment remains structurally impossible.
        self._binding_state = (
            screens, registry, settings, persistence, nav)

    def __getattr__(self, name):
        state = self._binding_state
        if name == "screens":
            return state[0]
        if name == "registry":
            return state[1]
        if name == "settings":
            return state[2]
        if name == "persistence":
            return state[3]
        if name == "_nav":
            return state[4]
        screens = state[0]
        canonical = len(screens) == _CANONICAL_SCREEN_COUNT
        if name == "root":
            return screens[0] if canonical else None
        if name == "calculator":
            return screens[1] if canonical else None
        if name == "plot":
            return screens[2] if canonical else None
        if name == "function_panel":
            return screens[3] if canonical else None
        if name == "stopwatch":
            return screens[4] if canonical else None
        if name == "settings_screen":
            return screens[5] if canonical else None
        if name == "about":
            return screens[6] if canonical else None
        if name == "letters":
            return screens[7] if canonical else None
        if name == "function_picker":
            return screens[8] if canonical else None
        if name == "variables":
            return screens[9] if canonical else None
        raise AttributeError("Application binding attribute is unavailable")

    def require_canonical_screens(self, expected_root=None):
        """Return this binding only for the one canonical resident topology."""
        screens = self._binding_state[0]
        if (not isinstance(screens, tuple)
                or len(screens) != _CANONICAL_SCREEN_COUNT):
            raise RuntimeError("Canonical resident screens are unavailable")
        if expected_root is not None and screens[0] is not expected_root:
            raise RuntimeError("Canonical resident screens are unavailable")
        for index in range(_CANONICAL_SCREEN_COUNT):
            screen = screens[index]
            if screen is None:
                raise RuntimeError("Canonical resident screens are unavailable")
            for earlier in range(index):
                if screen is screens[earlier]:
                    raise RuntimeError("Canonical resident screens are unavailable")
        return self

    def open_page_scenario_transaction(self):
        """Open Nav's canonical page transaction without exposing Nav."""
        self.require_canonical_screens()
        opener = getattr(
            self._binding_state[4], "open_page_scenario_transaction", None)
        if not callable(opener):
            raise RuntimeError("Canonical page transaction is unavailable")
        return opener(self.screens)


def set_resident_runtime(runtime):
    if runtime is not None and getattr(runtime, "mode", None) != "resident":
        raise ValueError("Resident runtime must use resident mode")
    global _resident_runtime
    _resident_runtime = runtime
    return runtime


def get_resident_runtime():
    return _resident_runtime
