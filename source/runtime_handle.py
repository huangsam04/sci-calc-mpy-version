"""Minimal resident runtime identity shared with on-demand acceptance tools."""


_resident_runtime = None


class ApplicationBinding:
    """Immutable binding to Nav-owned pages and shared application state."""

    __slots__ = ("_binding_state",)
    _sealed = True

    def __init__(self, nav, root, registry, settings, persistence):
        if (nav is None or root is None or registry is None
                or settings is None or persistence is None):
            raise ValueError("Application binding requires resident state")
        self._binding_state = (
            nav, root, registry, settings, persistence)

    def __getattr__(self, name):
        state = self._binding_state
        if name == "_nav":
            return state[0]
        if name == "root":
            return state[1]
        if name == "registry":
            return state[2]
        if name == "settings":
            return state[3]
        if name == "persistence":
            return state[4]
        raise AttributeError("Application binding attribute is unavailable")

    def require_page_owner(self, expected_root=None):
        """Return this binding only for its exact Nav/root identity."""
        nav, root = self._binding_state[0:2]
        if (expected_root is not None and root is not expected_root):
            raise RuntimeError("Resident page owner is unavailable")
        stack = getattr(nav, "stack", None)
        if not isinstance(stack, list):
            raise RuntimeError("Resident page owner is unavailable")
        if stack and stack[0] is not root:
            raise RuntimeError("Resident page owner is unavailable")
        return self

def set_resident_runtime(runtime):
    if runtime is not None and getattr(runtime, "mode", None) != "resident":
        raise ValueError("Resident runtime must use resident mode")
    global _resident_runtime
    _resident_runtime = runtime
    return runtime


def get_resident_runtime():
    return _resident_runtime
