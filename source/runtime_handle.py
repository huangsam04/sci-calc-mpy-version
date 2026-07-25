"""Minimal resident runtime identity shared with on-demand acceptance tools."""


_resident_runtime = None


class RuntimeHandle:
    """Identity-stable view of one already-constructed calculator runtime."""

    __slots__ = (
        "nav", "root", "targets", "mode", "version", "optional_buffers",
        "optional_buffer_target", "scenario_adapter")

    def __init__(
            self, nav, root, targets, mode="resident", version=None,
            optional_buffers=(), optional_buffer_target=None,
            scenario_adapter=None):
        self.nav = nav
        self.root = root
        self.targets = targets if isinstance(targets, tuple) else tuple(targets)
        self.mode = mode
        self.version = version
        self.optional_buffers = (
            optional_buffers
            if isinstance(optional_buffers, tuple)
            else tuple(optional_buffers))
        self.optional_buffer_target = optional_buffer_target
        self.scenario_adapter = scenario_adapter

    def at_root(self):
        return getattr(self.nav, "current", None) is self.root

    def root_visible(self):
        renderer = getattr(self.nav, "renderer", None)
        return getattr(renderer, "_visible_screen", None) is self.root

    def reset_root(self, present=True):
        if not self.at_root():
            self.nav.reset(self.root)
        if present:
            self.nav.present_current()

    def find_target(self, name):
        for target in self.targets:
            title = getattr(
                target, "transition_title", target.__class__.__name__)
            if title == name:
                return target
        return None

    def perform(self, action, argument=None):
        adapter = self.scenario_adapter
        if adapter is None:
            raise RuntimeError("Runtime scenario adapter is unavailable")
        return adapter.perform(self, action, argument)

    def buffer_snapshot(self):
        snapshot = []
        renderer = getattr(self.nav, "renderer", None)
        display = getattr(renderer, "display", None)
        main_buffer = getattr(display, "gs4_buf", None)
        if main_buffer is not None:
            snapshot.append(("main", len(main_buffer), id(main_buffer)))

        memory = getattr(self.nav, "memory", None)
        buffers = getattr(memory, "_buffers", None)
        if buffers:
            for name, value in buffers.items():
                if name != "main" or main_buffer is None:
                    snapshot.append((name, len(value), id(value)))
        snapshot.sort()
        return tuple(snapshot)

    def accepts_buffer_snapshot(self, baseline, snapshot, optional_target):
        for item in baseline:
            if item not in snapshot:
                return False
        if len(snapshot) == len(baseline):
            return True
        if (optional_target is None
                or optional_target is not self.optional_buffer_target):
            return False
        for name, length, identity in snapshot:
            item = (name, length, identity)
            if item in baseline:
                continue
            allowed = False
            for optional_name, optional_length in self.optional_buffers:
                if name == optional_name and length == optional_length:
                    allowed = True
                    break
            if not allowed:
                return False
        return True


def set_resident_runtime(runtime):
    if runtime is not None and getattr(runtime, "mode", None) != "resident":
        raise ValueError("Resident runtime must use resident mode")
    global _resident_runtime
    _resident_runtime = runtime
    return runtime


def get_resident_runtime():
    return _resident_runtime
