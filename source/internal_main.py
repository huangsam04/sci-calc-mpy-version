"""Internal Flash main.py launcher.  The full application lives on SD."""
import os


try:
    os.stat("/sd/main.py")
    execfile("/sd/main.py")  # MicroPython built-in; avoids importing main twice.
except Exception as error:
    print("SCI-CALC recovery: " + str(error))
    try:
        # A damaged SD module must not shadow the internal recovery package.
        sys = __import__("sys")
        while "/sd" in sys.path:
            sys.path.remove("/sd")
        if "/" not in sys.path:
            sys.path.insert(0, "/")
        for module_name in ("recovery", "display.ssd1322", "display.mono_palette", "display"):
            if module_name in sys.modules:
                del sys.modules[module_name]
        try:
            sys.print_exception(error)
        except Exception:
            pass
        from recovery import show_recovery
        show_recovery(error)
    except Exception as recovery_error:
        print("Recovery display failed: " + str(recovery_error))
