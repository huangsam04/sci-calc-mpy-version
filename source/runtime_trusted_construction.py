"""Fail-closed construction for a resident scenario adapter.

This module intentionally owns only the short interval between creating the
real resident adapter and publishing its ``RuntimeHandle``.  Once
``seal_runtime()`` succeeds, the construction record releases both references;
the runtime, adapter, and controller retain the only long-lived application
references.
"""

from runtime_scenarios import ResidentApplicationScenarioAdapter


class _TrustedResidentApplicationScenarioAdapter(
        ResidentApplicationScenarioAdapter):
    """A marked adapter whose provenance is consumed by one construction."""

    __slots__ = ("_construction_sealed",)

    def __init__(self, controller):
        ResidentApplicationScenarioAdapter.__init__(self, controller)
        self._construction_sealed = False


class TrustedResidentScenarioConstruction:
    """One-use proof that an adapter and runtime share one sealed binding."""

    __slots__ = ("_binding", "_adapter", "_controller", "_sealed")

    def __init__(self, binding, adapter, controller):
        self._sealed = False
        self._binding = binding
        self._adapter = adapter
        self._controller = controller
        self._sealed = True

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Trusted runtime construction is immutable")
        object.__setattr__(self, name, value)

    @property
    def adapter(self):
        """Return the one adapter during the pre-publication window."""
        adapter = self._adapter
        if adapter is None:
            raise RuntimeError("Trusted runtime construction is already sealed")
        return adapter

    def seal_runtime(self, runtime):
        """Authenticate and consume this construction before runtime publication.

        A successful call returns ``runtime`` and clears this record's temporary
        references.  Any mismatch leaves the record intact so construction has
        not silently published an unverified runtime.
        """
        binding = self._binding
        adapter = self._adapter
        controller = self._controller
        if binding is None or adapter is None or controller is None:
            raise RuntimeError("Trusted runtime construction is already sealed")

        from runtime_materialize import RuntimeHandle

        if type(runtime) is not RuntimeHandle:
            raise TypeError("Trusted runtime construction requires RuntimeHandle")
        if runtime.mode != "resident":
            raise RuntimeError("Trusted runtime must use resident mode")
        if runtime.application_binding is not binding:
            raise RuntimeError("Trusted runtime binding is foreign")
        if runtime.scenario_adapter is not adapter:
            raise RuntimeError("Trusted runtime adapter is foreign")
        if (getattr(runtime, "_application_binding_sealed", None) is not True
                or getattr(runtime, "_scenario_adapter_sealed", None) is not True):
            raise RuntimeError("Trusted runtime fields are not sealed")

        if runtime.require_application_binding() is not binding:
            raise RuntimeError("Trusted runtime binding is foreign")
        if binding.require_canonical_screens(runtime.root) is not binding:
            raise RuntimeError("Canonical resident screens are unavailable")
        if getattr(adapter, "_construction_sealed", None) is not False:
            raise RuntimeError("Trusted runtime adapter is already sealed")
        if _require_marked_resident_application_adapter(binding, adapter) is not adapter:
            raise RuntimeError("Trusted runtime adapter is unavailable")
        try:
            actual_controller = (
                adapter.require_resident_application_binding(binding))
        except MemoryError:
            raise
        except Exception:
            raise RuntimeError("Trusted runtime adapter is unavailable")
        if actual_controller is not controller:
            raise RuntimeError("Trusted runtime controller is foreign")

        # This scalar is the persistent proof consumed by RuntimeHandle's
        # device gate.  It is set only after every pre-publication identity
        # check succeeds, then needs no construction-record references.
        object.__setattr__(adapter, "_construction_sealed", True)

        # Do not retain a second binding or adapter reference after publication.
        object.__setattr__(self, "_binding", None)
        object.__setattr__(self, "_adapter", None)
        object.__setattr__(self, "_controller", None)
        return runtime


def _require_trusted_construction_binding(binding):
    """Return only a sealed canonical binding with its owned resident Nav."""
    from runtime_handle import ApplicationBinding

    if type(binding) is not ApplicationBinding:
        raise TypeError("Trusted construction requires ApplicationBinding")
    if getattr(binding, "_sealed", None) is not True:
        raise RuntimeError("Application binding is not immutable")
    if binding.require_canonical_screens() is not binding:
        raise RuntimeError("Canonical resident screens are unavailable")
    if binding._nav is None:
        raise RuntimeError("Canonical resident navigation is unavailable")
    return binding


def build_trusted_resident_application_scenario_adapter(binding):
    """Build the real adapter with one bounded trusted-provenance scalar."""
    binding = _require_trusted_construction_binding(binding)

    return _build_marked_resident_application_scenario_adapter(binding)


def build_compatible_resident_application_scenario_adapter(binding):
    """Build the marked real adapter without moving legacy host failure timing."""
    return _build_marked_resident_application_scenario_adapter(binding)


def _build_marked_resident_application_scenario_adapter(binding):
    """Construct the one real controller without allocating a base adapter first."""

    from runtime_application_controller import (
        _ResidentApplicationScenarioController)

    controller = _ResidentApplicationScenarioController(binding)
    adapter = _TrustedResidentApplicationScenarioAdapter(controller)
    try:
        actual_controller = adapter.require_resident_application_binding(binding)
    except MemoryError:
        raise
    except Exception:
        raise RuntimeError("Trusted resident adapter is unavailable")
    if actual_controller is not controller:
        raise RuntimeError("Trusted resident adapter is unavailable")
    return adapter


def require_trusted_resident_application_adapter(binding, adapter):
    """Return only an adapter made by the trusted resident construction path."""
    adapter = _require_marked_resident_application_adapter(binding, adapter)
    if getattr(adapter, "_construction_sealed", None) is not True:
        raise RuntimeError("Trusted resident adapter is not sealed")
    return adapter


def _require_marked_resident_application_adapter(binding, adapter):
    """Validate the fixed binding/controller marker before seal-state checks."""
    binding = _require_trusted_construction_binding(binding)
    if type(adapter) is not _TrustedResidentApplicationScenarioAdapter:
        raise RuntimeError("Trusted resident adapter is unavailable")
    try:
        controller = adapter.require_resident_application_binding(binding)
    except MemoryError:
        raise
    except Exception:
        raise RuntimeError("Trusted resident adapter is unavailable")
    if controller is None:
        raise RuntimeError("Trusted resident adapter is unavailable")
    return adapter


def prepare_trusted_resident_scenario_adapter(binding):
    """Construct one real adapter from one canonical immutable binding.

    This does not construct a runtime or open a scenario transaction.  The
    caller passes ``construction.adapter`` to ``RuntimeHandle`` and must call
    ``construction.seal_runtime(runtime)`` before publishing that runtime.
    """
    adapter = build_trusted_resident_application_scenario_adapter(binding)
    try:
        controller = adapter.require_resident_application_binding(binding)
    except MemoryError:
        raise
    except Exception:
        raise RuntimeError("Trusted resident adapter is unavailable")
    if controller is None:
        raise RuntimeError("Trusted resident adapter is unavailable")
    return TrustedResidentScenarioConstruction(binding, adapter, controller)
