"""Build the device-acceptance view only when a caller requests it."""

from runtime_handle import ApplicationBinding


class RuntimeHandle:
    """Identity-stable view of one already-constructed calculator runtime."""

    __slots__ = (
        "nav", "root", "targets", "mode", "version", "_runtime_state")
    _application_binding_sealed = True
    _scenario_adapter_sealed = True

    def __init__(
            self, nav, root, targets, mode="resident", version=None,
            optional_buffer_size=0, optional_buffer_target=None,
            scenario_adapter=None, application_binding=None):
        self.nav = nav
        self.root = root
        self.targets = targets if isinstance(targets, tuple) else tuple(targets)
        self.mode = mode
        self.version = version
        if (optional_buffer_size and optional_buffer_target is None
                and isinstance(application_binding, ApplicationBinding)):
            optional_buffer_target = application_binding.plot
        # Acceptance permits exactly one rebuildable Plot curve buffer. Keep
        # its target and size beside the two existing runtime references so
        # boot does not allocate an allow-list plus its nested row tuple.
        self._runtime_state = (
            scenario_adapter, application_binding,
            optional_buffer_target, optional_buffer_size)

    def __getattr__(self, name):
        if name == "scenario_adapter":
            return self._runtime_state[0]
        if name == "application_binding":
            return self._runtime_state[1]
        if name == "optional_buffer_target":
            return self._runtime_state[2]
        if name == "optional_buffer_size":
            return self._runtime_state[3]
        raise AttributeError("Runtime handle attribute is unavailable")

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
        adapter = self._runtime_state[0]
        if adapter is None:
            raise RuntimeError("Runtime scenario adapter is unavailable")
        return adapter.perform(self, action, argument)

    def require_application_binding(self):
        binding = self._runtime_state[1]
        if not isinstance(binding, ApplicationBinding):
            raise RuntimeError("Runtime application binding is unavailable")
        bound_nav = binding._nav
        # A canonical resident binding is only trustworthy when it was built
        # alongside this exact Nav and root. Smaller host-only bindings are
        # allowed to omit Nav because they cannot pass canonical validation.
        if ((bound_nav is None
             and getattr(self, "mode", None) == "resident"
             and binding.root is not None)
                or (bound_nav is not None
                    and (bound_nav is not self.nav
                         or binding.root is not self.root))):
            raise RuntimeError("Runtime application binding is foreign")
        return binding

    def require_resident_application_adapter(self):
        """Return only the trusted adapter built for this exact binding."""
        binding = self.require_application_binding()
        adapter = self._runtime_state[0]
        try:
            from runtime_trusted_construction import (
                require_trusted_resident_application_adapter)

            trusted_adapter = require_trusted_resident_application_adapter(
                binding, adapter)
        except MemoryError:
            raise
        except Exception:
            raise RuntimeError("Resident application adapter is unavailable")
        if trusted_adapter is not adapter:
            raise RuntimeError("Resident application adapter is unavailable")
        return adapter

    def buffer_snapshot(self):
        snapshot = []
        renderer = getattr(self.nav, "renderer", None)
        display = getattr(renderer, "display", None)
        main_buffer = getattr(display, "gs4_buf", None)
        if main_buffer is not None:
            snapshot.append(("main", len(main_buffer), id(main_buffer)))

        memory = getattr(self.nav, "memory", None)
        plot_workspace = getattr(memory, "_plot_curve", None)
        if plot_workspace is not None:
            snapshot.append((
                "plot_curve", len(plot_workspace), id(plot_workspace)))
        snapshot.sort()
        return tuple(snapshot)

    def accepts_buffer_snapshot(self, baseline, snapshot, optional_target):
        for item in baseline:
            if item not in snapshot:
                return False
        if len(snapshot) == len(baseline):
            return True
        if len(snapshot) != len(baseline) + 1:
            return False
        state = self._runtime_state
        if (optional_target is None
                or optional_target is not state[2]):
            return False
        for item in snapshot:
            if item in baseline:
                continue
            return item[0] == "plot_curve" and item[1] == state[3]
        return False


def build_runtime(binding, mode="resident"):
    if not isinstance(binding, ApplicationBinding):
        raise RuntimeError("Resident application binding is unavailable")
    binding.require_canonical_screens()
    nav = binding._nav
    if nav is None:
        raise RuntimeError("Resident application binding is unavailable")
    from version import VERSION
    screens = binding.screens
    return RuntimeHandle(
        nav, screens[0], screens, mode=mode, version=VERSION,
        optional_buffer_size=104, application_binding=binding)


def get_resident_runtime():
    from runtime_handle import (
        get_resident_runtime as get_registered_runtime,
        set_resident_runtime)

    resident = get_registered_runtime()
    if resident is None or isinstance(resident, RuntimeHandle):
        return resident
    if not isinstance(resident, ApplicationBinding):
        raise RuntimeError("Resident application binding is unavailable")
    import gc
    gc.collect()
    runtime = build_runtime(resident)
    set_resident_runtime(runtime)
    return runtime
