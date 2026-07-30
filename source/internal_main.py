# Internal Flash main.py: hand control to the stable boot supervisor.
# The supervisor reads the release selector, boots the chosen slot with a
# strict sys.path, and owns the recovery handoff. This shim only covers a
# failure of the supervisor itself.
#
# During first takeover the pinned 1.3.0 boot.py may run once before the new
# boot.py is committed. It puts /sd ahead of the internal root, so remove that
# untrusted import path before importing any new boot-chain module.
try:
    import sys

    sys.path = ["/lib", "/"]
    # A legacy boot may have put /sd on sys.path before it imported this
    # replacement main.py.  Do not let a cached card module bypass the
    # narrowed path for the trusted boot chain.
    for module_name in (
            "bootenv", "bootlog", "bootsel", "bootsupervisor", "recovery"):
        sys.modules.pop(module_name, None)
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

        for module_name in (
                "bootenv", "bootsupervisor", "bootsel", "bootlog"):
            sys.modules.pop(module_name, None)
        bootsupervisor = None
        bootenv = None
        environment = None
        plan = None
        gc.collect()
        try:
            execfile(target)
        except Exception as exec_error:
            # Rebuild the trusted recovery adapter only after the slot app has
            # unwound.  Keeping it alive during main() costs the final heap run
            # needed by the resident page graph on the constrained target.
            sys.path = ["/lib", "/"]
            sys.modules.pop("bootenv", None)
            import bootenv
            bootenv.environment().recover(exec_error)
except Exception as error:
    print("SCI-CALC boot supervisor failed: " + str(error))
    try:
        import gc
        import sys

        sys.path = ["/lib", "/"]
        try:
            if bootenv is None:
                import bootenv
            bootenv.purge_slot_modules()
        except Exception:
            pass
        gc.collect()
        from recovery import show_recovery
        show_recovery(error)
    except Exception as recovery_error:
        print("Recovery display failed: " + str(recovery_error))
