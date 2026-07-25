"""Load isolated SCI-CALC add-ons and their declared dependencies."""
import os

from calc.functions import FunctionRegistry


def _plugin_files(directory):
    return [name for name in os.listdir(directory)
            if name.endswith(".py") and not name.startswith("_")]


class LoadReport:
    """Outcome of one add-on load pass.

    ``auto_enabled`` contains dependencies that were loaded even though they
    were absent from the selected setting.  The function panel uses the same
    dependency metadata to make that automatic choice visible and persistent.
    """

    def __init__(self):
        self.loaded = []
        self.errors = []
        self.auto_enabled = []
        self.dependencies = {}
        self.functions = {}


def _join(directory, filename):
    if directory.endswith("/") or directory.endswith("\\"):
        return directory + filename
    separator = "\\" if "\\" in directory and "/" not in directory else "/"
    return directory + separator + filename


def _execute_plugin(path, module_name):
    namespace = {"__name__": module_name, "__file__": path}
    with open(path, "r") as source_file:
        source = source_file.read()
    exec(compile(source, path, "exec"), namespace)
    return namespace


def _normalise_plugin_name(name):
    if not isinstance(name, str) or not name:
        raise ValueError("Plugin dependency names must be non-empty strings")
    return name[7:] if name.startswith("plugin:") else name


def _dependency_names(namespace, path):
    """Read a plugin's dependency declaration without touching live state."""
    dependencies = namespace.get("DEPENDENCIES")
    legacy_dependencies = namespace.get("REQUIRES")
    if dependencies is not None and legacy_dependencies is not None:
        if dependencies != legacy_dependencies:
            raise ValueError(path + " has conflicting DEPENDENCIES and REQUIRES")
    if dependencies is None:
        dependencies = legacy_dependencies
    if dependencies is None:
        return ()
    if isinstance(dependencies, str):
        dependencies = (dependencies,)
    if not isinstance(dependencies, (tuple, list)):
        raise ValueError(path + " DEPENDENCIES must be a list or tuple")

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
    for name in exports:
        if not isinstance(name, str) or not name:
            raise ValueError(path + " EXPORTS keys must be non-empty strings")
    return dict(exports)


def _register_namespace(namespace, path, angle_mode=0, dependency_exports=None):
    register = namespace.get("register")
    if not callable(register):
        raise ValueError(path + " must define register(registry)")
    staging = FunctionRegistry()
    staging.angle_mode = angle_mode
    staging.set_plugin_dependencies(dependency_exports or {})
    register(staging)
    return staging, _validate_exports(namespace, path)


def _build_staging_registry(path, module_name, angle_mode=0):
    """Compatibility helper for single-plugin inspection callers."""
    namespace = _execute_plugin(path, module_name)
    _dependency_names(namespace, path)
    staging, _ = _register_namespace(namespace, path, angle_mode)
    return staging, namespace


def _append_error(report, name, detail):
    for existing_name, _ in report.errors:
        if existing_name == name:
            return
    report.errors.append((name, detail))
    print("Plugin error " + name + ": " + detail)


def _default_func_dir():
    from approot import app_root
    return app_root() + "/functions"


def load_function_files(registry, enabled_files=None, func_dir=None):
    """Register selected add-ons and recursively load their dependencies.

    Add-ons declare dependencies with ``DEPENDENCIES = ("other",)``.  A
    dependent can read the other add-on's explicit ``EXPORTS`` through
    ``context.plugin("other")`` at evaluation time, or ``registry.plugin``
    while it is registering.  Each add-on is still staged independently, so a
    failure never partially modifies the live registry.
    """
    if func_dir is None:
        func_dir = _default_func_dir()
    report = LoadReport()
    try:
        filenames = _plugin_files(func_dir)
    except OSError:
        return report

    paths = {}
    for filename in filenames:
        paths[filename[:-3]] = _join(func_dir, filename)

    if enabled_files is None:
        requested = sorted(paths)
    else:
        requested = []
        for selected in enabled_files:
            try:
                name = _normalise_plugin_name(selected)
            except ValueError as error:
                _append_error(report, str(selected), str(error))
                continue
            if name not in requested:
                requested.append(name)
    requested_set = set(requested)
    namespaces = {}
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
        if name not in paths:
            _append_error(report, name, "Dependency file was not found")
            states[name] = "failed"
            return False

        states[name] = "loading"
        stack.append(name)
        try:
            namespace = namespaces.get(name)
            if namespace is None:
                namespace = _execute_plugin(paths[name], "scicalc_plugin_" + name)
                namespaces[name] = namespace
            dependencies = _dependency_names(namespace, paths[name])
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
                namespace, paths[name], registry.angle_mode, dependency_exports)
            registry.merge(staging)
            registry.register_plugin(name, exports)
            function_names = []
            for function_name in staging.keys():
                function_names.append(function_name)
            report.functions[name] = function_names
            message = namespace.get("WELCOME", "")
            report.loaded.append((name, len(staging), message))
            if name not in requested_set:
                report.auto_enabled.append(name)
            states[name] = "loaded"
            return True
        except Exception as error:
            _append_error(report, name, str(error))
            states[name] = "failed"
            return False
        finally:
            if stack and stack[-1] == name:
                stack.pop()
            elif name in stack:
                stack.remove(name)

    for name in requested:
        load_one(name)
    return report


def describe_function_files(func_dir=None):
    """Return display names for each discoverable add-on's registered functions."""
    if func_dir is None:
        func_dir = _default_func_dir()
    descriptions = {}
    files = list_function_files(func_dir)
    for name, _ in files:
        registry = FunctionRegistry()
        report = load_function_files(registry, [name], func_dir)
        descriptions[name] = report.functions.get(name, [])
    return descriptions


def describe_plugin_dependencies(func_dir=None, files=None):
    """Inspect dependency metadata without registering any callbacks live."""
    if func_dir is None:
        func_dir = _default_func_dir()
    descriptions = {}
    if files is None:
        files = list_function_files(func_dir)
    for name, filename in files:
        path = _join(func_dir, filename)
        try:
            namespace = _execute_plugin(path, "scicalc_plugin_preview_" + name)
            descriptions[name] = _dependency_names(namespace, path)
        except Exception:
            descriptions[name] = ()
    return descriptions


def list_function_files(func_dir=None):
    if func_dir is None:
        func_dir = _default_func_dir()
    try:
        files = _plugin_files(func_dir)
        return [(name[:-3], name) for name in sorted(files)]
    except OSError:
        return []
