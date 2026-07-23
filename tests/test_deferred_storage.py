from utils.storage import DeferredStorage


def test_deferred_storage_coalesces_settings_until_idle_flush():
    writes = []
    storage = DeferredStorage(
        settings_writer=lambda value: writes.append(value) or True,
        vars_writer=lambda value: True)

    storage.request_settings({"brightness": 80})
    storage.request_settings({"brightness": 90})

    assert writes == []
    assert storage.flush(100) == ("settings", True)
    assert writes == [{"brightness": 90}]


def test_deferred_storage_retries_failed_atomic_write_without_dropping_data():
    outcomes = []
    attempts = []

    def write_vars(value):
        attempts.append(dict(value))
        return len(attempts) == 2

    storage = DeferredStorage(
        settings_writer=lambda value: True,
        vars_writer=write_vars,
        retry_ms=200)
    storage.request_vars({"x": 7}, outcomes.append)

    assert storage.flush(1_000) == ("vars", False)
    assert storage.flush(1_100) is None
    assert storage.flush(1_200) == ("vars", True)
    assert attempts == [{"x": 7}, {"x": 7}]
    assert outcomes == [False, True]


def test_deferred_storage_defers_snapshot_work_until_idle_flush():
    writes = []
    settings = {"brightness": 80}
    storage = DeferredStorage(
        settings_writer=lambda value: writes.append(dict(value)) or True,
        vars_writer=lambda value: True)

    storage.request_settings(settings)
    settings["brightness"] = 90

    assert writes == []
    assert storage.flush(100) == ("settings", True)
    assert writes == [{"brightness": 90}]
