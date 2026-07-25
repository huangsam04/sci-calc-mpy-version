# Internal Flash main.py: hand control to the stable boot supervisor.
# The supervisor reads the release selector, boots the chosen slot with a
# strict sys.path, and owns the recovery handoff. This shim only covers a
# failure of the supervisor itself.
try:
    import bootenv
    import bootsupervisor

    environment = bootenv.environment()
    try:
        plan, target = bootsupervisor.prepare(environment)
    except Exception as prepare_error:
        environment.recover(prepare_error)
    else:
        # The boot chain must not stay resident while the slot application
        # runs: dropping these modules returns their heap to the app.
        # They are re-imported from internal flash on the next cold boot.
        import gc
        import sys

        for module_name in ("bootsupervisor", "bootsel", "bootlog"):
            sys.modules.pop(module_name, None)
        bootsupervisor = None
        plan = None
        gc.collect()
        try:
            environment.exec_file(target)
        except Exception as exec_error:
            environment.recover(exec_error)
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
