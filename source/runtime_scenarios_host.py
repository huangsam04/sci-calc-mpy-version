"""CPython-only state controller for the application acceptance scenarios."""

import sys

if getattr(sys.implementation, "name", "") == "micropython":
    raise ImportError("runtime_scenarios_host is CPython-only")

from runtime_scenarios import (
    APPLICATION_CAPABILITIES,
    APPLICATION_PAGE_IDS,
    ERROR_KIND_COUNT,
    MAX_CALCULATOR_HISTORY,
    MAX_CALCULATOR_INPUT,
    STOPWATCH_LAP_COUNT,
    ResidentApplicationScenarioAdapter,
)


_VARIABLE_QUOTA = 32
_PLOT_WORKSPACE_BYTES = 1404


class _InMemoryScenarioController:
    __slots__ = (
        "history", "history_cursor", "error_code", "error_visible",
        "variables", "durable_variables", "plot_workspace",
        "plot_program", "plot_range", "plot_page", "plugins_enabled",
        "plugin_catalog", "plugin_live", "plugin_revision",
        "plugin_rescans", "stopwatch_running", "stopwatch_elapsed",
        "stopwatch_laps", "stopwatch_cursor", "stopwatch_next_lap",
        "stopwatch_page", "page_stack")

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

    def supports(self, capability):
        return capability in APPLICATION_CAPABILITIES

    def snapshot(self, capability):
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

    def restore(self, capability, snapshot):
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
        return self.snapshot(capability) == snapshot

    def perform(self, runtime, capability, round_index):
        if capability == APPLICATION_CAPABILITIES[0]:
            return self._calculator_history()
        if capability == APPLICATION_CAPABILITIES[1]:
            return self._error_lifecycle()
        if capability == APPLICATION_CAPABILITIES[2]:
            return self._variable_quota_restart()
        if capability == APPLICATION_CAPABILITIES[3]:
            return self._plot_pipeline()
        if capability == APPLICATION_CAPABILITIES[4]:
            return self._plugin_reload()
        if capability == APPLICATION_CAPABILITIES[5]:
            return self._stopwatch_laps()
        if capability == APPLICATION_CAPABILITIES[6]:
            return self._page_round_trips()
        raise ValueError("Unknown application capability")

    def _calculator_history(self):
        self.history.clear()
        for index in range(MAX_CALCULATOR_HISTORY):
            prefix = str(index) + ":"
            expression = prefix + (
                "9" * (MAX_CALCULATOR_INPUT - len(prefix)))
            if len(expression) != MAX_CALCULATOR_INPUT:
                raise RuntimeError("History expression is not maximum size")
            self.history.append((expression, expression))

        operations = MAX_CALCULATOR_HISTORY
        for index in range(MAX_CALCULATOR_HISTORY - 1, -1, -1):
            self.history_cursor = index
            if len(self.history[index][0]) != MAX_CALCULATOR_INPUT:
                raise RuntimeError("History traversal lost an entry")
            operations += 1
        for index in range(MAX_CALCULATOR_HISTORY):
            self.history_cursor = index
            if len(self.history[index][0]) != MAX_CALCULATOR_INPUT:
                raise RuntimeError("History traversal lost an entry")
            operations += 1
        return operations

    def _error_lifecycle(self):
        seen = 0
        operations = 0
        for error_code in range(ERROR_KIND_COUNT):
            self.error_code = error_code
            self.error_visible = True
            seen |= 1 << error_code
            operations += 1
            self.error_code = None
            self.error_visible = False
            operations += 1
        if seen != (1 << ERROR_KIND_COUNT) - 1:
            raise RuntimeError("Error matrix did not cover every kind")
        if self.error_visible or self.error_code is not None:
            raise RuntimeError("Final error did not close")
        return operations

    def _set_variable(self, name, value):
        if name not in self.variables and len(self.variables) >= _VARIABLE_QUOTA:
            return False
        self.variables[name] = value
        return True

    def _variable_quota_restart(self):
        self.variables.clear()
        self.durable_variables.clear()
        operations = 0
        for index in range(_VARIABLE_QUOTA):
            if not self._set_variable("v" + str(index), index):
                raise RuntimeError(
                    "Variable quota rejected an in-range item")
            operations += 1
        if self._set_variable("overflow", _VARIABLE_QUOTA):
            raise RuntimeError("Variable quota accepted an extra item")
        operations += 1

        self.durable_variables.update(self.variables)
        operations += 1
        self.variables.clear()
        self.variables.update(self.durable_variables)
        operations += 1
        del self.variables["v0"]
        operations += 1
        if not self._set_variable("replacement", 100):
            raise RuntimeError(
                "Deleted variable capacity was not reusable")
        operations += 1

        if len(self.variables) != _VARIABLE_QUOTA:
            raise RuntimeError("Variable count changed across restart")
        if self.variables.get("replacement") != 100:
            raise RuntimeError("Replacement variable was not retained")
        return operations

    @staticmethod
    def _plot_value(x_value):
        if x_value < 0:
            raise ValueError("domain")
        return x_value * x_value

    def _plot_pipeline(self):
        self.plot_page = True
        self.plot_workspace = bytearray(_PLOT_WORKSPACE_BYTES)
        operations = 1
        self.plot_program = "sqrt_domain_probe"
        operations += 1

        low = None
        high = None
        domain_errors = 0
        for x_value in range(-32, 33):
            try:
                value = self._plot_value(x_value)
            except ValueError:
                domain_errors += 1
                continue
            low = value if low is None or value < low else low
            high = value if high is None or value > high else high
        if low is None or high is None or domain_errors == 0:
            raise RuntimeError("Plot autoscale missed its domain error")
        self.plot_range = (low, high)
        operations += 1

        drawn = 0
        for column in range(256):
            try:
                self._plot_value(column - 128)
            except ValueError:
                domain_errors += 1
                continue
            offset = (column * 5) % len(self.plot_workspace)
            self.plot_workspace[offset] |= 1 << (column & 7)
            drawn += 1
        if drawn == 0 or not any(self.plot_workspace):
            raise RuntimeError("Plot draw produced no curve")
        operations += 1

        self.plot_workspace = None
        self.plot_program = None
        self.plot_range = None
        self.plot_page = False
        operations += 1
        return operations

    def _reload_plugins(self):
        staging = []
        for name in sorted(self.plugins_enabled):
            dependencies = self.plugin_catalog.get(name)
            if dependencies is None:
                return False
            for dependency in dependencies:
                if dependency not in self.plugins_enabled:
                    return False
            staging.append(name)
        self.plugin_live = tuple(staging)
        self.plugin_revision += 1
        return True

    def _plugin_reload(self):
        self.plugins_enabled.clear()
        self.plugins_enabled.add("core")
        operations = 0

        self.plugins_enabled.add("helper")
        operations += 1
        self.plugins_enabled.add("dependent")
        operations += 1
        self.plugins_enabled.remove("dependent")
        operations += 1

        self.plugin_catalog.clear()
        self.plugin_catalog.update({
            "core": (),
            "helper": (),
            "dependent": ("helper",),
            "broken": ("missing",),
        })
        self.plugin_rescans += 1
        operations += 1
        self.plugins_enabled.add("dependent")
        operations += 1
        if not self._reload_plugins():
            raise RuntimeError("Valid plugin dependency chain did not load")
        operations += 1

        committed_live = self.plugin_live
        committed_revision = self.plugin_revision
        self.plugins_enabled.add("broken")
        operations += 1
        if self._reload_plugins():
            raise RuntimeError(
                "Broken plugin dependency unexpectedly loaded")
        operations += 1
        if (self.plugin_live != committed_live
                or self.plugin_revision != committed_revision):
            raise RuntimeError("Failed plugin reload changed live registry")
        return operations

    def _stopwatch_laps(self):
        self.stopwatch_laps.clear()
        self.stopwatch_elapsed = 0
        self.stopwatch_cursor = 0
        self.stopwatch_next_lap = 1
        self.stopwatch_page = True
        self.stopwatch_running = True
        operations = 1

        for _ in range(STOPWATCH_LAP_COUNT):
            self.stopwatch_elapsed += 137
            self.stopwatch_laps.insert(
                0, (self.stopwatch_next_lap, self.stopwatch_elapsed))
            self.stopwatch_next_lap += 1
            operations += 1
        if (len(self.stopwatch_laps) != STOPWATCH_LAP_COUNT
                or self.stopwatch_laps[0][0] != STOPWATCH_LAP_COUNT):
            raise RuntimeError("Stopwatch did not retain twenty laps")

        for cursor in range(1, STOPWATCH_LAP_COUNT):
            self.stopwatch_cursor = cursor
            operations += 1
        for cursor in range(STOPWATCH_LAP_COUNT - 2, -1, -1):
            self.stopwatch_cursor = cursor
            operations += 1
        if self.stopwatch_cursor != 0:
            raise RuntimeError("Stopwatch lap scrolling did not return")

        self.stopwatch_page = False
        operations += 1
        if not self.stopwatch_running:
            raise RuntimeError("Stopwatch stopped while leaving its page")
        return operations

    def _page_round_trips(self):
        self.page_stack.clear()
        self.page_stack.append("root")
        operations = 0
        visited = 0
        for index, page_id in enumerate(APPLICATION_PAGE_IDS):
            self.page_stack.append(page_id)
            if self.page_stack[-1] != page_id:
                raise RuntimeError("Page enter did not expose target")
            visited |= 1 << index
            operations += 1
            self.page_stack.pop()
            if len(self.page_stack) != 1 or self.page_stack[0] != "root":
                raise RuntimeError("Page exit did not return to root")
            operations += 1
        if visited != (1 << len(APPLICATION_PAGE_IDS)) - 1:
            raise RuntimeError("Page matrix omitted a resident page")
        return operations


class InMemoryApplicationScenarioAdapter(
        ResidentApplicationScenarioAdapter):
    """Executable host Adapter for seven diagnostics or one ordered matrix."""

    __slots__ = ("_state_controller",)

    def __init__(self):
        self._state_controller = _InMemoryScenarioController()
        ResidentApplicationScenarioAdapter.__init__(
            self, self._state_controller)
