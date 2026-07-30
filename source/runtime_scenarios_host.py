"""CPython-only state controller for the application acceptance scenarios."""

import sys

if getattr(sys.implementation, "name", "") == "micropython":
    raise ImportError("runtime_scenarios_host is CPython-only")

from runtime_scenarios import (
    APPLICATION_CAPABILITIES,
    APPLICATION_OPERATION_COUNTS,
    APPLICATION_PAGE_IDS,
    ERROR_KIND_COUNT,
    MAX_CALCULATOR_HISTORY,
    MAX_CALCULATOR_INPUT,
    STOPWATCH_LAP_COUNT,
    ResidentApplicationScenarioAdapter,
)
from runtime_acceptance import STEP_DONE, STEP_MORE


_VARIABLE_QUOTA = 32
_PLOT_WORKSPACE_BYTES = 104
_BOUNDED_STEP_LIMITS = (61, 40, 101, 326, 17, 61, 19)
_BOUNDED_NO_PROGRESS_LIMITS = (1, 1, 1, 1, 1, 1, 1)
_PLUGIN_CORE = 1
_PLUGIN_HELPER = 2
_PLUGIN_DEPENDENT = 4
_PLUGIN_BROKEN = 8
_PLUGIN_VALID_MASK = _PLUGIN_CORE | _PLUGIN_HELPER | _PLUGIN_DEPENDENT
_PLUGIN_ALL_MASK = _PLUGIN_VALID_MASK | _PLUGIN_BROKEN
_PLUGIN_COMMITTED_LIVE = ("core", "dependent", "helper")
_PLUGIN_CANDIDATES = (
    ("core", _PLUGIN_CORE, ()),
    ("dependent", _PLUGIN_DEPENDENT, ("helper",)),
    ("helper", _PLUGIN_HELPER, ()),
    ("broken", _PLUGIN_BROKEN, ("missing",)),
)


class _InMemoryScenarioController:
    __slots__ = (
        "history", "history_cursor", "error_code", "error_visible",
        "variables", "durable_variables", "plot_workspace",
        "plot_program", "plot_range", "plot_page", "plugins_enabled",
        "plugin_catalog", "plugin_live", "plugin_revision",
        "plugin_rescans", "stopwatch_running", "stopwatch_elapsed",
        "stopwatch_laps", "stopwatch_cursor", "stopwatch_next_lap",
        "stopwatch_page", "page_stack", "_active_bounded_session")

    def __init__(self):
        self.history = [("seed", "0")]
        self.history_cursor = 0
        self.error_code = None
        self.error_visible = False
        self.variables = {"seed": 7}
        self.durable_variables = {"seed": 7}
        self.plot_workspace = None
        self.plot_program = None
        self.plot_range = None
        self.plot_page = False
        self.plugins_enabled = {"core"}
        self.plugin_catalog = {"core": ()}
        self.plugin_live = ("core",)
        self.plugin_revision = 1
        self.plugin_rescans = 0
        self.stopwatch_running = False
        self.stopwatch_elapsed = 42
        self.stopwatch_laps = [(99, 42)]
        self.stopwatch_cursor = 0
        self.stopwatch_next_lap = 100
        self.stopwatch_page = False
        self.page_stack = ["root"]
        self._active_bounded_session = None

    def supports(self, capability):
        return capability in APPLICATION_CAPABILITIES

    def open_bounded_session(self, _runtime, capabilities):
        if self._active_bounded_session is not None:
            raise RuntimeError("Bounded scenario transaction is already open")
        if not isinstance(capabilities, tuple) or not capabilities:
            raise ValueError("Scenario capabilities must be a nonempty tuple")
        for capability in capabilities:
            if not self.supports(capability):
                raise ValueError("Unknown application capability")
        snapshot = self.snapshot(capabilities[0])
        session = _InMemoryBoundedSession(self, capabilities, snapshot)
        self._active_bounded_session = session
        return session

    def snapshot(self, capability):
        if self._active_bounded_session is not None:
            raise RuntimeError("Bounded scenario transaction is already open")
        return (
            list(self.history),
            self.history_cursor,
            self.error_code,
            self.error_visible,
            dict(self.variables),
            dict(self.durable_variables),
            self.plot_workspace,
            self.plot_program,
            self.plot_range,
            self.plot_page,
            set(self.plugins_enabled),
            dict(self.plugin_catalog),
            self.plugin_live,
            self.plugin_revision,
            self.plugin_rescans,
            self.stopwatch_running,
            self.stopwatch_elapsed,
            list(self.stopwatch_laps),
            self.stopwatch_cursor,
            self.stopwatch_next_lap,
            self.stopwatch_page,
            list(self.page_stack),
        )

    def _restore_snapshot(self, snapshot):
        self.history.clear()
        self.history.extend(snapshot[0])
        self.history_cursor = snapshot[1]
        self.error_code = snapshot[2]
        self.error_visible = snapshot[3]
        self.variables.clear()
        self.variables.update(snapshot[4])
        self.durable_variables.clear()
        self.durable_variables.update(snapshot[5])
        self.plot_workspace = snapshot[6]
        self.plot_program = snapshot[7]
        self.plot_range = snapshot[8]
        self.plot_page = snapshot[9]
        self.plugins_enabled.clear()
        self.plugins_enabled.update(snapshot[10])
        self.plugin_catalog.clear()
        self.plugin_catalog.update(snapshot[11])
        self.plugin_live = snapshot[12]
        self.plugin_revision = snapshot[13]
        self.plugin_rescans = snapshot[14]
        self.stopwatch_running = snapshot[15]
        self.stopwatch_elapsed = snapshot[16]
        self.stopwatch_laps.clear()
        self.stopwatch_laps.extend(snapshot[17])
        self.stopwatch_cursor = snapshot[18]
        self.stopwatch_next_lap = snapshot[19]
        self.stopwatch_page = snapshot[20]
        self.page_stack.clear()
        self.page_stack.extend(snapshot[21])
        return True

    def _set_variable(self, name, value):
        if name not in self.variables and len(self.variables) >= _VARIABLE_QUOTA:
            return False
        self.variables[name] = value
        return True

    @staticmethod
    def _plot_value(x_value):
        if x_value < 0:
            raise ValueError("domain")
        return x_value * x_value

class _InMemoryBoundedSession:
    """One snapshot transaction that advances exactly one host primitive."""

    __slots__ = (
        "_controller", "_snapshot", "_capabilities", "_step_limits",
        "_no_progress_limits", "completed_capability", "completed_count",
        "completed_operations",
        "_expected_round", "_expected_capability", "_phase", "_index",
        "_low", "_high", "_domain_errors", "_drawn", "_saved_live",
        "_saved_revision", "_plugin_candidate_mask", "_plugin_stage_mask",
        "_closed", "_restore_pending")

    def __init__(self, controller, capabilities, snapshot):
        self._controller = controller
        self._snapshot = snapshot
        self._capabilities = capabilities
        self._step_limits = tuple(
            _BOUNDED_STEP_LIMITS[APPLICATION_CAPABILITIES.index(capability)]
            for capability in capabilities)
        self._no_progress_limits = tuple(
            _BOUNDED_NO_PROGRESS_LIMITS[
                APPLICATION_CAPABILITIES.index(capability)]
            for capability in capabilities)
        self.completed_capability = None
        self.completed_count = 0
        self.completed_operations = 0
        self._expected_round = 0
        self._expected_capability = 0
        self._phase = 0
        self._index = 0
        self._low = None
        self._high = None
        self._domain_errors = 0
        self._drawn = 0
        self._saved_live = None
        self._saved_revision = 0
        self._plugin_candidate_mask = 0
        self._plugin_stage_mask = 0
        self._closed = False
        self._restore_pending = False

    @property
    def capabilities(self):
        return self._capabilities

    @property
    def step_limits(self):
        return self._step_limits

    @property
    def no_progress_limits(self):
        return self._no_progress_limits

    def _complete(self, capability):
        self.completed_capability = capability
        self.completed_count += 1
        self.completed_operations += APPLICATION_OPERATION_COUNTS[
            APPLICATION_CAPABILITIES.index(capability)]
        self._expected_capability += 1
        if self._expected_capability == len(self.capabilities):
            self._expected_capability = 0
            self._expected_round += 1
        self._phase = 0
        self._index = 0
        self._low = None
        self._high = None
        self._domain_errors = 0
        self._drawn = 0
        self._saved_live = None
        self._saved_revision = 0
        self._plugin_candidate_mask = 0
        self._plugin_stage_mask = 0

    def step(self, round_index, capability_index):
        if self._closed or self._restore_pending:
            raise RuntimeError("Bounded scenario transaction is closed")
        if (round_index != self._expected_round
                or capability_index != self._expected_capability):
            raise RuntimeError("Bounded scenario order changed")
        capability = self.capabilities[capability_index]
        if capability == APPLICATION_CAPABILITIES[0]:
            completed = self._step_calculator_history()
        elif capability == APPLICATION_CAPABILITIES[1]:
            completed = self._step_error_lifecycle()
        elif capability == APPLICATION_CAPABILITIES[2]:
            completed = self._step_variable_quota_restart()
        elif capability == APPLICATION_CAPABILITIES[3]:
            completed = self._step_plot_pipeline()
        elif capability == APPLICATION_CAPABILITIES[4]:
            completed = self._step_plugin_reload()
        elif capability == APPLICATION_CAPABILITIES[5]:
            completed = self._step_stopwatch_laps()
        elif capability == APPLICATION_CAPABILITIES[6]:
            completed = self._step_page_round_trips()
        else:
            raise ValueError("Unknown application capability")
        if completed:
            self._complete(capability)
            return STEP_DONE
        return STEP_MORE

    def close(self):
        if self._closed:
            return True
        self._restore_pending = True
        restored = self._controller._restore_snapshot(self._snapshot)
        if restored is True:
            self._snapshot = None
            self._closed = True
            self._restore_pending = False
            if self._controller._active_bounded_session is self:
                self._controller._active_bounded_session = None
        return restored

    def _step_calculator_history(self):
        if self._phase == 0:
            self._controller.history.clear()
            self._phase = 1
            self._index = 0
            return False
        if self._phase == 1:
            prefix = str(self._index) + ":"
            expression = prefix + (
                "9" * (MAX_CALCULATOR_INPUT - len(prefix)))
            if len(expression) != MAX_CALCULATOR_INPUT:
                raise RuntimeError("History expression is not maximum size")
            self._controller.history.append((expression, expression))
            self._index += 1
            if self._index == MAX_CALCULATOR_HISTORY:
                self._phase = 2
                self._index = MAX_CALCULATOR_HISTORY - 1
            return False
        if self._phase == 2:
            self._controller.history_cursor = self._index
            if len(self._controller.history[self._index][0]) != MAX_CALCULATOR_INPUT:
                raise RuntimeError("History traversal lost an entry")
            self._index -= 1
            if self._index < 0:
                self._phase = 3
                self._index = 0
            return False
        self._controller.history_cursor = self._index
        if len(self._controller.history[self._index][0]) != MAX_CALCULATOR_INPUT:
            raise RuntimeError("History traversal lost an entry")
        self._index += 1
        return self._index == MAX_CALCULATOR_HISTORY

    def _step_error_lifecycle(self):
        if self._phase == 0:
            self._controller.error_code = self._index
            self._controller.error_visible = True
            self._phase = 1
            return False
        self._controller.error_code = None
        self._controller.error_visible = False
        self._index += 1
        if self._index == ERROR_KIND_COUNT:
            return True
        self._phase = 0
        return False

    def _step_variable_quota_restart(self):
        if self._phase == 0:
            self._controller.variables.clear()
            self._controller.durable_variables.clear()
            self._phase = 1
            self._index = 0
            return False
        if self._phase == 1:
            name = "v" + str(self._index)
            if not self._controller._set_variable(name, self._index):
                raise RuntimeError("Variable quota rejected an in-range item")
            self._index += 1
            if self._index == _VARIABLE_QUOTA:
                self._phase = 2
            return False
        if self._phase == 2:
            if self._controller._set_variable("overflow", _VARIABLE_QUOTA):
                raise RuntimeError("Variable quota accepted an extra item")
            self._phase = 3
            self._index = 0
            return False
        if self._phase == 3:
            name = "v" + str(self._index)
            self._controller.durable_variables[name] = (
                self._controller.variables[name])
            self._index += 1
            if self._index == _VARIABLE_QUOTA:
                self._phase = 4
            return False
        if self._phase == 4:
            self._controller.variables.clear()
            self._phase = 5
            self._index = 0
            return False
        if self._phase == 5:
            name = "v" + str(self._index)
            self._controller.variables[name] = (
                self._controller.durable_variables[name])
            self._index += 1
            if self._index == _VARIABLE_QUOTA:
                self._phase = 6
            return False
        if self._phase == 6:
            del self._controller.variables["v0"]
            self._phase = 7
            return False
        if not self._controller._set_variable("replacement", 100):
            raise RuntimeError("Deleted variable capacity was not reusable")
        if (len(self._controller.variables) != _VARIABLE_QUOTA
                or self._controller.variables.get("replacement") != 100):
            raise RuntimeError("Replacement variable was not retained")
        return True

    def _step_plot_pipeline(self):
        if self._phase == 0:
            self._controller.plot_page = True
            self._controller.plot_workspace = bytearray(_PLOT_WORKSPACE_BYTES)
            self._phase = 1
            return False
        if self._phase == 1:
            self._controller.plot_program = "sqrt_domain_probe"
            self._phase = 2
            self._index = 0
            self._low = None
            self._high = None
            self._domain_errors = 0
            return False
        if self._phase == 2:
            x_value = self._index - 32
            try:
                value = self._controller._plot_value(x_value)
            except ValueError:
                self._domain_errors += 1
            else:
                self._low = (
                    value if self._low is None or value < self._low
                    else self._low)
                self._high = (
                    value if self._high is None or value > self._high
                    else self._high)
            self._index += 1
            if self._index == 65:
                self._phase = 3
            return False
        if self._phase == 3:
            if (self._low is None or self._high is None
                    or self._domain_errors == 0):
                raise RuntimeError("Plot autoscale missed its domain error")
            self._controller.plot_range = (self._low, self._high)
            self._phase = 4
            self._index = 0
            self._drawn = 0
            return False
        if self._phase == 4:
            column = self._index
            try:
                self._controller._plot_value(column - 128)
            except ValueError:
                self._domain_errors += 1
            else:
                offset = (column * 5) % len(self._controller.plot_workspace)
                self._controller.plot_workspace[offset] |= 1 << (column & 7)
                self._drawn += 1
            self._index += 1
            if self._index == 256:
                self._phase = 5
            return False
        if self._phase == 5:
            if self._drawn == 0:
                raise RuntimeError("Plot draw produced no curve")
            self._phase = 6
            return False
        self._controller.plot_workspace = None
        self._controller.plot_program = None
        self._controller.plot_range = None
        self._controller.plot_page = False
        return True

    def _step_plugin_reload(self):
        if self._phase == 0:
            self._controller.plugins_enabled.clear()
        elif self._phase == 1:
            self._controller.plugins_enabled.add("core")
        elif self._phase == 2:
            self._controller.plugins_enabled.add("helper")
        elif self._phase == 3:
            self._controller.plugins_enabled.add("dependent")
        elif self._phase == 4:
            self._controller.plugins_enabled.remove("dependent")
        elif self._phase == 5:
            self._controller.plugin_catalog.clear()
        elif self._phase == 6:
            self._controller.plugin_catalog["core"] = ()
        elif self._phase == 7:
            self._controller.plugin_catalog["helper"] = ()
        elif self._phase == 8:
            self._controller.plugin_catalog["dependent"] = ("helper",)
        elif self._phase == 9:
            self._controller.plugin_catalog["broken"] = ("missing",)
        elif self._phase == 10:
            self._controller.plugin_rescans += 1
        elif self._phase == 11:
            self._controller.plugins_enabled.add("dependent")
        elif self._phase == 12:
            if (not self._stage_plugin_candidate()
                    or self._plugin_candidate_mask != _PLUGIN_VALID_MASK
                    or self._plugin_stage_mask != _PLUGIN_VALID_MASK):
                raise RuntimeError("Valid plugin dependency chain did not load")
            self._controller.plugin_live = _PLUGIN_COMMITTED_LIVE
            self._controller.plugin_revision += 1
        elif self._phase == 13:
            self._saved_live = self._controller.plugin_live
            self._saved_revision = self._controller.plugin_revision
        elif self._phase == 14:
            self._controller.plugins_enabled.add("broken")
        elif self._phase == 15:
            if self._stage_plugin_candidate():
                raise RuntimeError("Broken plugin dependency unexpectedly loaded")
            if (self._plugin_candidate_mask != _PLUGIN_ALL_MASK
                    or self._plugin_stage_mask != _PLUGIN_VALID_MASK):
                raise RuntimeError("Broken plugin candidate did not reject")
        else:
            if (self._controller.plugin_live != self._saved_live
                    or self._controller.plugin_revision != self._saved_revision):
                raise RuntimeError("Failed plugin reload changed live registry")
            return True
        self._phase += 1
        return False

    def _stage_plugin_candidate(self):
        """Validate the fixed candidate set without building a staging list."""
        enabled = self._controller.plugins_enabled
        catalog = self._controller.plugin_catalog
        candidate_mask = 0
        stage_mask = 0
        candidate_count = 0
        for name, bit, dependencies in _PLUGIN_CANDIDATES:
            if name not in enabled:
                continue
            candidate_count += 1
            candidate_mask |= bit
            if catalog.get(name) != dependencies:
                self._plugin_candidate_mask = candidate_mask
                self._plugin_stage_mask = stage_mask
                return False
            for dependency in dependencies:
                if dependency not in enabled:
                    self._plugin_candidate_mask = candidate_mask
                    self._plugin_stage_mask = stage_mask
                    return False
            stage_mask |= bit
        self._plugin_candidate_mask = candidate_mask
        self._plugin_stage_mask = stage_mask
        return candidate_count == len(enabled)

    def _step_stopwatch_laps(self):
        if self._phase == 0:
            self._controller.stopwatch_laps.clear()
            self._controller.stopwatch_elapsed = 0
            self._controller.stopwatch_cursor = 0
            self._controller.stopwatch_next_lap = 1
            self._controller.stopwatch_page = True
            self._controller.stopwatch_running = True
            self._phase = 1
            self._index = 0
            return False
        if self._phase == 1:
            self._controller.stopwatch_elapsed += 137
            self._controller.stopwatch_laps.insert(
                0,
                (self._controller.stopwatch_next_lap,
                 self._controller.stopwatch_elapsed),
            )
            self._controller.stopwatch_next_lap += 1
            self._index += 1
            if self._index == STOPWATCH_LAP_COUNT:
                self._phase = 2
            return False
        if self._phase == 2:
            if (len(self._controller.stopwatch_laps) != STOPWATCH_LAP_COUNT
                    or self._controller.stopwatch_laps[0][0]
                    != STOPWATCH_LAP_COUNT):
                raise RuntimeError("Stopwatch did not retain twenty laps")
            self._phase = 3
            self._index = 1
            return False
        if self._phase == 3:
            self._controller.stopwatch_cursor = self._index
            self._index += 1
            if self._index == STOPWATCH_LAP_COUNT:
                self._phase = 4
                self._index = STOPWATCH_LAP_COUNT - 2
            return False
        if self._phase == 4:
            self._controller.stopwatch_cursor = self._index
            self._index -= 1
            if self._index < 0:
                self._phase = 5
            return False
        self._controller.stopwatch_page = False
        if not self._controller.stopwatch_running:
            raise RuntimeError("Stopwatch stopped while leaving its page")
        return True

    def _step_page_round_trips(self):
        if self._phase == 0:
            self._controller.page_stack.clear()
            self._controller.page_stack.append("root")
            self._phase = 1
            self._index = 0
            return False
        if self._phase == 1:
            page_id = APPLICATION_PAGE_IDS[self._index]
            self._controller.page_stack.append(page_id)
            if self._controller.page_stack[-1] != page_id:
                raise RuntimeError("Page enter did not expose target")
            self._phase = 2
            return False
        self._controller.page_stack.pop()
        if (len(self._controller.page_stack) != 1
                or self._controller.page_stack[0] != "root"):
            raise RuntimeError("Page exit did not return to root")
        self._index += 1
        if self._index == len(APPLICATION_PAGE_IDS):
            return True
        self._phase = 1
        return False


class InMemoryApplicationScenarioAdapter(
        ResidentApplicationScenarioAdapter):
    """Executable host Adapter for seven diagnostics or one ordered matrix."""

    __slots__ = ("_state_controller",)

    def __init__(self):
        self._state_controller = _InMemoryScenarioController()
        ResidentApplicationScenarioAdapter.__init__(
            self, self._state_controller)
