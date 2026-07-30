"""Cold staged add-on reload transaction loaded only on demand."""

from calc.functions import FUNCTION_GROUPS, FunctionRegistry, build_registry
from calc.loader import (
    LoadReport, _append_error, _default_func_dir,
    _dependency_names, _discard_loader_metadata, _execute_plugin, _join,
    _normalise_plugin_name, _plugin_files, _register_namespace,
    _validate_exports)
from calc.limits import (
    MAX_DISCOVERED_PLUGIN_FILES, MAX_ENABLED_FUNCTIONS, MAX_ENABLED_PLUGINS,
    MAX_FUNCTION_NAME_LENGTH, MAX_PLUGIN_DEPENDENCY_DEPTH)


def _copy_snapshot_filenames(entries):
    filenames = []
    for entry in entries:
        if len(filenames) >= MAX_DISCOVERED_PLUGIN_FILES:
            raise ValueError("Too many add-on files")
        if isinstance(entry, tuple):
            if len(entry) < 2:
                raise ValueError("Add-on snapshot entry is invalid")
            filename = entry[1]
        else:
            filename = entry
        if not isinstance(filename, str):
            raise ValueError("Add-on snapshot filename must be a string")
        filenames.append(filename)
    return filenames


class _CatalogReport(LoadReport):
    __slots__ = ("functions", "exports")

    def __init__(self):
        super().__init__()
        self.functions = {}
        self.exports = {}


class FunctionEnvironment:
    """Cold, bounded source snapshot used only by scenario transactions."""

    __slots__ = (
        "directory", "report", "_paths", "_namespaces", "_source_errors",
        "_catalog_exports", "_catalog_states", "_scan_index",
        "_scan_names", "_scan_all", "_catalog_cursor", "_catalog_depths",
        "_complete", "_cancelled")

    _PENDING = 0
    _LOADED = 1
    _FAILED = 2

    def __init__(self, func_dir=None, files=None, selected_files=None):
        self.directory = _default_func_dir() if func_dir is None else func_dir
        self.report = _CatalogReport()
        self._paths = {}
        self._namespaces = {}
        self._source_errors = {}
        self._catalog_exports = {}
        self._catalog_states = {}
        self._scan_index = 0
        self._scan_names = []
        self._scan_all = selected_files is None
        self._catalog_cursor = 0
        self._catalog_depths = {}
        self._complete = False
        self._cancelled = False

        if files is None:
            try:
                filenames = _plugin_files(self.directory)
            except OSError:
                filenames = []
            except ValueError as error:
                filenames = []
                _append_error(self.report, "Add-ons", str(error))
        else:
            try:
                filenames = _copy_snapshot_filenames(files)
            except ValueError as error:
                filenames = []
                _append_error(self.report, "Add-ons", str(error))
        filenames.sort()

        snapshot = []
        for filename in filenames:
            name = filename[:-3]
            snapshot.append((name, filename))
            try:
                _normalise_plugin_name(name)
            except ValueError as error:
                detail = str(error)
                self._source_errors[name] = detail
                self._catalog_states[name] = self._FAILED
                _append_error(self.report, name, detail)
                continue
            self._paths[name] = _join(self.directory, filename)
        self.report.files = snapshot

        if self._scan_all:
            for name, _ in snapshot:
                if name in self._paths:
                    self._catalog_states[name] = self._PENDING
                    self._scan_names.append(name)
        else:
            for selected in selected_files:
                try:
                    name = _normalise_plugin_name(selected)
                except ValueError as error:
                    _append_error(self.report, str(selected), str(error))
                    continue
                if name not in self._paths:
                    _append_error(
                        self.report, name, "Dependency file was not found")
                    continue
                if name not in self._catalog_states:
                    if len(self._scan_names) >= MAX_ENABLED_PLUGINS:
                        self._catalog_states[name] = self._FAILED
                        _append_error(
                            self.report, name, "Loaded add-on limit reached")
                        continue
                    self._catalog_states[name] = self._PENDING
                    self._catalog_depths[name] = 1
                    self._scan_names.append(name)

    @property
    def complete(self):
        return self._complete

    @property
    def namespaces(self):
        return self._namespaces

    @property
    def source_errors(self):
        return self._source_errors

    def _record_source_error(self, name, error):
        detail = str(error)
        self._source_errors[name] = detail
        self._catalog_states[name] = self._FAILED
        _append_error(self.report, name, detail)

    def _queue_dependency(self, name, depth):
        if name not in self._paths:
            return
        if depth > MAX_PLUGIN_DEPENDENCY_DEPTH:
            if self._catalog_states.get(name) != self._FAILED:
                self._catalog_states[name] = self._FAILED
                _append_error(
                    self.report, name, "Dependency depth limit reached")
            return
        if name in self._catalog_states:
            return
        if len(self._scan_names) >= MAX_ENABLED_PLUGINS:
            self._catalog_states[name] = self._FAILED
            _append_error(
                self.report, name, "Loaded add-on limit reached")
            return
        self._catalog_states[name] = self._PENDING
        self._catalog_depths[name] = depth
        self._scan_names.append(name)

    def _inspect_one(self, name, _filename):
        path = self._paths.get(name)
        if path is None:
            return
        namespace = None
        try:
            namespace = _execute_plugin(
                path, "scicalc_plugin_scan_" + name)
            dependencies = _dependency_names(namespace, path)
            exports = _validate_exports(namespace, path)
            self._namespaces[name] = namespace
            self._catalog_exports[name] = exports
            self.report.dependencies[name] = dependencies
            self.report.exports[name] = tuple(exports)
            if not self._scan_all:
                depth = self._catalog_depths.get(name, 1) + 1
                for dependency in dependencies:
                    self._queue_dependency(dependency, depth)
        except MemoryError:
            raise
        except Exception as error:
            if namespace is not None:
                namespace.clear()
            self._record_source_error(name, error)

    def _dependency_status(self, name):
        dependencies = self.report.dependencies.get(name, ())
        for dependency in dependencies:
            state = self._catalog_states.get(dependency)
            if state is None:
                _append_error(
                    self.report, dependency, "Dependency file was not found")
                return False, dependency
            if state == self._FAILED:
                return False, dependency
            if state != self._LOADED:
                return False, None
        return True, None

    def _catalog_one(self, name):
        path = self._paths[name]
        namespace = self._namespaces[name]
        dependencies = self.report.dependencies.get(name, ())
        dependency_exports = {}
        for dependency in dependencies:
            dependency_exports[dependency] = self._catalog_exports[dependency]
        try:
            staging, _ = _register_namespace(
                namespace, path, 0, dependency_exports,
                self._catalog_exports[name])
            names = []
            for function_name in staging.keys():
                names.append(function_name)
            self.report.functions[name] = names
            self._catalog_states[name] = self._LOADED
        except MemoryError:
            raise
        except Exception as error:
            self._catalog_states[name] = self._FAILED
            _append_error(self.report, name, str(error))

    def _catalog_step(self):
        names = self._scan_names
        pending = None
        for _ in range(len(names)):
            name = names[self._catalog_cursor]
            self._catalog_cursor = (self._catalog_cursor + 1) % len(names)
            if self._catalog_states.get(name) != self._PENDING:
                continue
            if pending is None:
                pending = name
            ready, failed_dependency = self._dependency_status(name)
            if failed_dependency is not None:
                self._catalog_states[name] = self._FAILED
                _append_error(
                    self.report, name, "Dependency failed: " + failed_dependency)
                return False
            if not ready:
                continue
            self._catalog_one(name)
            return False

        if pending is not None:
            self._catalog_states[pending] = self._FAILED
            _append_error(self.report, pending, "Dependency cycle")
            return False

        self._catalog_exports.clear()
        self._complete = True
        return True

    def step_source(self):
        if self._complete or self._cancelled:
            return self._scan_index >= len(self._scan_names)
        if self._scan_index >= len(self._scan_names):
            return True
        name = self._scan_names[self._scan_index]
        self._scan_index += 1
        if self._catalog_states.get(name) != self._FAILED:
            self._inspect_one(name, None)
        complete = self._scan_index >= len(self._scan_names)
        if complete:
            self._catalog_depths.clear()
        return complete

    def step(self):
        if self._complete or self._cancelled:
            return self._complete
        if self._scan_index < len(self._scan_names):
            self.step_source()
            return False
        return self._catalog_step()

    def release_loaded_metadata(self, loaded):
        for item in loaded:
            namespace = self._namespaces.get(item[0])
            if namespace is not None:
                _discard_loader_metadata(namespace)

    def cancel(self):
        if self._cancelled:
            return False
        self._namespaces.clear()
        self._source_errors.clear()
        self._catalog_exports.clear()
        self._paths.clear()
        self._catalog_states.clear()
        self._catalog_depths.clear()
        self._scan_names[:] = []
        self._cancelled = True
        self._complete = True
        return True


def _selection_plugin_name(selected):
    """Validate one selection token without silently dropping unknown input."""
    if type(selected) is not str:
        raise ValueError("Function selection names must be strings")
    if selected.startswith("plugin:"):
        return _normalise_plugin_name(selected)
    # Keep a hostile non-plugin token out of the group dictionary until its
    # hash input is capped.  All canonical group names fit this shared limit.
    if len(selected) > MAX_FUNCTION_NAME_LENGTH:
        raise ValueError("Unknown function selection")
    if selected in FUNCTION_GROUPS:
        return None
    raise ValueError("Unknown function selection")


def _split_reload_selection(selection):
    """Separate one bounded panel selection into groups and add-on names."""
    if type(selection) not in (list, tuple):
        raise ValueError("enabled_functions must be a list")
    if len(selection) > MAX_ENABLED_FUNCTIONS:
        raise ValueError("Enabled function limit reached")
    groups = []
    plugins = []
    for selected in selection:
        name = _selection_plugin_name(selected)
        if name is None:
            if selected not in groups:
                groups.append(selected)
        elif name not in plugins:
            if len(plugins) >= MAX_ENABLED_PLUGINS:
                raise ValueError("Enabled add-on limit reached")
            plugins.append(name)
    return groups, plugins


def _snapshot_reload_selection(selection):
    """Copy one bounded external selection before it enters panel state."""
    if type(selection) not in (list, tuple):
        raise ValueError("enabled_functions must be a list")
    if len(selection) > MAX_ENABLED_FUNCTIONS:
        raise ValueError("Enabled function limit reached")
    snapshot = []
    plugins = []
    for selected in selection:
        name = _selection_plugin_name(selected)
        if name is not None and name not in plugins:
            if len(plugins) >= MAX_ENABLED_PLUGINS:
                raise ValueError("Enabled add-on limit reached")
            plugins.append(name)
        snapshot.append(selected)
    # A tuple input can otherwise be returned by identity on CPython.  The
    # transaction must not retain either caller-owned container shape.
    return tuple(snapshot)


_PLUGIN_RELOAD_MISSING = object()


class _FunctionPanelPluginReloadCheckpoint:
    """No-copy settings/panel checkpoint owned by one staged reload."""

    __slots__ = (
        "_panel", "_settings", "_settings_had_enabled", "_settings_enabled",
        "_pending_enabled", "_flags", "_had_pending",
        "_selection", "_publish_panel_state", "_plugin_dependencies",
        "_plugin_files", "_closed")

    def __init__(self, panel, settings, selection, publish_panel_state):
        if not isinstance(settings, dict):
            raise ValueError("Function settings must be a dictionary")
        self._panel = panel
        self._settings = settings
        self._settings_had_enabled = "enabled_functions" in settings
        self._settings_enabled = settings.get(
            "enabled_functions", _PLUGIN_RELOAD_MISSING)
        self._pending_enabled = panel._state[0][3]
        self._flags = panel._flags
        self._had_pending = (self._pending_enabled is not None
                             or bool(self._flags & 2))
        self._selection = selection
        self._publish_panel_state = publish_panel_state
        self._plugin_dependencies = panel._state[2][0]
        self._plugin_files = panel._state[2][1]
        self._closed = False

    @property
    def selection(self):
        return self._selection

    def commit(self, report):
        """Publish already-built metadata after the registry swaps safely."""
        panel = self._panel
        if self._closed or panel is None:
            raise RuntimeError("Function panel reload checkpoint is closed")
        if not self._publish_panel_state:
            # A controller-supplied fixture selection is transaction scratch,
            # not a user Add-ons choice.  Its registry proof must never alter
            # deferred settings, pending UI state, or panel metadata.
            return True
        # ``selection`` is caller-owned scratch state.  Assigning it is the
        # only potentially allocating part when an old settings file omitted
        # the key, and the loader restores this checkpoint if it fails.
        self._settings["enabled_functions"] = self._selection
        panel._state[0][3] = None
        panel._flags &= ~2
        panel._state[2][1] = report.files
        panel._state[2][0] = report.dependencies
        # Rebuild labels only during the next ordinary activation.  Building a
        # Menu here would put text allocation after the live-registry commit.
        panel._flags &= ~4
        return True

    def restore(self):
        """Restore pending selection/settings and metadata without copying."""
        panel = self._panel
        if self._closed or panel is None:
            raise RuntimeError("Function panel reload checkpoint is closed")
        if not self._publish_panel_state:
            return True
        settings = self._settings
        if self._had_pending:
            # Existing UI reloads queue the old selection in _pending_enabled
            # before replacing the settings value.  A failed staged reload
            # must return to that pre-queue selection, not keep the candidate.
            if self._flags & 2:
                if "enabled_functions" in settings:
                    del settings["enabled_functions"]
            else:
                settings["enabled_functions"] = self._pending_enabled
            panel._state[0][3] = None
        else:
            if self._settings_had_enabled:
                settings["enabled_functions"] = self._settings_enabled
            elif "enabled_functions" in settings:
                del settings["enabled_functions"]
            panel._state[0][3] = self._pending_enabled
        panel._flags &= ~2
        panel._state[2][1] = self._plugin_files
        panel._state[2][0] = self._plugin_dependencies
        panel._flags = (panel._flags & ~4) | (self._flags & 4)
        return True

    def release(self):
        """Release checkpoint references after the loader has finished."""
        if self._closed:
            return True
        panel = self._panel
        if panel is not None and panel._state[1][3] is self:
            panel._state[1][3] = None
        self._panel = None
        self._settings = None
        self._selection = None
        self._plugin_dependencies = None
        self._plugin_files = None
        self._closed = True
        return True


def open_function_panel_reload_checkpoint(panel, settings=None,
                                          selection=None):
    """Open the panel-owned part of one low-frequency reload transaction."""
    if panel._state[1][3] is not None:
        raise RuntimeError("Function panel reload checkpoint is already open")
    settings = panel._settings_value() if settings is None else settings
    publish_panel_state = selection is None
    if selection is None:
        if panel._state[0][3] is not None or panel._flags & 2:
            selection = settings.get("enabled_functions", ())
        elif not panel._flags & 4 and not panel._items:
            # Inactive panels can release labels to lower the heap peak.  Their
            # empty item owner is not a request to disable every function.
            from calc.functions import DEFAULT_ENABLED_GROUPS
            selection = settings.get(
                "enabled_functions", DEFAULT_ENABLED_GROUPS)
        else:
            selection = panel.get_enabled_list()
    checkpoint = _FunctionPanelPluginReloadCheckpoint(
        panel, settings, selection, publish_panel_state)
    panel._state[1][3] = checkpoint
    return checkpoint


class PluginReloadTransaction:
    """One bounded, identity-stable staged reload of panel-selected add-ons.

    The public interface is deliberately small: call :meth:`step` until
    :attr:`complete`, then call :meth:`close` to finalize a successful reload
    or release a rejected one.  Each step advances only one source,
    dependency, registration, staging commit, or final live commit unit.
    """

    _PREPARE = 0
    _SOURCE = 1
    _DEPENDENCY = 2
    _REGISTER = 3
    _STAGE_COMMIT = 4
    _LIVE_COMMIT = 5
    _COMPLETE = 6
    _FAILED = 7

    __slots__ = (
        "_registry", "_registry_state", "_panel_checkpoint", "_directory",
        "_files", "_phase", "_environment", "_staging", "_report",
        "_requested_plugins", "_requested_set", "_states",
        "_dependency_cursor", "_dependency_idle", "_remaining",
        "_pending_name", "_pending_plugin", "_committed", "_closed")

    def __init__(self, registry, panel, settings=None, func_dir=None,
                 files=None, selection=None):
        if not isinstance(registry, FunctionRegistry):
            raise TypeError("Plugin reload requires a FunctionRegistry")
        checkpoint_opener = getattr(panel, "open_plugin_reload_checkpoint", None)
        if not callable(checkpoint_opener):
            raise TypeError("Plugin reload requires a FunctionPanel checkpoint")

        selection_snapshot = None
        if selection is not None:
            # Keep the bounded allocation ahead of all checkpoint work.  An
            # OOM here therefore leaves the live registry and panel untouched.
            selection_snapshot = _snapshot_reload_selection(selection)
        # Both tokens retain existing references only.  If either allocation
        # fails, no live registry field or FunctionPanel setting was touched.
        registry_state = registry._plugin_reload_state()
        if selection is None:
            # Preserve the existing call shape for normal UI reloads and the
            # intentionally narrow checkpoint adapters used by callers.
            panel_checkpoint = checkpoint_opener(settings)
        else:
            panel_checkpoint = checkpoint_opener(
                settings, selection=selection_snapshot)

        self._registry = registry
        self._registry_state = registry_state
        self._panel_checkpoint = panel_checkpoint
        self._directory = func_dir
        self._files = files
        self._phase = self._PREPARE
        self._environment = None
        self._staging = None
        self._report = None
        self._requested_plugins = None
        self._requested_set = None
        self._states = None
        self._dependency_cursor = 0
        self._dependency_idle = 0
        self._remaining = 0
        self._pending_name = None
        self._pending_plugin = None
        self._committed = False
        self._closed = False

    @property
    def report(self):
        """The compact report until a successful close releases transaction state."""
        return self._report

    @property
    def complete(self):
        return self._phase in (self._COMPLETE, self._FAILED)

    @property
    def succeeded(self):
        return self._phase == self._COMPLETE and self._committed

    def _require_open(self):
        registry = self._registry
        if self._closed or registry is None:
            raise RuntimeError("Plugin reload transaction is closed")
        return registry

    def _restore_checkpoint(self):
        """Restore the two no-copy checkpoints without loader cleanup work."""
        self._registry._restore_plugin_reload_state(self._registry_state)
        self._panel_checkpoint.restore()
        self._committed = False

    def _restore_after_primary_error(self):
        """Best-effort rollback that never replaces the primary failure."""
        # A late live commit may already have swapped the private registry
        # tables before a panel publication fails.  Mark it rejected before
        # attempting either restoration, so a cleanup failure cannot leave a
        # later close() believing that the staged result is still successful.
        self._committed = False
        try:
            self._registry._restore_plugin_reload_state(self._registry_state)
        except Exception:
            pass
        try:
            self._panel_checkpoint.restore()
        except Exception:
            pass

    def _fail(self):
        # Source/dependency/registration failures are represented in the
        # compact report.  A secondary rollback fault must not replace that
        # result or prevent close() from retrying the retained checkpoints.
        self._restore_after_primary_error()
        self._phase = self._FAILED
        return True

    def _prepare_step(self):
        registry = self._require_open()
        groups, plugins = _split_reload_selection(
            self._panel_checkpoint.selection)
        # This private registry owns every allocation until the final commit.
        # The live function limit remains part of the complete checkpoint and
        # is also preserved in the staged object for success-path parity.
        staging = build_registry(groups, registry._function_limit)
        staging.angle_mode = registry.angle_mode
        environment = FunctionEnvironment(
            self._directory, self._files, selected_files=plugins)

        self._staging = staging
        self._environment = environment
        self._report = environment.report
        self._requested_plugins = plugins
        self._requested_set = set(plugins)
        self._states = {}

        for error_name, _ in self._report.errors:
            if error_name == "Add-ons":
                return self._fail()
        # A requested filename absent from the bounded snapshot has already
        # received a compact report error from FunctionEnvironment.  Do not
        # execute another source before reporting the rejected transaction.
        for name in plugins:
            if (name not in environment._paths
                    or environment._catalog_states.get(name)
                    == environment._FAILED):
                return self._fail()
        self._phase = self._SOURCE if plugins else self._LIVE_COMMIT
        return False

    def _source_step(self):
        environment = self._environment
        if environment._scan_index < len(environment._scan_names):
            name = environment._scan_names[environment._scan_index]
            environment.step_source()
            if (name in environment.source_errors
                    or environment._catalog_states.get(name)
                    == environment._FAILED):
                return self._fail()
            return False
        self._remaining = len(environment._scan_names)
        self._dependency_cursor = 0
        self._dependency_idle = 0
        self._phase = (self._DEPENDENCY if self._remaining
                       else self._LIVE_COMMIT)
        return False

    def _dependency_step(self):
        if self._remaining <= 0:
            self._phase = self._LIVE_COMMIT
            return False
        environment = self._environment
        names = environment._scan_names
        total = len(names)
        if total == 0:
            self._phase = self._LIVE_COMMIT
            return False
        if self._dependency_cursor >= total:
            self._dependency_cursor = 0
        name = names[self._dependency_cursor]
        self._dependency_cursor += 1
        if self._states.get(name) == environment._LOADED:
            return False
        if name in environment.source_errors:
            return self._fail()

        dependencies = self._report.dependencies.get(name, ())
        for dependency in dependencies:
            if (dependency not in environment._paths
                    or dependency in environment.source_errors
                    or environment._catalog_states.get(dependency)
                    == environment._FAILED):
                _append_error(
                    self._report, name,
                    "Dependency failed: " + dependency)
                return self._fail()
            if self._states.get(dependency) != environment._LOADED:
                self._dependency_idle += 1
                if self._dependency_idle >= total:
                    _append_error(self._report, name, "Dependency cycle")
                    return self._fail()
                return False

        self._dependency_idle = 0
        self._pending_name = name
        self._phase = self._REGISTER
        return False

    def _registration_step(self):
        name = self._pending_name
        environment = self._environment
        dependencies = self._report.dependencies.get(name, ())
        try:
            dependency_exports = {}
            for dependency in dependencies:
                dependency_exports[dependency] = self._staging.plugin(dependency)
            plugin_staging, exports = _register_namespace(
                environment.namespaces[name], environment._paths[name],
                self._staging.angle_mode, dependency_exports,
                environment._catalog_exports.get(name))
            # Retain only one uncommitted plugin staging registry at a time.
            self._pending_plugin = (name, plugin_staging, exports)
            self._phase = self._STAGE_COMMIT
            return False
        except MemoryError:
            raise
        except Exception as error:
            _append_error(self._report, name, str(error))
            return self._fail()

    def _stage_commit_step(self):
        name, plugin_staging, exports = self._pending_plugin
        try:
            # FunctionRegistry completes all copying before it swaps staging-
            # table references.  An OOM therefore leaves both checkpointed live
            # state and the current private staging table usable for cleanup.
            self._staging.commit_plugin(
                name, plugin_staging, exports)
            function_names = []
            for function_name in plugin_staging.keys():
                function_names.append(function_name)
            self._report.functions[name] = function_names
            self._report.exports[name] = tuple(exports)
            message = self._environment.namespaces[name].get("WELCOME", "")
            self._report.loaded.append((name, len(plugin_staging), message))
            if name not in self._requested_set:
                self._report.auto_enabled.append(name)
            self._states[name] = self._environment._LOADED
            self._environment._catalog_states[name] = self._environment._LOADED
            self._remaining -= 1
            self._pending_name = None
            self._pending_plugin = None
            self._phase = (self._DEPENDENCY if self._remaining
                           else self._LIVE_COMMIT)
            return False
        except MemoryError:
            raise
        except Exception as error:
            _append_error(self._report, name, str(error))
            return self._fail()

    def _live_commit_step(self):
        # Keep normal loader report ownership: registry consumers hold compact
        # metadata tables directly, while source namespaces remain loader-only
        # until close confirms the final identity-stable replacement.
        # Selected source/dependency/registration failures already call
        # _fail() in their own unit.  Directory-only catalog diagnostics for
        # unrelated disabled files do not reject a valid selected closure.
        self._staging.plugin_errors = self._report.errors
        self._staging.plugin_functions = self._report.functions
        self._staging.plugin_dependencies = self._report.dependencies
        self._staging.plugin_files = self._report.files
        self._registry.replace(self._staging)
        self._panel_checkpoint.commit(self._report)
        self._committed = True
        self._phase = self._COMPLETE
        return True

    def step(self):
        """Advance exactly one staged reload unit and return terminal status."""
        self._require_open()
        if self.complete:
            return True
        try:
            if self._phase == self._PREPARE:
                return self._prepare_step()
            if self._phase == self._SOURCE:
                return self._source_step()
            if self._phase == self._DEPENDENCY:
                return self._dependency_step()
            if self._phase == self._REGISTER:
                return self._registration_step()
            if self._phase == self._STAGE_COMMIT:
                return self._stage_commit_step()
            if self._phase == self._LIVE_COMMIT:
                return self._live_commit_step()
            raise RuntimeError("Plugin reload transaction has an invalid phase")
        except MemoryError:
            # Restore only direct references/scalars here.  Loader metadata is
            # released later by close(), so a cleanup fault cannot hide this
            # primary MemoryError.
            self._restore_after_primary_error()
            self._phase = self._FAILED
            raise
        except Exception:
            # Rollback can itself run out of heap (for example, a panel
            # settings adapter).  Keep the original ordinary exception as the
            # primary signal and retain the checkpoint for close() retry.
            self._restore_after_primary_error()
            self._phase = self._FAILED
            raise

    def _release_loader(self, successful):
        environment = self._environment
        if environment is None:
            return
        if successful:
            environment.release_loaded_metadata(self._report.loaded)
        environment.cancel()

    def cancel(self):
        """Reject this reload and release its source snapshot immediately."""
        if self._closed:
            return True
        self._require_open()
        # Even if direct restoration faults, this transaction must no longer
        # look like a successful live commit.  close() retains the checkpoint
        # and can retry the restoration later.
        self._phase = self._FAILED
        self._committed = False
        self._restore_checkpoint()
        return self.close()

    def close(self):
        """Finalize success or restore failure, then release loader metadata."""
        if self._closed:
            return True
        registry = self._require_open()
        successful = self.succeeded
        if not successful:
            self._restore_checkpoint()
        # Do not release checkpoint ownership before every metadata deletion
        # completes; close can then be retried after an unexpected cleanup fault.
        self._release_loader(successful)
        self._panel_checkpoint.release()
        self._registry = None
        self._registry_state = None
        self._panel_checkpoint = None
        self._directory = None
        self._files = None
        self._environment = None
        self._staging = None
        self._report = None
        self._requested_plugins = None
        self._requested_set = None
        self._states = None
        self._pending_name = None
        self._pending_plugin = None
        self._closed = True
        return True


def open_plugin_reload_transaction(registry, panel, settings=None, func_dir=None,
                                   files=None, selection=None):
    """Open the bounded staged reload seam used by a future controller."""
    return PluginReloadTransaction(
        registry, panel, settings, func_dir, files, selection)
