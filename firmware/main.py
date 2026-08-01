"""Frozen SCI-CALC bootstrap executed by MicroPython's main.py hook."""
import sys


sys.path = [".frozen", "/lib"]

try:
    from application import main
    main()
except Exception as error:
    print("SCI-CALC startup failed: " + str(error))
    try:
        import gc
        gc.collect()
        from recovery import show_recovery
        show_recovery(error)
    except Exception as recovery_error:
        print("Recovery display failed: " + str(recovery_error))
