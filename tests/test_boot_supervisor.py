# Host behaviour tests for the stable boot supervisor decision module.
# The module (source/bootsupervisor.py) must also compile for MicroPython.
import bootsel
import bootsupervisor


def _ref(name, release_id, sha_byte):
    return bootsel.SlotEntry(name, release_id, bytes([sha_byte]) * 32)


def _selector(generation=1, confirmed=None, trial=None, trial_generation=0,
              trial_consumed=False, retired=(), confirmation_pending=False):
    return bootsel.SelectorData(
        generation,
        confirmed,
        trial,
        trial_generation,
        trial_consumed,
        tuple(retired),
        confirmation_pending,
    )


CONFIRMED = _ref("A", "app:1.3.0:source", 0x11)
TRIAL = _ref("B", "app:1.4.0:source", 0x22)


def _decide(selector, existing=("A", "B")):
    return bootsupervisor.decide(selector, lambda name: name in existing)


class _Environment:
    def __init__(self, selector, existing=("A", "B"), exec_error=None,
                 store_error=None, boot_record_error=None):
        self._selector = selector
        self._existing = existing
        self._exec_error = exec_error
        self._store_error = store_error
        self._boot_record_error = boot_record_error
        self.calls = []
        self.written = []
        self.boot_records = []

    def read_selector(self):
        self.calls.append("read_selector")
        return self._selector

    def write_selector(self, selector):
        self.calls.append("write_selector")
        if self._store_error is not None:
            raise self._store_error
        self.written.append(selector)
        return bootsel.SelectorData(
            42,
            selector.confirmed,
            selector.trial,
            selector.trial_generation,
            selector.trial_consumed,
            selector.retired,
            selector.confirmation_pending,
        )

    def write_boot_record(self, entry):
        self.calls.append(("boot_record", entry))
        if self._boot_record_error is not None:
            raise self._boot_record_error
        self.boot_records.append(entry)

    def slot_exists(self, name):
        return name in self._existing

    def slot_root(self, name):
        return "/sd/.slots/" + name

    def set_sys_path(self, entries):
        self.calls.append(("sys_path", tuple(entries)))

    def purge_slot_modules(self):
        self.calls.append("purge")

    def collect_garbage(self):
        self.calls.append("gc")

    def exec_file(self, path):
        self.calls.append(("exec", path))
        if self._exec_error is not None:
            raise self._exec_error

    def show_recovery(self, error):
        self.calls.append(("recovery", str(error)))


def test_no_selector_boots_recovery():
    plan = _decide(None)
    assert plan.action == bootsupervisor.ACTION_RECOVERY
    assert plan.slot_ref is None
    assert plan.consume is None


def test_confirmed_only_boots_the_confirmed_slot():
    plan = _decide(_selector(confirmed=CONFIRMED))
    assert plan.action == bootsupervisor.ACTION_SLOT
    assert plan.slot_ref == CONFIRMED
    assert plan.selection_generation is None
    assert plan.consume is None


def test_unconsumed_trial_boots_the_trial_and_consumes_it():
    selector = _selector(
        generation=3, confirmed=CONFIRMED, trial=TRIAL, trial_generation=3)
    plan = _decide(selector)
    assert plan.action == bootsupervisor.ACTION_SLOT
    assert plan.slot_ref == TRIAL
    assert plan.selection_generation == 3
    assert plan.consume is not None
    assert plan.consume.trial_consumed is True
    assert plan.consume.trial == TRIAL
    assert plan.consume.trial_generation == 3
    assert plan.consume.confirmed == CONFIRMED
    assert plan.consume.generation == 0


def test_consumed_trial_boots_the_confirmed_slot():
    plan = _decide(_selector(
        generation=4, confirmed=CONFIRMED, trial=TRIAL,
        trial_generation=3, trial_consumed=True))
    assert plan.action == bootsupervisor.ACTION_SLOT
    assert plan.slot_ref == CONFIRMED
    assert plan.consume is None


def test_pending_confirmation_still_boots_the_confirmed_slot():
    plan = _decide(_selector(
        confirmed=TRIAL, retired=(CONFIRMED,), confirmation_pending=True))
    assert plan.action == bootsupervisor.ACTION_SLOT
    assert plan.slot_ref == TRIAL
    assert plan.consume is None


def test_missing_trial_slot_falls_back_to_confirmed_and_still_consumes():
    selector = _selector(
        generation=3, confirmed=CONFIRMED, trial=TRIAL, trial_generation=3)
    plan = _decide(selector, existing=("A",))
    assert plan.action == bootsupervisor.ACTION_SLOT
    assert plan.slot_ref == CONFIRMED
    assert plan.selection_generation is None
    assert plan.consume is not None
    assert plan.consume.trial_consumed is True


def test_missing_trial_and_confirmed_slots_boot_recovery_with_consume():
    selector = _selector(
        generation=3, confirmed=CONFIRMED, trial=TRIAL, trial_generation=3)
    plan = _decide(selector, existing=())
    assert plan.action == bootsupervisor.ACTION_RECOVERY
    assert plan.consume is not None
    assert plan.consume.trial_consumed is True


def test_missing_confirmed_slot_boots_recovery():
    plan = _decide(_selector(confirmed=CONFIRMED), existing=())
    assert plan.action == bootsupervisor.ACTION_RECOVERY
    assert plan.consume is None


def test_supervise_consumes_the_trial_before_executing_the_slot():
    env = _Environment(_selector(
        generation=3, confirmed=CONFIRMED, trial=TRIAL, trial_generation=3))

    bootsupervisor.supervise(env)

    assert env.calls[0] == "read_selector"
    assert env.calls[1] == "write_selector"
    assert env.written[0].trial_consumed is True
    assert ("sys_path", ("/sd/.slots/B", ".frozen", "/lib")) in env.calls
    assert ("exec", "/sd/.slots/B/launch.py") in env.calls
    assert env.calls.index("write_selector") < env.calls.index(
        ("exec", "/sd/.slots/B/launch.py"))


def test_supervise_records_the_trial_boot_before_executing():
    env = _Environment(_selector(
        generation=3, confirmed=CONFIRMED, trial=TRIAL, trial_generation=3))

    bootsupervisor.supervise(env)

    assert len(env.boot_records) == 1
    entry = env.boot_records[0]
    assert entry.generation == 0
    assert entry.selector_generation == 42
    assert entry.selection_generation == 3
    assert entry.selected == TRIAL
    exec_index = env.calls.index(("exec", "/sd/.slots/B/launch.py"))
    record_index = next(
        index for index, call in enumerate(env.calls)
        if isinstance(call, tuple) and call[0] == "boot_record")
    assert env.calls.index("write_selector") < record_index < exec_index


def test_supervise_records_a_confirmed_boot_without_a_selection():
    env = _Environment(_selector(generation=5, confirmed=CONFIRMED))

    bootsupervisor.supervise(env)

    assert len(env.boot_records) == 1
    entry = env.boot_records[0]
    assert entry.selector_generation == 5
    assert entry.selection_generation is None
    assert entry.selected == CONFIRMED


def test_supervise_records_a_recovery_boot_with_no_selection():
    env = _Environment(None)

    bootsupervisor.supervise(env)

    assert len(env.boot_records) == 1
    entry = env.boot_records[0]
    assert entry.selector_generation == 0
    assert entry.selection_generation is None
    assert entry.selected is None


def test_boot_record_failure_never_blocks_a_boot():
    env = _Environment(
        _selector(confirmed=CONFIRMED),
        boot_record_error=OSError("evidence write failed"))

    bootsupervisor.supervise(env)

    assert ("exec", "/sd/.slots/A/launch.py") in env.calls
    assert not env.boot_records


def test_supervise_exec_failure_purges_before_recovery():
    error = MemoryError("slot exhausted")
    env = _Environment(_selector(confirmed=CONFIRMED), exec_error=error)

    bootsupervisor.supervise(env)

    assert ("sys_path", ("/sd/.slots/A", ".frozen", "/lib")) in env.calls
    recovery_index = next(
        index for index, call in enumerate(env.calls)
        if isinstance(call, tuple) and call[0] == "recovery")
    assert "purge" in env.calls[:recovery_index]
    assert "gc" in env.calls[:recovery_index]
    assert env.calls.index("purge") < env.calls.index("gc")
    assert ("sys_path", ("/lib", "/")) in env.calls
    assert env.calls[recovery_index] == ("recovery", str(error))


def test_supervise_recovers_when_nothing_is_bootable():
    env = _Environment(None)

    bootsupervisor.supervise(env)

    assert not env.written
    assert any(
        isinstance(call, tuple) and call[0] == "recovery"
        for call in env.calls)


def test_supervise_recovers_when_the_consume_write_fails():
    env = _Environment(
        _selector(
            generation=3, confirmed=CONFIRMED, trial=TRIAL,
            trial_generation=3),
        store_error=OSError("flash write failed"))

    bootsupervisor.supervise(env)

    assert not any(
        isinstance(call, tuple) and call[0] == "exec"
        for call in env.calls)
    assert any(
        isinstance(call, tuple) and call[0] == "recovery"
        for call in env.calls)


def test_supervise_confirmed_boot_does_not_touch_the_selector():
    env = _Environment(_selector(confirmed=CONFIRMED))

    bootsupervisor.supervise(env)

    assert not env.written
    assert ("exec", "/sd/.slots/A/launch.py") in env.calls
