"""Load isolated SCI-CALC add-ons and their declared dependencies."""
import gc
import os

from calc.functions import FunctionRegistry
from calc.limits import (MAX_DISCOVERED_PLUGIN_FILES, MAX_ENABLED_PLUGINS,
                         MAX_FUNCTION_NAME_LENGTH,
                         MAX_PLUGIN_DEPENDENCIES, MAX_PLUGIN_DEPENDENCY_DEPTH,
                         MAX_PLUGIN_EXPORTS, MAX_PLUGIN_FUNCTIONS,
                         MAX_PLUGIN_SOURCE_BYTES, is_ascii_identifier,
                         is_plugin_name)


try:
    _STREAM_EXECUTE = execfile
except NameError:
    _STREAM_EXECUTE = None


def _plugin_files(directory):
    """List a bounded add-on directory without an unbounded target-side list."""
    iterator = getattr(os, "ilistdir", None)
    result = []
    if iterator is not None:
        entries = iterator(directory)
        for entry in entries:
            name = entry[0]
            if name.endswith(".py") and not name.startswith("_"):
                if len(result) >= MAX_DISCOVERED_PLUGIN_FILES:
                    raise ValueError("Too many add-on files")
                result.append(name)
        return result
    for name in os.listdir(directory):
        if name.endswith(".py") and not name.startswith("_"):
            if len(result) >= MAX_DISCOVERED_PLUGIN_FILES:
                raise ValueError("Too many add-on files")
            result.append(name)
    return result


class LoadReport:
    """Outcome of one add-on load pass.

    ``auto_enabled`` contains dependencies that were loaded even though they
    were absent from the selected setting.  The function panel uses the same
    dependency metadata to make that automatic choice visible and persistent.
    """

    __slots__ = (
        "files", "loaded", "errors", "auto_enabled", "dependencies")

    def __init__(self):
        # ``files`` is the directory snapshot that produced this report.  The
        # panel adopts this bounded list directly instead of listing SD again.
        self.files = ()
        self.loaded = []
        self.errors = []
        self.auto_enabled = []
        self.dependencies = {}


def _join(directory, filename):
    if directory.endswith("/") or directory.endswith("\\"):
        return directory + filename
    separator = "\\" if "\\" in directory and "/" not in directory else "/"
    return directory + separator + filename


def _file_path(directory, files, name):
    for file_name, filename in files:
        if file_name == name:
            return _join(directory, filename)
    return None


def _source_size(directory, files, name):
    path = _file_path(directory, files, name)
    if path is None:
        return -1
    try:
        stat = os.stat(path)
        size = getattr(stat, "st_size", None)
        return int(stat[6] if size is None else size)
    except OSError:
        return -1


def _largest_sources_first(names, directory, files):
    """Order a bounded request in place before source allocations fragment."""
    index = 1
    while index < len(names):
        name = names[index]
        size = _source_size(directory, files, name)
        cursor = index
        while (cursor > 0
               and _source_size(
                   directory, files, names[cursor - 1]) < size):
            names[cursor] = names[cursor - 1]
            cursor -= 1
        names[cursor] = name
        index += 1


def _execute_plugin(path, module_name):
    stat = os.stat(path)
    size = getattr(stat, "st_size", None)
    size = int(stat[6] if size is None else size)
    if size < 0 or size > MAX_PLUGIN_SOURCE_BYTES:
        raise ValueError("Add-on source exceeds the size limit")
    namespace = {"__name__": module_name, "__file__": path}
    if _STREAM_EXECUTE is not None:
        # MicroPython's execfile parser consumes the bounded file as a stream.
        # Avoid retaining a second source-sized string beside its compiler
        # output; this was the measured 1,479-byte reload failure at maximum
        # Calculator state.  CPython tests use the fallback below.
        stat = None
        gc.collect()
        _STREAM_EXECUTE(path, namespace)
        return namespace
    with open(path, "r") as source_file:
        source = source_file.read(size)
        grew = source_file.read(1)
    if grew or len(source) > MAX_PLUGIN_SOURCE_BYTES:
        raise ValueError("Add-on source exceeds the size limit")
    # The source and compiler output must overlap, but closed SD handles,
    # stat tuples and the growth probe must not.  This is a cold background
    # operation, outside both key handling and frame production.
    source_file = None
    stat = None
    grew = None
    gc.collect()
    code = compile(source, path, "exec")
    source = None
    gc.collect()
    exec(code, namespace)
    return namespace


def _normalise_plugin_name(name):
    if not isinstance(name, str) or not name:
        raise ValueError("Plugin dependency names must be non-empty strings")
    if name.startswith("plugin:"):
        # Bound before slicing: a hostile external selection must not create a
        # large temporary name while it is being rejected.
        if len(name) > 7 + MAX_FUNCTION_NAME_LENGTH:
            raise ValueError("Plugin name is invalid or too long")
        normalized = name[7:]
    else:
        if len(name) > MAX_FUNCTION_NAME_LENGTH:
            raise ValueError("Plugin name is invalid or too long")
        normalized = name
    if not is_plugin_name(normalized):
        raise ValueError("Plugin name is invalid or too long")
    return normalized


def _dependency_names(namespace, path):
    """Read a plugin's dependency declaration without touching live state."""
    dependencies = namespace.get("DEPENDENCIES")
    if dependencies is None:
        return ()
    if isinstance(dependencies, str):
        dependencies = (dependencies,)
    if not isinstance(dependencies, (tuple, list)):
        raise ValueError(path + " DEPENDENCIES must be a list or tuple")
    if len(dependencies) > MAX_PLUGIN_DEPENDENCIES:
        raise ValueError(path + " has too many dependencies")

    result = []
    for name in dependencies:
        normalized = _normalise_plugin_name(name)
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _validate_exports(namespace, path):
    exports = namespace.get("EXPORTS", {})
    if exports is None:
        return {}
    if not isinstance(exports, dict):
        raise ValueError(path + " EXPORTS must be a dictionary")
    if len(exports) > MAX_PLUGIN_EXPORTS:
        raise ValueError(path + " has too many exports")
    for name in exports:
        if not is_ascii_identifier(name, MAX_FUNCTION_NAME_LENGTH):
            raise ValueError(path + " EXPORTS key is invalid or too long")
    return dict(exports)


def _register_namespace(namespace, path, angle_mode=0, dependency_exports=None,
                        exports=None):
    register = namespace.get("register")
    if not callable(register):
        raise ValueError(path + " must define register(registry)")
    staging = FunctionRegistry(max_functions=MAX_PLUGIN_FUNCTIONS)
    staging.angle_mode = angle_mode
    staging.set_plugin_dependencies(dependency_exports or {})
    register(staging)
    if exports is None:
        exports = _validate_exports(namespace, path)
    return staging, exports


def _append_error(report, name, detail):
    for existing_name, _ in report.errors:
        if existing_name == name:
            return
    report.errors.append((name, detail))
    print("Plugin error " + name + ": " + detail)


_LOADER_NAMESPACE_METADATA = (
    "register", "DEPENDENCIES", "EXPORTS", "WELCOME")
_BUNDLED_PLUGIN_FILES = ("basic.py", "solve.py", "trig.py")


def _discard_loader_metadata(namespace):
    """Release metadata that callbacks never need after a successful commit."""
    for name in _LOADER_NAMESPACE_METADATA:
        if name in namespace:
            del namespace[name]


def _default_func_dir():
    return "/sd/Add-ons"


def load_function_files(registry, enabled_files=None, func_dir=None,
                        in_place=False):
    """Register selected add-ons and recursively load their dependencies.

    Add-ons declare dependencies with ``DEPENDENCIES = ("other",)``.  A
    dependent can read the other add-on's explicit ``EXPORTS`` through
    ``context.plugin("other")`` at evaluation time, or ``registry.plugin``
    while it is registering.  Each add-on is still staged independently, so a
    failure never partially modifies the live registry.
    """
    canonical_directory = func_dir is None
    if canonical_directory:
        func_dir = _default_func_dir()
    report = LoadReport()
    try:
        filenames = _plugin_files(func_dir)
    except OSError:
        if not canonical_directory:
            return report
        filenames = []
    except ValueError as error:
        _append_error(report, "Add-ons", str(error))
        return report
    if canonical_directory:
        for filename in _BUNDLED_PLUGIN_FILES:
            if filename not in filenames:
                filenames.append(filename)
    filenames.sort()
    files = []
    for filename in filenames:
        name = filename[:-3]
        try:
            _normalise_plugin_name(name)
        except ValueError as error:
            _append_error(report, name, str(error))
            continue
        files.append((name, filename))
    report.files = files

    if enabled_files is None:
        requested = [item[0] for item in files]
        if len(requested) > MAX_ENABLED_PLUGINS:
            _append_error(report, "Add-ons", "Enabled add-on limit reached")
            requested = requested[:MAX_ENABLED_PLUGINS]
    else:
        requested = []
        for selected in enabled_files:
            try:
                name = _normalise_plugin_name(selected)
            except ValueError as error:
                _append_error(report, str(selected), str(error))
                continue
            if name not in requested:
                if len(requested) >= MAX_ENABLED_PLUGINS:
                    _append_error(report, name, "Enabled add-on limit reached")
                    continue
                requested.append(name)
    _largest_sources_first(requested, func_dir, files)
    states = {}
    stack = []

    def load_one(name):
        state = states.get(name)
        if state == "loaded":
            return True
        if state == "failed":
            return False
        if state == "loading":
            try:
                start = stack.index(name)
                cycle = stack[start:] + [name]
            except ValueError:
                cycle = [name, name]
            _append_error(report, name, "Dependency cycle: " + " -> ".join(cycle))
            return False
        path = _file_path(func_dir, files, name)
        if path is None:
            _append_error(report, name, "Dependency file was not found")
            states[name] = "failed"
            return False
        if len(stack) >= MAX_PLUGIN_DEPENDENCY_DEPTH:
            _append_error(report, name, "Dependency depth limit reached")
            states[name] = "failed"
            return False
        if len(report.loaded) >= MAX_ENABLED_PLUGINS:
            _append_error(report, name, "Loaded add-on limit reached")
            states[name] = "failed"
            return False

        states[name] = "loading"
        stack.append(name)
        commit_state = None
        committed = False
        namespace = None
        direct_names = None
        direct_revision = 0
        direct_symbols = None
        try:
            if (canonical_directory
                    and name in ("basic", "solve", "trig")):
                # These implementations are already compiled into the release
                # and resident after boot.  Executing their SD registration
                # shims again still invoked the runtime compiler; a measured
                # 575-byte contiguous request failed after maximum Calculator
                # and Stopwatch state fragmented the heap.
                from calc.bundled_plugins import register_bundled
                dependencies = ()
                if in_place:
                    if name == "basic":
                        candidate_names = ("%",)
                    elif name == "solve":
                        candidate_names = ("solve",)
                    else:
                        candidate_names = (
                            "sinh", "cosh", "tanh", "sind", "cosd", "tand",
                            "PI")
                    for function_name in candidate_names:
                        if function_name in registry:
                            raise ValueError(
                                "Function already registered: "
                                + function_name)
                    direct_names = candidate_names
                    direct_revision = registry._revision
                    direct_symbols = registry._symbolic_names
                    if not register_bundled(name, registry):
                        raise ValueError("Bundled add-on is unavailable")
                    registry._plugin_exports[name] = {}
                    loaded_count = len(direct_names)
                else:
                    dependency_exports = {}
                    staging = FunctionRegistry(
                        max_functions=MAX_PLUGIN_FUNCTIONS)
                    staging.angle_mode = registry.angle_mode
                    staging.set_plugin_dependencies(dependency_exports)
                    if not register_bundled(name, staging):
                        raise ValueError("Bundled add-on is unavailable")
                    exports = {}
            else:
                namespace = _execute_plugin(path, "scicalc_plugin_" + name)
                dependencies = _dependency_names(namespace, path)
                if dependencies:
                    report.dependencies[name] = dependencies

                failed_dependency = None
                for dependency in dependencies:
                    if not load_one(dependency):
                        failed_dependency = dependency
                        break
                if failed_dependency is not None:
                    _append_error(report, name,
                                  "Dependency failed: " + failed_dependency)
                    states[name] = "failed"
                    return False

                dependency_exports = {}
                for dependency in dependencies:
                    dependency_exports[dependency] = registry.plugin(dependency)
                staging, exports = _register_namespace(
                    namespace, path, registry.angle_mode, dependency_exports)
            if direct_names is None:
                commit_state = registry._plugin_commit_state()
                registry.commit_plugin(
                    name, staging, exports, in_place=in_place)
                committed = True
                loaded_count = len(staging)
            report.loaded.append((name, loaded_count, ""))
            if name not in requested:
                report.auto_enabled.append(name)
            if namespace is not None:
                _discard_loader_metadata(namespace)
            states[name] = "loaded"
            return True
        except MemoryError:
            # OOM is not a broken add-on.  It must reach the one runtime
            # recovery seam instead of allocating report/error text here.
            if committed:
                registry._restore_plugin_commit_state(commit_state)
            raise
        except Exception as error:
            if committed:
                registry._restore_plugin_commit_state(commit_state)
            _append_error(report, name, str(error))
            states[name] = "failed"
            return False
        finally:
            if states.get(name) != "loaded":
                if namespace is not None:
                    namespace.clear()
                if direct_names is not None:
                    for function_name in direct_names:
                        if function_name in registry._defs:
                            del registry._defs[function_name]
                    registry._plugin_exports.pop(name, None)
                    registry._revision = direct_revision
                    registry._symbolic_names = direct_symbols
            if stack and stack[-1] == name:
                stack.pop()
            elif name in stack:
                stack.remove(name)

    for name in requested:
        load_one(name)
    return report


def list_function_files(func_dir=None):
    if func_dir is None:
        func_dir = _default_func_dir()
    try:
        files = _plugin_files(func_dir)
        return [(name[:-3], name) for name in sorted(files)]
    except OSError:
        return []
