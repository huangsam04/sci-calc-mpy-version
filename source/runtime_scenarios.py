"""Seven application capabilities behind one acceptance Adapter seam.

Device tools only select a scenario and consume its verdict.  They never need
screen fields, key coordinates, storage paths, or plugin internals.

The diagnostic scenarios deliberately have independent heap/drift/buffer
baselines.  Only :func:`application_matrix` represents one complete A-to-G
matrix under a single runner baseline.  Each capability is still one aggregate
``RUN_ACTION``, so transient allocation peaks inside an action are invisible.
The resident device gate must remain unavailable until those actions become a
bounded multi-step state machine.
"""

from runtime_acceptance import RUN_ACTION


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

# Hard release gate: aggregate RUN_ACTION capabilities are not device-safe.
APPLICATION_MATRIX_DEVICE_READY = False


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


class ResidentApplicationScenarioAdapter:
    """Production Adapter for an application-owned scenario controller.

    RuntimeKernel/main can later supply a controller with this minimal
    interface:

    - ``supports(capability) -> bool``
    - ``snapshot(capability) -> opaque snapshot``
    - ``perform(runtime, capability, round_index) -> operation count``
    - ``restore(capability, snapshot) -> True``

    ``snapshot`` must not mutate application state; ``restore`` must be safe in
    a ``finally`` path.  The current application has no such controller.
    Without it, every action returns an explicit ``UNAVAILABLE`` verdict and
    raises, preventing a false device pass.
    """

    __slots__ = ("_controller", "_verdicts")

    def __init__(self, controller=None):
        self._controller = controller
        self._verdicts = _new_verdicts()

    def verdict(self, action):
        if action <= 0 or action >= len(self._verdicts):
            raise ValueError("Unknown application scenario")
        return self._verdicts[action]

    def perform(self, runtime, action, round_index):
        verdict = self.verdict(action)
        capability = APPLICATION_CAPABILITIES[action - 1]
        controller = self._controller
        if controller is None or not controller.supports(capability):
            verdict.status = UNAVAILABLE
            verdict.restored = True
            verdict.reason = capability
            raise ScenarioUnavailable(capability)

        try:
            snapshot = controller.snapshot(capability)
        except MemoryError:
            verdict.status = FAILED
            verdict.reason = "MemoryError"
            raise
        except Exception:
            verdict.status = FAILED
            verdict.reason = "Scenario snapshot failed"
            raise

        operations = 0
        primary_error = None
        primary_is_memory = False
        try:
            operations = controller.perform(
                runtime, capability, round_index)
        except MemoryError as error:
            primary_error = error
            primary_is_memory = True
            verdict.status = FAILED
            verdict.reason = "MemoryError"
        except Exception as error:
            primary_error = error
            verdict.status = FAILED
            verdict.reason = "Scenario execution failed"

        restore_error = None
        restore_is_memory = False
        try:
            verdict.restored = (
                controller.restore(capability, snapshot) is True)
        except MemoryError as error:
            restore_error = error
            restore_is_memory = True
            verdict.restored = False
        except Exception as error:
            restore_error = error
            verdict.restored = False
        if not verdict.restored:
            verdict.status = FAILED
            verdict.reason = "Scenario restore failed"
            if primary_is_memory:
                raise primary_error
            if restore_is_memory:
                raise restore_error
            if primary_error is not None:
                raise primary_error
            if restore_error is not None:
                raise restore_error
            raise RuntimeError(verdict.reason)
        if primary_error is not None:
            raise primary_error
        if operations is None:
            operations = 0
        if not isinstance(operations, int) or operations < 0:
            verdict.status = FAILED
            verdict.reason = "Invalid scenario operation count"
            raise RuntimeError(verdict.reason)
        verdict.status = PASS
        verdict.rounds_completed += 1
        verdict.operations += operations
        verdict.reason = None


def _calculator_history(runtime, round_index):
    runtime.perform(ACTION_CALCULATOR_HISTORY, round_index)


def _error_lifecycle(runtime, round_index):
    runtime.perform(ACTION_ERROR_LIFECYCLE, round_index)


def _variable_quota_restart(runtime, round_index):
    runtime.perform(ACTION_VARIABLE_QUOTA_RESTART, round_index)


def _plot_pipeline(runtime, round_index):
    runtime.perform(ACTION_PLOT_PIPELINE, round_index)


def _plugin_reload(runtime, round_index):
    runtime.perform(ACTION_PLUGIN_RELOAD, round_index)


def _stopwatch_laps(runtime, round_index):
    runtime.perform(ACTION_STOPWATCH_LAPS, round_index)


def _page_round_trips(runtime, round_index):
    runtime.perform(ACTION_PAGE_ROUND_TRIPS, round_index)


_CALCULATOR_HISTORY_STEPS = (
    ("history_fill_and_traverse", RUN_ACTION, _calculator_history),
)
_ERROR_LIFECYCLE_STEPS = (
    ("twenty_errors_show_and_close", RUN_ACTION, _error_lifecycle),
)
_VARIABLE_QUOTA_RESTART_STEPS = (
    ("quota_save_restart_delete_refill", RUN_ACTION,
     _variable_quota_restart),
)
_PLOT_PIPELINE_STEPS = (
    ("reserve_compile_autoscale_draw_exit", RUN_ACTION, _plot_pipeline),
)
_PLUGIN_RELOAD_STEPS = (
    ("enable_disable_rescan_reload_failure", RUN_ACTION, _plugin_reload),
)
_STOPWATCH_LAPS_STEPS = (
    ("run_twenty_laps_scroll_and_return", RUN_ACTION, _stopwatch_laps),
)
_PAGE_ROUND_TRIPS_STEPS = (
    ("all_main_and_auxiliary_pages", RUN_ACTION, _page_round_trips),
)

_APPLICATION_MATRIX_STEPS = (
    _CALCULATOR_HISTORY_STEPS[0],
    _ERROR_LIFECYCLE_STEPS[0],
    _VARIABLE_QUOTA_RESTART_STEPS[0],
    _PLOT_PIPELINE_STEPS[0],
    _PLUGIN_RELOAD_STEPS[0],
    _STOPWATCH_LAPS_STEPS[0],
    _PAGE_ROUND_TRIPS_STEPS[0],
)


def application_scenarios(rounds=5):
    """Return seven diagnostic scenarios with independent runner baselines.

    These reports help localize failures.  They do not constitute one complete
    application-matrix heap, drift, timing, or buffer baseline.
    """
    return (
        (APPLICATION_CAPABILITIES[0], rounds, _CALCULATOR_HISTORY_STEPS),
        (APPLICATION_CAPABILITIES[1], rounds, _ERROR_LIFECYCLE_STEPS),
        (APPLICATION_CAPABILITIES[2], rounds, _VARIABLE_QUOTA_RESTART_STEPS),
        (APPLICATION_CAPABILITIES[3], rounds, _PLOT_PIPELINE_STEPS),
        (APPLICATION_CAPABILITIES[4], rounds, _PLUGIN_RELOAD_STEPS),
        (APPLICATION_CAPABILITIES[5], rounds, _STOPWATCH_LAPS_STEPS),
        (APPLICATION_CAPABILITIES[6], rounds, _PAGE_ROUND_TRIPS_STEPS),
    )


def application_matrix(rounds=5):
    """Return one ordered A-to-G matrix with one aggregate runner baseline.

    This is host-verifiable only while ``APPLICATION_MATRIX_DEVICE_READY`` is
    false: each capability remains one ``RUN_ACTION`` and its transient
    heap/buffer peak is not observable between runner steps.
    """
    return ("application_matrix", rounds, _APPLICATION_MATRIX_STEPS)
