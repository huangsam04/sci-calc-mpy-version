# ponytail: import .py files from /sd/functions/, merge flist() results
"""Function file loader for user-defined calculator functions.

Scans /sd/functions/ for .py files, imports them, calls flist() on each,
and merges into the function table.
"""
import sys
import os


def load_function_files(func_table, enabled_files=None):
    """Load function definitions from SD card.

    Args:
        func_table: dict to merge functions into (modified in place)
        enabled_files: list of filenames (without .py) to load, or None for all

    Returns:
        list of (filename, flist_result, welcome_msg) for loaded files
    """
    loaded = []
    func_dir = "/sd/functions"

    try:
        files = [f for f in os.listdir(func_dir) if f.endswith('.py')]
    except OSError:
        return loaded  # No functions directory

    for filename in sorted(files):
        name = filename[:-3]  # strip .py

        if enabled_files is not None and name not in enabled_files:
            continue

        try:
            # Clear cached import if re-loading
            mod_name = "functions." + name
            if mod_name in sys.modules:
                del sys.modules[mod_name]

            # Import the module
            path = func_dir + "/" + filename
            mod = _import_file(name, path)

            if not hasattr(mod, 'flist'):
                print(f"Warning: {filename} has no flist() function, skipping")
                continue

            # Get function list
            func_defs = mod.flist()

            # Call welcome if available
            welcome_msg = None
            if hasattr(mod, 'welcome'):
                welcome_msg = mod.welcome()

            # Check for conflicts
            for defn in func_defs:
                def_name = defn[0]
                if def_name in func_table:
                    print(f"Warning: function '{def_name}' from {filename} "
                          f"overrides existing function")

            # Merge into table
            from calc.functions import merge_functions
            merge_functions(func_table, func_defs)

            loaded.append((name, func_defs, welcome_msg))
            print(f"Loaded: {filename} ({len(func_defs)} functions)")

        except Exception as e:
            print(f"Error loading {filename}: {e}")

    return loaded


def _import_file(name, path):
    """Import a Python file by path."""
    # ponytail: importlib not available in MicroPython, use built-in __import__
    # Read and compile
    with open(path, 'r') as f:
        source = f.read()
    code = compile(source, path, 'exec')
    mod = type(sys)(name)
    mod.__file__ = path
    exec(code, mod.__dict__)
    return mod


def list_function_files():
    """Return list of (filename, display_name) in the functions directory."""
    func_dir = "/sd/functions"
    try:
        files = [f for f in os.listdir(func_dir) if f.endswith('.py')]
        return [(f[:-3], f) for f in sorted(files)]
    except OSError:
        return []
