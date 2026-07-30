# Stable boot supervisor: selector-driven slot selection, one-shot trial
# consumption, strict sys.path and single-display recovery handoff.
# Runs on MicroPython (device boot chain) and CPython (host tests), so it
# stays free of dataclasses, typing and f-strings.
#
# The environment adapter supplies every side effect (selector store, slot
# probe, sys.path, module purge, GC, exec and recovery), which keeps this
# module testable on the host and thin on the device.
# Codec imports stay lazy so the boot chain can be fully released before
# the slot application starts; see internal_main.py.

ACTION_SLOT = "slot"
ACTION_RECOVERY = "recovery"

_RECOVERY_SYS_PATH = ("/lib", "/")
_LAUNCH_NAME = "launch.py"


class BootPlan:
    def __init__(self, action, slot_ref=None, consume=None,
                 selection_generation=None, reason=""):
        self.action = action
        self.slot_ref = slot_ref
        self.consume = consume
        self.selection_generation = selection_generation
        self.reason = reason


def _consume_marker(selector):
    import bootsel
    return bootsel.SelectorData(
        0,
        selector.confirmed,
        selector.trial,
        selector.trial_generation,
        True,
        selector.retired,
        selector.confirmation_pending)


def decide(selector, slot_exists):
    if selector is None:
        return BootPlan(ACTION_RECOVERY, reason="selector-unavailable")

    trial = selector.trial
    if trial is not None and not selector.trial_consumed:
        consume = _consume_marker(selector)
        if slot_exists(trial.name):
            return BootPlan(
                ACTION_SLOT,
                slot_ref=trial,
                consume=consume,
                selection_generation=selector.trial_generation)
        confirmed = selector.confirmed
        if confirmed is not None and slot_exists(confirmed.name):
            return BootPlan(
                ACTION_SLOT,
                slot_ref=confirmed,
                consume=consume,
                reason="trial-slot-missing")
        return BootPlan(
            ACTION_RECOVERY, consume=consume, reason="no-bootable-slot")

    confirmed = selector.confirmed
    if confirmed is None:
        return BootPlan(ACTION_RECOVERY, reason="no-confirmed-release")
    if not slot_exists(confirmed.name):
        return BootPlan(
            ACTION_RECOVERY, reason="confirmed-slot-missing")
    return BootPlan(ACTION_SLOT, slot_ref=confirmed)


def _recover(environment, error):
    environment.set_sys_path(_RECOVERY_SYS_PATH)
    environment.purge_slot_modules()
    environment.collect_garbage()
    environment.show_recovery(error)


def prepare(environment):
    """Decide the boot target and return (plan, exec path) or raise."""
    import bootlog
    selector = environment.read_selector()
    plan = decide(selector, environment.slot_exists)

    selector_generation = 0 if selector is None else selector.generation
    if plan.consume is not None:
        stored = environment.write_selector(plan.consume)
        selector_generation = stored.generation

    # Boot evidence is telemetry: it must never block an otherwise
    # bootable release. A missing record simply fails the host smoke.
    entry = bootlog.BootEntry(
        0,
        selector_generation,
        plan.selection_generation,
        plan.slot_ref if plan.action == ACTION_SLOT else None)
    try:
        environment.write_boot_record(entry)
    except Exception:
        pass

    if plan.action != ACTION_SLOT:
        raise RuntimeError(plan.reason)

    slot_root = environment.slot_root(plan.slot_ref.name)
    environment.set_sys_path((".frozen", slot_root, "/lib"))
    environment.purge_slot_modules()
    return plan, slot_root + "/" + _LAUNCH_NAME


def supervise(environment):
    try:
        plan, target = prepare(environment)
    except Exception as error:
        _recover(environment, error)
        return None
    try:
        environment.exec_file(target)
    except Exception as error:
        _recover(environment, error)
    return plan
