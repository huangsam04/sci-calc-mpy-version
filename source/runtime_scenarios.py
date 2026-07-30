"""Seven application capabilities behind one bounded acceptance seam.

Device tools select a canonical scenario and consume its verdict.  They never
need screen fields, key coordinates, storage paths, or plugin internals.  The
scenario descriptor resolves the application-owned adapter only when its one
transaction is opened, so it never captures or constructs another runtime.

Diagnostics retain independent heap/drift/buffer baselines.  The matrix uses
one descriptor for all seven ordered capabilities, giving the acceptance
runner a physical action boundary for each controller ``step()``.  The release
gates are open only because the resident controller, trusted adoption, and the
required host/device protections are all present.
"""

from runtime_acceptance import (
    MAX_BOUNDED_NO_PROGRESS_STEPS, MAX_BOUNDED_STEPS, RUN_BOUNDED,
    STEP_DONE, STEP_MORE, STEP_WAIT)


PASS = 1
UNAVAILABLE = 2
FAILED = 3

ACTION_CALCULATOR_HISTORY = 1
ACTION_ERROR_LIFECYCLE = 2
ACTION_VARIABLE_QUOTA_RESTART = 3
ACTION_PLOT_PIPELINE = 4
ACTION_PLUGIN_RELOAD = 5
ACTION_STOPWATCH_LAPS = 6
ACTION_PAGE_ROUND_TRIPS = 7

MAX_CALCULATOR_HISTORY = 20
MAX_CALCULATOR_INPUT = 96
ERROR_KIND_COUNT = 20
STOPWATCH_LAP_COUNT = 20

APPLICATION_PAGE_IDS = (
    "Calculator",
    "Plot",
    "Add-ons",
    "Stopwatch",
    "Settings",
    "About",
    "Letters",
    "Catalog",
    "Variables",
)

APPLICATION_CAPABILITIES = (
    "calculator_history",
    "error_lifecycle",
    "variable_quota_restart",
    "plot_pipeline",
    "plugin_reload",
    "stopwatch_laps",
    "page_round_trips",
)

# These are the observable operation totals of the bounded scenarios.  A
# controller reports their cumulative sum as its completion proof.
APPLICATION_OPERATION_COUNTS = (60, 40, 37, 5, 8, 60, 18)

# The production runtime owns a sealed resident bounded controller.
APPLICATION_MATRIX_DEVICE_READY = True

# Device contact remains behind the transactional adoption/deployment path and
# the orchestrator's reset-after-every-stage contract.
APPLICATION_DEVICE_OPERATIONS_READY = True


class ScenarioUnavailable(RuntimeError):
    """The application does not expose a safe production capability."""


class ScenarioVerdict:
    """Bounded observable outcome for one application scenario."""

    __slots__ = (
        "action", "status", "rounds_completed", "operations", "restored",
        "reason")

    def __init__(self, action):
        self.action = action
        self.status = 0
        self.rounds_completed = 0
        self.operations = 0
        self.restored = False
        self.reason = None


def _new_verdicts():
    return [
        None,
        ScenarioVerdict(ACTION_CALCULATOR_HISTORY),
        ScenarioVerdict(ACTION_ERROR_LIFECYCLE),
        ScenarioVerdict(ACTION_VARIABLE_QUOTA_RESTART),
        ScenarioVerdict(ACTION_PLOT_PIPELINE),
        ScenarioVerdict(ACTION_PLUGIN_RELOAD),
        ScenarioVerdict(ACTION_STOPWATCH_LAPS),
        ScenarioVerdict(ACTION_PAGE_ROUND_TRIPS),
    ]


def _action_for_capability(capability):
    for index, known_capability in enumerate(APPLICATION_CAPABILITIES):
        if capability == known_capability:
            return index + 1
    raise ValueError("Unknown application capability")


def _operations_for_capability(capability):
    return APPLICATION_OPERATION_COUNTS[
        _action_for_capability(capability) - 1]


class _ApplicationScenarioSession:
    """Immutable capability descriptor bound to an adapter only in ``open``."""

    __slots__ = (
        "capabilities", "step_limits", "no_progress_limits",
        "_bound_session", "completed_capability", "completed_count",
        "completed_operations")

    def __init__(self, capabilities):
        if not isinstance(capabilities, tuple) or not capabilities:
            raise ValueError("Scenario capabilities must be a nonempty tuple")
        for capability in capabilities:
            _action_for_capability(capability)
        self.capabilities = capabilities
        self.step_limits = ()
        self.no_progress_limits = ()
        self._bound_session = None
        self.completed_capability = None
        self.completed_count = 0
        self.completed_operations = 0

    def open(self, runtime):
        if self._bound_session is not None:
            raise RuntimeError("Scenario transaction is already open")
        self.step_limits = ()
        self.no_progress_limits = ()
        try:
            adapter = runtime.scenario_adapter
        except AttributeError:
            adapter = None
        if adapter is None:
            raise ScenarioUnavailable("Runtime bounded scenario adapter unavailable")
        try:
            opener = adapter.open_bounded_session
        except AttributeError:
            raise ScenarioUnavailable(
                "Runtime bounded scenario adapter unavailable")
        if opener is None:
            raise ScenarioUnavailable("Runtime bounded scenario adapter unavailable")
        bound_session = opener(runtime, self.capabilities)
        if bound_session is None:
            raise ScenarioUnavailable("Application bounded session unavailable")
        self._bound_session = bound_session
        self.completed_capability = None
        self.completed_count = 0
        self.completed_operations = 0
        self.step_limits = bound_session.step_limits
        self.no_progress_limits = bound_session.no_progress_limits

    def step(self, round_index, capability_index):
        bound_session = self._bound_session
        if bound_session is None:
            raise RuntimeError("Scenario transaction is not open")
        status = bound_session.step(round_index, capability_index)
        if status == STEP_DONE:
            self.completed_capability = bound_session.completed_capability
            self.completed_count = bound_session.completed_count
            self.completed_operations = bound_session.completed_operations
        return status

    def close(self):
        bound_session = self._bound_session
        if bound_session is None:
            return True
        restored = bound_session.close()
        if restored is True:
            self._bound_session = None
        return restored


class _ControllerApplicationBoundedSession:
    """Adapter-owned proof and verdict accounting around a controller session."""

    __slots__ = (
        "_adapter", "_controller_session", "capabilities",
        "completed_capability", "completed_count", "completed_operations",
        "_step_limits", "_no_progress_limits")

    def __init__(self, adapter, controller_session, capabilities):
        self._adapter = adapter
        self._controller_session = controller_session
        self.capabilities = capabilities
        self.completed_capability = None
        self.completed_count = 0
        self.completed_operations = 0
        self._step_limits = None
        self._no_progress_limits = None

    def _limits(self, name):
        try:
            values = getattr(self._controller_session, name)
        except MemoryError:
            for capability in self.capabilities:
                self._failed(capability, "MemoryError")
            raise
        except Exception:
            for capability in self.capabilities:
                self._failed(capability, "Scenario transaction limits failed")
            raise
        if (not isinstance(values, tuple)
                or len(values) != len(self.capabilities)):
            for capability in self.capabilities:
                self._failed(capability, "Invalid bounded session limits")
            raise ValueError("Invalid bounded session limits")
        if name == "step_limits":
            maximum = MAX_BOUNDED_STEPS
        else:
            maximum = MAX_BOUNDED_NO_PROGRESS_STEPS
        for index, value in enumerate(values):
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value <= 0 or value > maximum):
                self._failed(
                    self.capabilities[index], "Invalid bounded session limits")
                raise ValueError("Invalid bounded session limits")
        return values

    @property
    def step_limits(self):
        values = self._step_limits
        if values is None:
            values = self._limits("step_limits")
            self._step_limits = values
        return values

    @property
    def no_progress_limits(self):
        values = self._no_progress_limits
        if values is None:
            values = self._limits("no_progress_limits")
            self._no_progress_limits = values
        return values

    def _failed(self, capability, reason):
        verdict = self._adapter.verdict(_action_for_capability(capability))
        verdict.status = FAILED
        verdict.reason = reason

    def _restored(self):
        for capability in self.capabilities:
            verdict = self._adapter.verdict(_action_for_capability(capability))
            verdict.restored = True

    def _restore_failed(self):
        for capability in self.capabilities:
            verdict = self._adapter.verdict(_action_for_capability(capability))
            verdict.status = FAILED
            verdict.restored = False
            verdict.reason = "Scenario restore failed"

    def step(self, round_index, capability_index):
        capability = self.capabilities[capability_index]
        controller_session = self._controller_session
        if controller_session is None:
            self._failed(capability, "Scenario transaction is closed")
            raise RuntimeError("Scenario transaction is closed")
        try:
            status = controller_session.step(round_index, capability_index)
        except MemoryError:
            self._failed(capability, "MemoryError")
            raise
        except ScenarioUnavailable:
            self._adapter._mark_unavailable((capability,))
            raise
        except Exception:
            self._failed(capability, "Scenario execution failed")
            raise

        if status == STEP_DONE:
            expected_count = self.completed_count + 1
            operations_before = self.completed_operations
            expected_operations = (
                operations_before
                + _operations_for_capability(capability))
            try:
                completed_capability = controller_session.completed_capability
                completed_count = controller_session.completed_count
                completed_operations = getattr(
                    controller_session, "completed_operations", None)
            except MemoryError:
                self._failed(capability, "MemoryError")
                raise
            except Exception:
                self._failed(capability, "Scenario completion proof failed")
                raise
            invalid_count = (
                isinstance(completed_count, bool)
                or not isinstance(completed_count, int)
                or completed_count != expected_count)
            invalid_operations = (
                isinstance(completed_operations, bool)
                or not isinstance(completed_operations, int)
                or completed_operations != expected_operations)
            if (completed_capability != capability
                    or invalid_count
                    or invalid_operations):
                self.completed_capability = completed_capability
                # The runner also rejects the malformed scalar.  A bool must
                # not compare equal to its integer counterpart and slip past.
                # Operation proofs are not part of the runner protocol, so an
                # invalid one deliberately makes the completion proof invalid.
                self.completed_count = (
                    None if invalid_count or invalid_operations
                    else completed_count)
                self.completed_operations = (
                    None if invalid_operations else completed_operations)
                self._failed(capability, "Invalid bounded completion proof")
            else:
                self.completed_capability = completed_capability
                self.completed_count = completed_count
                self.completed_operations = completed_operations
                verdict = self._adapter.verdict(
                    _action_for_capability(capability))
                verdict.status = PASS
                verdict.rounds_completed += 1
                verdict.operations += completed_operations - operations_before
                verdict.reason = None
        elif status not in (STEP_MORE, STEP_WAIT):
            self._failed(capability, "Invalid bounded step status")
        return status

    def close(self):
        controller_session = self._controller_session
        if controller_session is None:
            return True
        try:
            restored = controller_session.close()
        except MemoryError:
            self._restore_failed()
            raise
        except Exception:
            self._restore_failed()
            raise
        if restored is not True:
            self._restore_failed()
            raise RuntimeError("Scenario restore failed")
        self._controller_session = None
        self._restored()
        return True


class ResidentApplicationScenarioAdapter:
    """Production Adapter for an application-owned scenario controller.

    A production controller must expose this minimal bounded interface:

    - ``supports(capability) -> bool``
    - ``open_bounded_session(runtime, capabilities) -> controller_session``
    - ``controller_session.step(round_index, capability_index) -> STEP_*``
    - ``controller_session.completed_capability``,
      ``controller_session.completed_count``, and strictly cumulative
      ``controller_session.completed_operations`` after ``STEP_DONE``
    - immutable ``controller_session.step_limits`` and
      ``controller_session.no_progress_limits`` tuples, one positive scalar
      per requested capability
    - ``controller_session.close() -> True``

    The controller session owns its transaction snapshot/restore.  This
    adapter copies its completion proof, accounts each capability verdict, and
    turns an unsuccessful close into a restore failure.  The production
    runtime supplies this controller through its sealed construction binding;
    missing or foreign bindings fail closed.
    """

    __slots__ = ("_controller", "_verdicts", "_controller_sealed")

    def __init__(self, controller=None):
        self._controller_sealed = False
        self._controller = controller
        self._verdicts = _new_verdicts()
        self._controller_sealed = True

    def __setattr__(self, name, value):
        if (name == "_controller"
                and getattr(self, "_controller_sealed", False)):
            raise AttributeError("Resident application controller is immutable")
        object.__setattr__(self, name, value)

    def require_resident_application_binding(self, binding):
        """Return the sealed real controller only for its construction binding."""
        from runtime_application_controller import (
            _ResidentApplicationScenarioController)

        controller = self._controller
        if not isinstance(controller, _ResidentApplicationScenarioController):
            raise RuntimeError("Resident application controller is unavailable")
        if controller._require_resident_application_binding(binding) is not binding:
            raise RuntimeError("Resident application controller is foreign")
        return controller

    def verdict(self, action):
        if action <= 0 or action >= len(self._verdicts):
            raise ValueError("Unknown application scenario")
        return self._verdicts[action]

    def _mark_unavailable(self, capabilities):
        for capability in capabilities:
            verdict = self.verdict(_action_for_capability(capability))
            verdict.status = UNAVAILABLE
            verdict.restored = True
            verdict.reason = capability

    def _mark_failed(self, capabilities, reason):
        for capability in capabilities:
            verdict = self.verdict(_action_for_capability(capability))
            verdict.status = FAILED
            verdict.reason = reason

    def open_bounded_session(self, runtime, capabilities):
        """Open one bounded controller transaction."""
        if not isinstance(capabilities, tuple) or not capabilities:
            raise ValueError("Scenario capabilities must be a nonempty tuple")
        for capability in capabilities:
            _action_for_capability(capability)

        controller = self._controller
        if controller is None:
            self._mark_unavailable(capabilities)
            raise ScenarioUnavailable(capabilities[0])
        try:
            supports = controller.supports
        except MemoryError:
            self._mark_failed(capabilities, "MemoryError")
            raise
        except Exception:
            self._mark_failed(capabilities, "Scenario capability check failed")
            raise
        if not callable(supports):
            self._mark_unavailable(capabilities)
            raise ScenarioUnavailable(capabilities[0])
        for capability in capabilities:
            try:
                supported = supports(capability)
            except MemoryError:
                self._mark_failed((capability,), "MemoryError")
                raise
            except Exception:
                self._mark_failed(
                    (capability,), "Scenario capability check failed")
                raise
            if not supported:
                self._mark_unavailable((capability,))
                raise ScenarioUnavailable(capability)

        try:
            opener = controller.open_bounded_session
        except MemoryError:
            self._mark_failed(capabilities, "MemoryError")
            raise
        except AttributeError:
            opener = None
        except Exception:
            self._mark_failed(capabilities, "Scenario transaction open failed")
            raise
        if not callable(opener):
            self._mark_unavailable(capabilities)
            raise ScenarioUnavailable(capabilities[0])
        try:
            controller_session = opener(runtime, capabilities)
        except MemoryError:
            self._mark_failed(capabilities, "MemoryError")
            raise
        except Exception:
            self._mark_failed(
                capabilities, "Scenario transaction open failed")
            raise
        if controller_session is None:
            self._mark_unavailable(capabilities)
            raise ScenarioUnavailable(capabilities[0])
        return _ControllerApplicationBoundedSession(
            self, controller_session, capabilities)

def _bounded_steps(session):
    return tuple(
        (capability, RUN_BOUNDED, session)
        for capability in session.capabilities)


def application_scenarios(rounds=5):
    """Return seven independent one-capability bounded transactions.

    Each report has its own session descriptor and baseline.  The descriptor
    does not resolve the runtime adapter until the acceptance runner opens it.
    These diagnostics help localize failures; they are not the complete matrix
    heap, drift, timing, or buffer baseline.
    """
    return tuple(
        (
            capability,
            rounds,
            _bounded_steps(_ApplicationScenarioSession((capability,))),
        )
        for capability in APPLICATION_CAPABILITIES)


def application_matrix(rounds=5):
    """Return one ordered A-to-G matrix under one bounded transaction.

    All seven entries deliberately share one session identity.  The runner can
    therefore prove one ordered completion at a time without retaining seven
    simultaneous snapshots.  The resident runtime supplies the real bounded
    controller selected by the release gate.
    """
    session = _ApplicationScenarioSession(APPLICATION_CAPABILITIES)
    return ("application_matrix", rounds, _bounded_steps(session))
