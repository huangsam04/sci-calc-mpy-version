# Internal Flash main.py: hand control to the stable boot supervisor.
# The supervisor reads the release selector, boots the chosen slot with a
# strict sys.path, and owns the recovery handoff. This shim only covers a
# failure of the supervisor itself.
try:
    import bootenv
    import bootsupervisor

    bootsupervisor.supervise(bootenv.environment())
except Exception as error:
    print("SCI-CALC boot supervisor failed: " + str(error))
    try:
        import gc
        import sys

        sys.path = ["/lib", "/"]
        try:
            bootenv.purge_slot_modules()
        except Exception:
            pass
        gc.collect()
        from recovery import show_recovery
        show_recovery(error)
    except Exception as recovery_error:
        print("Recovery display failed: " + str(recovery_error))
