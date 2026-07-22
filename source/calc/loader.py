"""Load isolated SCI-CALC plugins from the SD card."""
import os

from calc.functions import FunctionRegistry


def _plugin_files(directory):
    return [name for name in os.listdir(directory)
            if name.endswith(".py") and not name.startswith("_")]


class LoadReport:
    def __init__(self):
        self.loaded = []
        self.errors = []


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


def _build_staging_registry(path, module_name, angle_mode=0):
    """Run one plugin against an isolated registry and return its namespace."""
    namespace = _execute_plugin(path, module_name)
    register = namespace.get("register")
    if not callable(register):
        raise ValueError(path + " must define register(registry)")
    staging = FunctionRegistry()
    staging.angle_mode = angle_mode
    register(staging)
    return staging, namespace


def load_function_files(registry, enabled_files=None, func_dir="/sd/functions"):
    """Register enabled plugins, isolating each file until it succeeds."""
    report = LoadReport()
    try:
        files = _plugin_files(func_dir)
    except OSError:
        return report

    for filename in sorted(files):
        name = filename[:-3]
        if enabled_files is not None and name not in enabled_files:
            continue
        try:
            staging, namespace = _build_staging_registry(
                _join(func_dir, filename), "scicalc_plugin_" + name,
                registry.angle_mode)
            registry.merge(staging)
            message = namespace.get("WELCOME", "")
            report.loaded.append((name, len(staging), message))
        except Exception as error:
            report.errors.append((name, str(error)))
            print("Plugin error " + filename + ": " + str(error))
    return report


def describe_function_files(func_dir="/sd/functions"):
    """Return registered display names for each discoverable plugin file.

    Plugins run in isolated registries, so inspection cannot add callbacks to
    the live calculator registry. A broken plugin simply has no description;
    normal loading will still surface its detailed error to the user.
    """
    descriptions = {}
    try:
        files = _plugin_files(func_dir)
    except OSError:
        return descriptions

    for filename in sorted(files):
        name = filename[:-3]
        try:
            staging, _ = _build_staging_registry(
                _join(func_dir, filename), "scicalc_plugin_preview_" + name)
            function_names = []
            for function_name in staging.keys():
                function_names.append(function_name)
            descriptions[name] = function_names
        except Exception:
            descriptions[name] = []
    return descriptions


def list_function_files(func_dir="/sd/functions"):
    try:
        files = _plugin_files(func_dir)
        return [(name[:-3], name) for name in sorted(files)]
    except OSError:
        return []
