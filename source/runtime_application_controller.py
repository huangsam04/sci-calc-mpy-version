"""Real resident application work behind the bounded acceptance seam.

This module owns only scalar controller state and references to transactions
already provided by the resident screens.  It never constructs a second
runtime, registry, framebuffer, screen collection, or navigation lookup.
"""

from runtime_acceptance import STEP_DONE, STEP_MORE
from runtime_scenarios import (
    APPLICATION_CAPABILITIES, APPLICATION_OPERATION_COUNTS,
    ERROR_KIND_COUNT, MAX_CALCULATOR_HISTORY, MAX_CALCULATOR_INPUT,
    STOPWATCH_LAP_COUNT, ScenarioUnavailable)


_CHILD_NONE = 0
_CHILD_CALCULATOR = 1
_CHILD_VARIABLES = 2
_CHILD_PLOT = 3
_CHILD_PLUGIN_CANDIDATE = 4
_CHILD_PLUGIN_VALID = 5
_CHILD_PLUGIN_MISSING = 6
_CHILD_STOPWATCH = 7
_CHILD_PAGES = 8

_PLUGIN_CANDIDATE_OPEN = 0
_PLUGIN_CANDIDATE_STEP = 1
_PLUGIN_CANDIDATE_CLOSE = 2
_PLUGIN_VALID_OPEN = 3
_PLUGIN_VALID_STEP = 4
_PLUGIN_VALID_CANCEL = 5
_PLUGIN_REVERIFY_OPEN = 6
_PLUGIN_REVERIFY_STEP = 7
_PLUGIN_REVERIFY_CLOSE = 8
_PLUGIN_MISSING_OPEN = 9
_PLUGIN_MISSING_STEP = 10
_PLUGIN_MISSING_CLOSE = 11


def _step_limit_for(capability):
    """Return the fixed physical-step ceiling for one semantic capability."""
    if capability == "plot_pipeline" or capability == "plugin_reload":
        return 512
    return 64


def _operation_count_for(capability):
    for index in range(len(APPLICATION_CAPABILITIES)):
        if APPLICATION_CAPABILITIES[index] == capability:
            return APPLICATION_OPERATION_COUNTS[index]
    raise ValueError("Unknown application capability")


class _ResidentApplicationScenarioController:
    """One immutable controller over the already-constructed resident app."""

    __slots__ = ("_binding", "_active_session", "_sealed")

    def __init__(self, binding):
        self._sealed = False
        self._binding = binding
        self._active_session = None
        self._sealed = True

    def __setattr__(self, name, value):
        if (name == "_binding" and getattr(self, "_sealed", False)):
            raise AttributeError("Resident application controller is immutable")
        object.__setattr__(self, name, value)

    def _require_resident_application_binding(self, binding):
        """Return the construction binding only when its identity is exact."""
        if binding is not self._binding:
            raise RuntimeError("Resident application controller binding is foreign")
        return binding

    def supports(self, capability):
        return capability in APPLICATION_CAPABILITIES

    def open_bounded_session(self, runtime, capabilities):
        if type(capabilities) is not tuple or not capabilities:
            raise ValueError("Scenario capabilities must be a nonempty tuple")
        for capability in capabilities:
            if not self.supports(capability):
                raise ValueError("Unknown application capability")
        if self._active_session is not None:
            raise RuntimeError("Bounded scenario transaction is already open")

        # The controller is deliberately incapable of discovering application
        # state through Nav or a screen lookup.  This is its only acquisition.
        binding = runtime.require_application_binding()
        self._require_resident_application_binding(binding)
        if binding.require_page_owner(runtime.root) is not binding:
            raise RuntimeError("Resident page owner is unavailable")

        session = _ResidentApplicationBoundedSession(
            self, binding, capabilities)
        self._active_session = session
        return session

    def _release_session(self, session):
        if self._active_session is not session:
            raise RuntimeError("Bounded scenario transaction is foreign")
        self._active_session = None


class _ResidentApplicationBoundedSession:
    """One scalar state machine over one existing child transaction at a time."""

    __slots__ = (
        "_controller", "_binding", "_capabilities", "step_limits",
        "no_progress_limits", "_limits_sealed", "completed_capability",
        "completed_count", "completed_operations", "_expected_round",
        "_expected_capability", "_child", "_child_kind", "_phase",
        "_index", "_fixture_pack", "_page", "_page_id", "_closed")

    def __init__(self, controller, binding, capabilities):
        self._limits_sealed = False
        self._controller = controller
        self._binding = binding
        self._capabilities = capabilities
        limits = []
        for capability in capabilities:
            limits.append(_step_limit_for(capability))
        self.step_limits = tuple(limits)
        self.no_progress_limits = (1,) * len(capabilities)
        self._limits_sealed = True
        self.completed_capability = None
        self.completed_count = 0
        self.completed_operations = 0
        self._expected_round = 0
        self._expected_capability = 0
        self._child = None
        self._child_kind = _CHILD_NONE
        self._phase = 0
        self._index = 0
        self._fixture_pack = None
        self._page = None
        self._page_id = 0
        self._closed = False

    def __setattr__(self, name, value):
        if (name in ("step_limits", "no_progress_limits")
                and getattr(self, "_limits_sealed", False)):
            raise AttributeError("Bounded scenario limits are immutable")
        object.__setattr__(self, name, value)

    def _require_open(self):
        if self._closed or self._binding is None:
            raise RuntimeError("Bounded scenario transaction is closed")
        return self._binding

    def _page_for(self, page_id):
        page = self._page
        if page is not None:
            if self._page_id == page_id:
                return page
            if self._child is not None:
                raise RuntimeError("A different scenario page is active")
            self._release_page()
        binding = self._require_open()
        binding.require_page_owner()
        nav = binding._nav
        if nav.current is not binding.root:
            raise RuntimeError("Scenario page owner is not at the root")
        page = nav.open(page_id)
        if page is None or nav.current is not page:
            raise RuntimeError("Scenario page did not open")
        self._page = page
        self._page_id = page_id
        return page

    def _release_page(self):
        page = self._page
        if page is None:
            return False
        binding = self._require_open()
        nav = binding._nav
        if nav.current is not page:
            raise RuntimeError("Scenario page is not current")
        returned = nav.back()
        if returned is not binding.root or nav.current is not binding.root:
            raise RuntimeError("Scenario page did not return to the root")
        self._page = None
        self._page_id = 0
        return True

    def _open_child(self, kind, child):
        if self._child is not None:
            raise RuntimeError("Bounded scenario child transaction is already open")
        if child is None:
            raise RuntimeError("Bounded scenario child transaction is unavailable")
        self._child = child
        self._child_kind = kind
        return child

    def _close_child(self, cancel=False):
        child = self._child
        if child is None:
            return True
        if cancel:
            restored = child.cancel()
        else:
            restored = child.close()
        if restored is not True:
            raise RuntimeError("Bounded scenario child restore failed")
        self._child = None
        self._child_kind = _CHILD_NONE
        return True

    def _close_previous(self, preserve_calculator=False):
        kind = self._child_kind
        if kind == _CHILD_NONE:
            return False
        if preserve_calculator and kind == _CHILD_CALCULATOR:
            return False
        self._close_child(cancel=(kind == _CHILD_PLUGIN_VALID))
        return True

    def _complete(self, capability):
        kind = self._child_kind
        if kind != _CHILD_NONE:
            self._close_child(cancel=(kind == _CHILD_PLUGIN_VALID))
        self._release_page()
        self.completed_capability = capability
        self.completed_count += 1
        self.completed_operations += _operation_count_for(capability)
        self._expected_capability += 1
        if self._expected_capability == len(self._capabilities):
            self._expected_capability = 0
            self._expected_round += 1
        self._phase = 0
        self._index = 0
        return STEP_DONE

    @staticmethod
    def _history_expression(index):
        width = 46 if index == MAX_CALCULATOR_HISTORY - 1 else 38
        index_text = str(index)
        expression = "0e+" + ("0" * (width - 3 - len(index_text))) + index_text
        if len(expression) != width or len(expression) > MAX_CALCULATOR_INPUT:
            raise RuntimeError("Calculator history expression is not bounded")
        return expression

    def _step_calculator_history(self, capability):
        if self._close_previous(preserve_calculator=True):
            return STEP_MORE
        binding = self._require_open()
        transaction = self._child
        if transaction is None:
            transaction = self._open_child(
                _CHILD_CALCULATOR,
                self._page_for(1).open_scenario_transaction())
            return STEP_MORE
        if self._child_kind != _CHILD_CALCULATOR:
            raise RuntimeError("Calculator scenario transaction is unavailable")

        from screens.calculator_scenario import (
            CALCULATOR_SCENARIO_HISTORY,
            CALCULATOR_SCENARIO_HISTORY_CURSOR,
            CALCULATOR_SCENARIO_HISTORY_CURSOR_FORWARD,
            CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE)

        index = self._index
        if index < MAX_CALCULATOR_HISTORY:
            transaction.step(
                CALCULATOR_SCENARIO_HISTORY, self._history_expression(index))
        elif index < MAX_CALCULATOR_HISTORY * 2:
            transaction.step(
                CALCULATOR_SCENARIO_HISTORY_CURSOR,
                CALCULATOR_SCENARIO_HISTORY_CURSOR_REVERSE)
        else:
            transaction.step(
                CALCULATOR_SCENARIO_HISTORY_CURSOR,
                CALCULATOR_SCENARIO_HISTORY_CURSOR_FORWARD)
        self._index = index + 1
        if self._index < MAX_CALCULATOR_HISTORY * 3:
            return STEP_MORE
        if (transaction.history_steps != MAX_CALCULATOR_HISTORY
                or transaction.history_reverse_steps != MAX_CALCULATOR_HISTORY
                or transaction.history_forward_steps != MAX_CALCULATOR_HISTORY
                or transaction.history_cursor != MAX_CALCULATOR_HISTORY - 1):
            raise RuntimeError("Calculator history proof failed")
        return self._complete(capability)

    def _step_error_lifecycle(self, capability):
        transaction = self._child
        if transaction is not None:
            if self._child_kind != _CHILD_CALCULATOR:
                self._close_child(
                    cancel=(self._child_kind == _CHILD_PLUGIN_VALID))
                return STEP_MORE
            if transaction.history_steps:
                self._close_child()
                return STEP_MORE
        binding = self._require_open()
        transaction = self._child
        if transaction is None:
            transaction = self._open_child(
                _CHILD_CALCULATOR,
                self._page_for(1).open_scenario_transaction())
            return STEP_MORE
        if self._child_kind != _CHILD_CALCULATOR:
            raise RuntimeError("Calculator scenario transaction is unavailable")

        from screens.calculator_scenario import (
            CALCULATOR_SCENARIO_ERROR_DISMISS,
            CALCULATOR_SCENARIO_ERROR_KIND)

        if self._phase == 0:
            transaction.step(CALCULATOR_SCENARIO_ERROR_KIND, self._index)
            if (transaction.error_kind != self._index
                    or not isinstance(transaction.error_diagnostic_proof, int)):
                raise RuntimeError("Calculator error diagnostic proof failed")
            self._phase = 1
            return STEP_MORE
        if self._phase != 1:
            raise RuntimeError("Calculator error scenario state is invalid")
        transaction.step(CALCULATOR_SCENARIO_ERROR_DISMISS)
        if transaction.error_kind is not None:
            raise RuntimeError("Calculator error dismissal proof failed")
        self._index += 1
        self._phase = 0
        if self._index < ERROR_KIND_COUNT:
            return STEP_MORE
        if transaction.error_kind_mask != (1 << ERROR_KIND_COUNT) - 1:
            raise RuntimeError("Calculator error coverage proof failed")
        return self._complete(capability)

    def _step_variable_quota_restart(self, capability):
        if (self._child is not None
                and self._child_kind != _CHILD_VARIABLES):
            self._close_child(cancel=(self._child_kind == _CHILD_PLUGIN_VALID))
            return STEP_MORE
        binding = self._require_open()
        transaction = self._child
        if transaction is None:
            from calc.scenario_variables import open_variables_scenario_transaction

            transaction = self._open_child(
                _CHILD_VARIABLES,
                open_variables_scenario_transaction(self._page_for(1)))
            return STEP_MORE
        if self._child_kind != _CHILD_VARIABLES:
            raise RuntimeError("Variables scenario transaction is unavailable")

        from calc.scenario_variables import VARIABLES_SCENARIO_OPERATION_COUNT

        transaction.step_canonical_operation(self._index)
        self._index += 1
        if self._index < VARIABLES_SCENARIO_OPERATION_COUNT:
            return STEP_MORE
        if (transaction.canonical_operations_completed
                != VARIABLES_SCENARIO_OPERATION_COUNT
                or not transaction.canonical_complete):
            raise RuntimeError("Variables scenario canonical proof failed")
        return self._complete(capability)

    def _step_plot_pipeline(self, capability):
        if self._child is not None and self._child_kind != _CHILD_PLOT:
            self._close_child(cancel=(self._child_kind == _CHILD_PLUGIN_VALID))
            return STEP_MORE
        binding = self._require_open()
        transaction = self._child
        if transaction is None:
            transaction = self._open_child(
                _CHILD_PLOT, self._page_for(2).open_scenario_transaction())
            return STEP_MORE
        if self._child_kind != _CHILD_PLOT:
            raise RuntimeError("Plot scenario transaction is unavailable")

        from screens.plot_scenario import (
            PLOT_SCENARIO_PROBE_ORDINARY_ERROR,
            PLOT_SCENARIO_PROBE_VALID,
            PLOT_SCENARIO_RESULT_COMPLETE,
            PLOT_SCENARIO_RESULT_ORDINARY_ERROR)

        if self._phase == 0:
            transaction.start_probe(PLOT_SCENARIO_PROBE_VALID)
            self._phase = 1
            return STEP_MORE
        if self._phase == 1:
            transaction.step()
            if not transaction.terminal:
                return STEP_MORE
            if transaction.result != PLOT_SCENARIO_RESULT_COMPLETE:
                raise RuntimeError("Plot valid probe did not render a curve")
            self._phase = 2
            return STEP_MORE
        if self._phase == 2:
            transaction.start_probe(PLOT_SCENARIO_PROBE_ORDINARY_ERROR)
            self._phase = 3
            return STEP_MORE
        if self._phase != 3:
            raise RuntimeError("Plot scenario state is invalid")
        transaction.step()
        if not transaction.terminal:
            return STEP_MORE
        if transaction.result != PLOT_SCENARIO_RESULT_ORDINARY_ERROR:
            raise RuntimeError("Plot ordinary error probe failed")
        return self._complete(capability)

    def _open_fixture_candidate(self, fixture_pack=None):
        from calc.plugin_fixture import PluginScenarioFixtureCandidate

        if fixture_pack is None:
            candidate = PluginScenarioFixtureCandidate()
        else:
            candidate = fixture_pack.open_reverify()
        return self._open_child(_CHILD_PLUGIN_CANDIDATE, candidate)

    def _open_fixture_reload(self, kind, selection):
        from calc.plugin_reload import open_plugin_reload_transaction

        binding = self._require_open()
        fixture_pack = self._fixture_pack
        if fixture_pack is None:
            raise RuntimeError("Managed plugin fixture pack is unavailable")
        transaction = open_plugin_reload_transaction(
            binding.registry,
            self._page_for(3),
            settings=binding.settings,
            func_dir=fixture_pack.directory,
            files=fixture_pack.files,
            selection=selection)
        return self._open_child(kind, transaction)

    def _finish_fixture_candidate(self, reverify):
        from runtime_fixture_pack import bind_verified_candidate

        candidate = self._child
        if self._child_kind != _CHILD_PLUGIN_CANDIDATE:
            raise RuntimeError("Plugin fixture candidate is unavailable")
        if not candidate.available:
            self._close_child()
            raise ScenarioUnavailable("plugin_reload")
        if reverify:
            fixture_pack = self._fixture_pack
            if fixture_pack is None:
                raise RuntimeError("Managed plugin fixture pack is unavailable")
            if not fixture_pack.accepts_reverified_candidate(candidate):
                self._close_child()
                raise RuntimeError("Plugin fixture reverify identity changed")
        else:
            fixture_pack = bind_verified_candidate(candidate)
        self._close_child()
        self._fixture_pack = fixture_pack
        return STEP_MORE

    def _step_plugin_reload(self, capability):
        phase = self._phase
        child = self._child

        if phase == _PLUGIN_CANDIDATE_OPEN:
            if self._child is not None:
                self._close_child(
                    cancel=(self._child_kind == _CHILD_PLUGIN_VALID))
                return STEP_MORE
            self._open_fixture_candidate()
            self._phase = _PLUGIN_CANDIDATE_STEP
            return STEP_MORE
        if phase == _PLUGIN_CANDIDATE_STEP:
            if self._child_kind != _CHILD_PLUGIN_CANDIDATE:
                raise RuntimeError("Plugin fixture candidate is unavailable")
            if child.step():
                self._phase = _PLUGIN_CANDIDATE_CLOSE
            return STEP_MORE
        if phase == _PLUGIN_CANDIDATE_CLOSE:
            self._finish_fixture_candidate(False)
            self._phase = _PLUGIN_VALID_OPEN
            return STEP_MORE
        if phase == _PLUGIN_VALID_OPEN:
            fixture_pack = self._fixture_pack
            if fixture_pack is None:
                raise RuntimeError("Managed plugin fixture pack is unavailable")
            self._open_fixture_reload(
                _CHILD_PLUGIN_VALID, fixture_pack.valid_selection)
            self._phase = _PLUGIN_VALID_STEP
            return STEP_MORE
        if phase == _PLUGIN_VALID_STEP:
            if self._child_kind != _CHILD_PLUGIN_VALID:
                raise RuntimeError("Plugin valid reload is unavailable")
            if child.step():
                fixture_pack = self._fixture_pack
                if (fixture_pack is None
                        or not fixture_pack.valid_reload_result(child)):
                    raise RuntimeError("Plugin valid dependency proof failed")
                self._phase = _PLUGIN_VALID_CANCEL
            return STEP_MORE
        if phase == _PLUGIN_VALID_CANCEL:
            if self._child_kind != _CHILD_PLUGIN_VALID:
                raise RuntimeError("Plugin valid reload is unavailable")
            self._close_child(cancel=True)
            self._phase = _PLUGIN_REVERIFY_OPEN
            return STEP_MORE
        if phase == _PLUGIN_REVERIFY_OPEN:
            fixture_pack = self._fixture_pack
            if fixture_pack is None:
                raise RuntimeError("Managed plugin fixture pack is unavailable")
            self._open_fixture_candidate(fixture_pack)
            self._phase = _PLUGIN_REVERIFY_STEP
            return STEP_MORE
        if phase == _PLUGIN_REVERIFY_STEP:
            if self._child_kind != _CHILD_PLUGIN_CANDIDATE:
                raise RuntimeError("Plugin fixture candidate is unavailable")
            if child.step():
                self._phase = _PLUGIN_REVERIFY_CLOSE
            return STEP_MORE
        if phase == _PLUGIN_REVERIFY_CLOSE:
            self._finish_fixture_candidate(True)
            self._phase = _PLUGIN_MISSING_OPEN
            return STEP_MORE
        if phase == _PLUGIN_MISSING_OPEN:
            fixture_pack = self._fixture_pack
            if fixture_pack is None:
                raise RuntimeError("Managed plugin fixture pack is unavailable")
            self._open_fixture_reload(
                _CHILD_PLUGIN_MISSING, fixture_pack.missing_selection)
            self._phase = _PLUGIN_MISSING_STEP
            return STEP_MORE
        if phase == _PLUGIN_MISSING_STEP:
            if self._child_kind != _CHILD_PLUGIN_MISSING:
                raise RuntimeError("Plugin missing reload is unavailable")
            if child.step():
                fixture_pack = self._fixture_pack
                if (fixture_pack is None
                        or not fixture_pack.missing_reload_result(child)):
                    raise RuntimeError("Plugin missing dependency proof failed")
                self._phase = _PLUGIN_MISSING_CLOSE
            return STEP_MORE
        if phase != _PLUGIN_MISSING_CLOSE:
            raise RuntimeError("Plugin scenario state is invalid")
        if self._child_kind != _CHILD_PLUGIN_MISSING:
            raise RuntimeError("Plugin missing reload is unavailable")
        self._close_child()
        self._fixture_pack = None
        return self._complete(capability)

    def _step_stopwatch_laps(self, capability):
        if (self._child is not None
                and self._child_kind != _CHILD_STOPWATCH):
            self._close_child(cancel=(self._child_kind == _CHILD_PLUGIN_VALID))
            return STEP_MORE
        binding = self._require_open()
        lease = self._child
        if lease is None:
            lease = self._open_child(
                _CHILD_STOPWATCH,
                self._page_for(4).open_scenario_lease())
            return STEP_MORE
        if self._child_kind != _CHILD_STOPWATCH:
            raise RuntimeError("Stopwatch scenario lease is unavailable")

        if self._phase == 0:
            if lease.start() is not True:
                raise RuntimeError("Stopwatch scenario did not start")
            self._phase = 1
            return STEP_MORE
        if self._phase == 1:
            if lease.lap() is not True:
                raise RuntimeError("Stopwatch scenario lap failed")
            self._index += 1
            if self._index == STOPWATCH_LAP_COUNT:
                self._index = 0
                self._phase = 2
            return STEP_MORE
        if self._phase == 2:
            if lease.move_lap_cursor(1) is not True:
                raise RuntimeError("Stopwatch older-lap traversal failed")
            self._index += 1
            if self._index == STOPWATCH_LAP_COUNT - 1:
                self._index = 0
                self._phase = 3
            return STEP_MORE
        if self._phase == 3:
            if lease.move_lap_cursor(-1) is not True:
                raise RuntimeError("Stopwatch newer-lap traversal failed")
            self._index += 1
            if self._index == STOPWATCH_LAP_COUNT - 1:
                self._phase = 4
            return STEP_MORE
        if self._phase != 4:
            raise RuntimeError("Stopwatch scenario state is invalid")
        if lease.verify_and_leave_lap_window() is not True:
            raise RuntimeError("Stopwatch terminal window proof failed")
        if not lease.lap_window_verified or lease.lap_window_active:
            raise RuntimeError("Stopwatch terminal window proof failed")
        return self._complete(capability)

    def _step_page_round_trips(self, capability):
        if self._child is not None and self._child_kind != _CHILD_PAGES:
            self._close_child(cancel=(self._child_kind == _CHILD_PLUGIN_VALID))
            return STEP_MORE
        binding = self._require_open()
        transaction = self._child
        if transaction is None:
            self._release_page()
            from nav_scenario import PageLifecycleScenario

            transaction = self._open_child(
                _CHILD_PAGES,
                PageLifecycleScenario(binding._nav, binding.root))
            return STEP_MORE
        if self._child_kind != _CHILD_PAGES:
            raise RuntimeError("Page scenario transaction is unavailable")
        if transaction.step(self._index + 1) is not True:
            return STEP_MORE
        self._index += 1
        if self._index != 9:
            return STEP_MORE
        return self._complete(capability)

    def step(self, round_index, capability_index):
        self._require_open()
        if (isinstance(round_index, bool) or not isinstance(round_index, int)
                or isinstance(capability_index, bool)
                or not isinstance(capability_index, int)):
            raise ValueError("Bounded scenario order is invalid")
        if (round_index != self._expected_round
                or capability_index != self._expected_capability):
            raise RuntimeError("Bounded scenario order changed")
        capability = self._capabilities[capability_index]
        if capability == "calculator_history":
            return self._step_calculator_history(capability)
        if capability == "error_lifecycle":
            return self._step_error_lifecycle(capability)
        if capability == "variable_quota_restart":
            return self._step_variable_quota_restart(capability)
        if capability == "plot_pipeline":
            return self._step_plot_pipeline(capability)
        if capability == "plugin_reload":
            return self._step_plugin_reload(capability)
        if capability == "stopwatch_laps":
            return self._step_stopwatch_laps(capability)
        if capability == "page_round_trips":
            return self._step_page_round_trips(capability)
        raise ValueError("Unknown application capability")

    def close(self):
        """Restore the sole child lease with its public zero-argument API."""
        if self._closed:
            return True
        if self._child is not None:
            self._close_child(cancel=(self._child_kind == _CHILD_PLUGIN_VALID))
        self._release_page()
        self._fixture_pack = None
        controller = self._controller
        if controller is None:
            raise RuntimeError("Bounded scenario controller is unavailable")
        controller._release_session(self)
        self._controller = None
        self._binding = None
        self._capabilities = None
        self._closed = True
        return True
