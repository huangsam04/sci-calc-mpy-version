"""Load isolated SCI-CALC plugins from the SD card."""
import os

from calc.functions import FunctionRegistry


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


def load_function_files(registry, enabled_files=None, func_dir="/sd/functions"):
    """Register enabled plugins, isolating each file until it succeeds."""
    report = LoadReport()
    try:
        files = [name for name in os.listdir(func_dir) if name.endswith(".py")]
    except OSError:
        return report

    for filename in sorted(files):
        name = filename[:-3]
        if enabled_files is not None and name not in enabled_files:
            continue
        try:
            namespace = _execute_plugin(_join(func_dir, filename), "scicalc_plugin_" + name)
            register = namespace.get("register")
            if not callable(register):
                raise ValueError(filename + " must define register(registry)")
            staging = FunctionRegistry()
            staging.angle_mode = registry.angle_mode
            register(staging)
            registry.merge(staging)
            message = namespace.get("WELCOME", "")
            report.loaded.append((name, len(staging), message))
        except Exception as error:
            report.errors.append((name, str(error)))
            print("Plugin error " + filename + ": " + str(error))
    return report


def list_function_files(func_dir="/sd/functions"):
    try:
        files = [name for name in os.listdir(func_dir) if name.endswith(".py")]
        return [(name[:-3], name) for name in sorted(files)]
    except OSError:
        return []
