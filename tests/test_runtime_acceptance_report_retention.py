from runtime_acceptance import (
    ERROR_EXCEPTION,
    ERROR_MEMORY,
    RUN_BOUNDED,
    STEP_DONE,
    RuntimeHandle,
    run,
)


class _Nav:
    def __init__(self, root):
        self.current = root

    def reset(self, root):
        self.current = root

    def present_current(self):
        pass


class _FailingSession:
    capabilities = ("capability",)

    def __init__(self, step_error, close_error=None):
        self._step_error = step_error
        self._close_error = close_error
        self.completed_capability = None
        self.completed_count = 0

    def open(self, _runtime):
        pass

    def step(self, _round_index, _capability_index):
        if self._step_error is not None:
            raise self._step_error
        self.completed_capability = self.capabilities[0]
        self.completed_count += 1
        return STEP_DONE

    def close(self):
        if self._close_error is not None:
            raise self._close_error


def _report(mode, step_error, close_error=None):
    root = object()
    runtime = RuntimeHandle(_Nav(root), root, (), mode=mode)
    rounds = 1 if mode == "in_memory" else 5
    session = _FailingSession(step_error, close_error)
    return run(
        runtime,
        ("retention", rounds, (("capability", RUN_BOUNDED, session),)),
    )


def test_resident_report_hands_off_primary_oom_once_without_secondary_object():
    primary = MemoryError("primary OOM")
    secondary = RuntimeError("close failed")

    report = _report("resident", primary, secondary)

    assert report.primary_error_code == ERROR_MEMORY
    assert report.secondary_error_code == ERROR_EXCEPTION
    assert report._secondary_error is None
    assert primary.__traceback__ is None
    assert report.primary_error is primary
    assert report.primary_error is None
    assert report._primary_error is None


def test_release_report_promotes_cleanup_oom_with_scalar_primary_history():
    step_error = RuntimeError("step failed")
    cleanup = MemoryError("cleanup OOM")

    report = _report("release", step_error, cleanup)

    assert report.primary_error_code == ERROR_MEMORY
    assert report.secondary_error_code == ERROR_EXCEPTION
    assert report._secondary_error is None
    assert cleanup.__traceback__ is None
    assert report.primary_error is cleanup
    assert report.primary_error is None


def test_in_memory_report_keeps_legacy_primary_and_secondary_identity():
    primary = MemoryError("primary OOM")
    secondary = RuntimeError("close failed")

    report = _report("in_memory", primary, secondary)

    assert report.primary_error is primary
    assert report.secondary_error is secondary
