import json

import pytest

from utils import storage
from calc.number import Number
from calc.limits import (MAX_ENABLED_PLUGINS, MAX_VARIABLES,
                         MAX_VARIABLES_FILE_BYTES)


def test_settings_are_merged_with_defaults_and_recovered_from_backup(tmp_path):
    storage.configure_storage(str(tmp_path))
    assert storage.save_settings({"angle_mode": 1}) is True
    assert storage.save_settings({"angle_mode": 0, "version": "next"}) is True
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")

    storage.configure_storage(str(tmp_path))
    settings = storage.load_settings()

    assert settings["angle_mode"] == 1
    assert settings["enabled_functions"] == ["basic", "trig", "math", "list"]
    assert settings["sleep_timeout_s"] == 180
    assert settings["brightness"] == 100
    assert "version" not in settings


def test_variable_write_failure_keeps_in_memory_cache(tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    storage.save_vars({"x": 1})
    monkeypatch.setattr(storage, "_atomic_write_vars", lambda path, data: False)

    assert storage.save_vars({"x": 2}) is False
    assert storage.load_vars() == {"x": 2}


def test_sleep_timeout_is_clamped_to_ticks_safe_range(tmp_path):
    (tmp_path / "settings.json").write_text(
        '{"sleep_timeout_s": 999999999}', encoding="utf-8")
    storage.configure_storage(str(tmp_path))

    assert storage.load_settings()["sleep_timeout_s"] == 86_400


def test_invalid_brightness_is_replaced_with_safe_default(tmp_path):
    (tmp_path / "settings.json").write_text(
        '{"brightness": 0}', encoding="utf-8")
    storage.configure_storage(str(tmp_path))

    assert storage.load_settings()["brightness"] == 100


def test_legacy_version_is_removed_and_never_saved_again(tmp_path):
    (tmp_path / "settings.json").write_text(
        '{"version": "1.1.0"}', encoding="utf-8")
    storage.configure_storage(str(tmp_path))

    settings = storage.load_settings()
    assert "version" not in settings
    assert storage.save_settings(settings) is True
    assert "version" not in json.loads(
        (tmp_path / "settings.json").read_text(encoding="utf-8"))


def test_unserializable_plugin_value_is_reported_without_losing_memory_state(tmp_path):
    storage.configure_storage(str(tmp_path))
    value = object()

    assert storage.save_vars({"plugin_value": value}) is False
    assert storage.load_vars()["plugin_value"] is value
    assert storage.last_error()


def test_failed_commit_never_replaces_good_backup_with_corrupt_primary(tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    assert storage.save_vars({"x": 1}) is True
    assert storage.save_vars({"x": 2}) is True
    (tmp_path / "vars.json").write_text("{broken", encoding="utf-8")
    real_rename = storage.os.rename

    def fail_new_primary(source, target):
        if str(source).endswith(".tmp"):
            raise OSError("simulated interrupted commit")
        return real_rename(source, target)

    monkeypatch.setattr(storage.os, "rename", fail_new_primary)
    assert storage.save_vars({"x": 3}) is False
    assert json.loads((tmp_path / "vars.json.bak").read_text(encoding="utf-8")) == {"x": 1}

    storage.configure_storage(str(tmp_path))
    assert storage.load_vars() == {"x": 1}


def test_primary_rename_move_then_error_preserves_latest_variables(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    assert storage.save_vars({"x": "older"}) is True
    assert storage.save_vars({"x": "latest"}) is True
    primary = str(tmp_path / "vars.json")
    backup = str(tmp_path / "vars.json.bak")
    real_rename = storage.os.rename

    def move_primary_then_fail(source, target):
        if str(source) == primary and str(target) == backup:
            real_rename(source, target)
            raise OSError("rename completed before injected error")
        return real_rename(source, target)

    monkeypatch.setattr(storage.os, "rename", move_primary_then_fail)

    assert storage.save_vars({"x": "next"}) is False
    assert json.loads((tmp_path / "vars.json").read_text(
        encoding="utf-8")) == {"x": "latest"}
    assert json.loads((tmp_path / "vars.json.bak").read_text(
        encoding="utf-8")) == {"x": "older"}
    storage.configure_storage(str(tmp_path))
    assert storage.load_vars() == {"x": "latest"}


def test_primary_rename_move_then_error_promotes_cleanup_oom(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    assert storage.save_vars({"x": "older"}) is True
    assert storage.save_vars({"x": "latest"}) is True
    primary = str(tmp_path / "vars.json")
    backup = str(tmp_path / "vars.json.bak")
    cleanup_error = MemoryError("injected cleanup OOM")
    real_rename = storage.os.rename
    real_remove = storage._remove_if_exists

    def move_primary_then_fail(source, target):
        if str(source) == primary and str(target) == backup:
            real_rename(source, target)
            raise OSError("rename completed before injected error")
        return real_rename(source, target)

    def cleanup_oom(path):
        if str(path).endswith(".tmp"):
            raise cleanup_error
        return real_remove(path)

    monkeypatch.setattr(storage.os, "rename", move_primary_then_fail)
    monkeypatch.setattr(storage, "_remove_if_exists", cleanup_oom)

    with pytest.raises(MemoryError) as raised:
        storage.save_vars({"x": "next"})

    assert raised.value is cleanup_error
    assert json.loads((tmp_path / "vars.json.bak").read_text(
        encoding="utf-8")) == {"x": "latest"}
    storage.configure_storage(str(tmp_path))
    assert storage.load_vars() == {"x": "latest"}


def test_primary_rename_move_then_memory_error_keeps_latest_backup(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    assert storage.save_vars({"x": "older"}) is True
    assert storage.save_vars({"x": "latest"}) is True
    primary = str(tmp_path / "vars.json")
    backup = str(tmp_path / "vars.json.bak")
    primary_error = MemoryError("rename completed before injected OOM")
    cleanup_error = MemoryError("injected cleanup OOM")
    real_rename = storage.os.rename
    real_remove = storage._remove_if_exists

    def move_primary_then_oom(source, target):
        if str(source) == primary and str(target) == backup:
            real_rename(source, target)
            raise primary_error
        return real_rename(source, target)

    def cleanup_oom(path):
        if str(path).endswith(".tmp"):
            raise cleanup_error
        return real_remove(path)

    monkeypatch.setattr(storage.os, "rename", move_primary_then_oom)
    monkeypatch.setattr(storage, "_remove_if_exists", cleanup_oom)

    with pytest.raises(MemoryError) as raised:
        storage.save_vars({"x": "next"})

    assert raised.value is primary_error
    assert json.loads((tmp_path / "vars.json").read_text(
        encoding="utf-8")) == {"x": "latest"}
    assert json.loads((tmp_path / "vars.json.bak").read_text(
        encoding="utf-8")) == {"x": "older"}
    storage.configure_storage(str(tmp_path))
    assert storage.load_vars() == {"x": "latest"}


def test_interrupted_commit_revalidates_a_now_unknown_primary_before_retry(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    assert storage.save_vars({"x": 1}) is True
    assert storage.save_vars({"x": 2}) is True
    (tmp_path / "vars.json").write_text("{broken", encoding="utf-8")
    real_rename = storage.os.rename

    def fail_new_primary(source, target):
        if str(source).endswith(".tmp"):
            raise OSError("simulated interrupted commit")
        return real_rename(source, target)

    monkeypatch.setattr(storage.os, "rename", fail_new_primary)
    assert storage.save_vars({"x": 3}) is False
    monkeypatch.setattr(storage.os, "rename", real_rename)

    calls = []
    real_validate = storage._is_valid_variables_primary

    def record_validate(path):
        calls.append(path)
        return real_validate(path)

    monkeypatch.setattr(storage, "_is_valid_variables_primary", record_validate)
    assert storage.save_vars({"x": 4}) is True

    assert calls == [str(tmp_path / "vars.json")]
    assert json.loads((tmp_path / "vars.json.bak").read_text(encoding="utf-8")) == {
        "x": 1}
    assert not (tmp_path / "vars.json.bak.hold").exists()


def test_invalid_number_tag_primary_never_replaces_good_backup(tmp_path):
    malformed = {"x": {"__sci_calc_number__": "not-a-number"}}
    backup = {"x": 1}
    (tmp_path / "vars.json").write_text(
        json.dumps(malformed), encoding="utf-8")
    (tmp_path / "vars.json.bak").write_text(
        json.dumps(backup), encoding="utf-8")
    storage.configure_storage(str(tmp_path))

    assert storage.save_vars({"x": 2}) is True
    assert json.loads((tmp_path / "vars.json").read_text(
        encoding="utf-8")) == {"x": 2}
    assert json.loads((tmp_path / "vars.json.bak").read_text(
        encoding="utf-8")) == backup
    assert json.loads((tmp_path / "vars.json.bad").read_text(
        encoding="utf-8")) == malformed


def test_power_cut_held_backup_recovers_when_primary_and_backup_are_invalid(
        tmp_path, monkeypatch):
    held = {
        "answer": {"__sci_calc_number__": "123e4"},
        "label": "preserve me",
    }
    (tmp_path / "vars.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "vars.json.bak").write_text(
        '{"x":{"__sci_calc_number__":"not-a-number"}}',
        encoding="utf-8")
    held_path = tmp_path / "vars.json.bak.hold"
    held_path.write_text(json.dumps(held), encoding="utf-8")
    storage.configure_storage(str(tmp_path))

    recovered = storage.load_vars()
    assert recovered["answer"].to_literal() == "123e4"
    assert recovered["label"] == "preserve me"
    assert json.loads(held_path.read_text(encoding="utf-8")) == held

    real_rename = storage.os.rename

    def fail_new_primary(source, target):
        if str(source).endswith(".tmp"):
            raise OSError("simulated interrupted recovery commit")
        return real_rename(source, target)

    monkeypatch.setattr(storage.os, "rename", fail_new_primary)
    assert storage.save_vars({"x": 8}) is False

    storage.configure_storage(str(tmp_path))
    recovered = storage.load_vars()
    assert recovered["answer"].to_literal() == "123e4"
    assert recovered["label"] == "preserve me"


def test_settings_whitelist_deduplicates_selection_and_rejects_excess(tmp_path):
    storage.configure_storage(str(tmp_path))

    assert storage.save_settings({
        "enabled_functions": ["basic", "basic", "plugin:solve"],
    }) is True
    assert storage.load_settings()["enabled_functions"] == [
        "basic", "plugin:solve"]

    too_many = ["plugin:p" + str(index)
                for index in range(MAX_ENABLED_PLUGINS + 1)]
    assert storage.save_settings({"enabled_functions": too_many}) is False
    assert "limit" in storage.last_error().lower()

    assert storage.save_settings({"not_a_setting": 1}) is False
    assert "unknown" in storage.last_error().lower()


def test_variable_file_schema_rejects_excess_and_nested_values(tmp_path):
    too_many = {"v" + str(index): index
                for index in range(MAX_VARIABLES + 1)}
    (tmp_path / "vars.json").write_text(json.dumps(too_many), encoding="utf-8")
    storage.configure_storage(str(tmp_path))

    assert storage.load_vars() == {}

    (tmp_path / "vars.json").write_text(
        json.dumps({"x": [1, 2]}), encoding="utf-8")
    storage.configure_storage(str(tmp_path))
    assert storage.load_vars() == {}


def test_variable_save_streaming_budget_accepts_supported_maximum_without_copy(
        tmp_path):
    storage.configure_storage(str(tmp_path))
    variables = {"v" + str(index): "x" * 96
                 for index in range(MAX_VARIABLES)}

    assert storage.save_vars(variables) is True
    assert storage.load_vars() is variables
    assert storage.last_error() == ""


def test_oversized_variable_file_is_rejected_before_json_decoding(
        tmp_path, monkeypatch):
    (tmp_path / "vars.json").write_text(
        "{" + " " * MAX_VARIABLES_FILE_BYTES + "}", encoding="utf-8")
    storage.configure_storage(str(tmp_path))
    monkeypatch.setattr(
        storage.json, "loads",
        lambda value: (_ for _ in ()).throw(
            AssertionError("oversized JSON must not be decoded")))

    assert storage.load_vars() == {}


def test_deep_variable_file_is_rejected_before_json_decoding(
        tmp_path, monkeypatch):
    (tmp_path / "vars.json").write_text(
        '{"x":{"a":{"b":{"c":{"d":1}}}}}', encoding="utf-8")
    storage.configure_storage(str(tmp_path))
    monkeypatch.setattr(
        storage.json, "loads",
        lambda value: (_ for _ in ()).throw(
            AssertionError("deep JSON must not be decoded")))

    assert storage.load_vars() == {}


def test_variable_save_memory_error_reaches_the_runtime_recovery_seam(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))

    def exhaust_heap(target, variables):
        raise MemoryError("injected streaming write")

    monkeypatch.setattr(storage, "_write_variables_json", exhaust_heap)

    with pytest.raises(MemoryError, match="injected streaming write"):
        storage.save_vars({"x": 1})


def test_settings_writer_keeps_primary_memory_error_when_cleanup_oom(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    assert storage.save_settings({"angle_mode": 1}) is True
    primary = MemoryError("injected settings write OOM")
    cleanup = MemoryError("injected settings cleanup OOM")
    real_rename = storage.os.rename
    real_remove = storage._remove_if_exists

    def interrupt_new_primary(source, target):
        if str(source).endswith(".tmp"):
            raise primary
        return real_rename(source, target)

    def cleanup_oom(path):
        if str(path).endswith(".tmp"):
            raise cleanup
        return real_remove(path)

    monkeypatch.setattr(storage.os, "rename", interrupt_new_primary)
    monkeypatch.setattr(storage, "_remove_if_exists", cleanup_oom)

    with pytest.raises(MemoryError) as raised:
        storage.save_settings({"angle_mode": 0})

    assert raised.value is primary
    storage.configure_storage(str(tmp_path))
    assert storage.load_settings()["angle_mode"] == 1


def test_variable_writer_keeps_primary_memory_error_when_cleanup_oom(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    assert storage.save_vars({"x": 1}) is True
    assert storage.save_vars({"x": 2}) is True
    primary = MemoryError("injected vars write OOM")
    cleanup = MemoryError("injected vars cleanup OOM")
    real_rename = storage.os.rename
    real_remove = storage._remove_if_exists

    def interrupt_new_primary(source, target):
        if str(source).endswith(".tmp"):
            raise primary
        return real_rename(source, target)

    def cleanup_oom(path):
        if str(path).endswith(".tmp"):
            raise cleanup
        return real_remove(path)

    monkeypatch.setattr(storage.os, "rename", interrupt_new_primary)
    monkeypatch.setattr(storage, "_remove_if_exists", cleanup_oom)

    with pytest.raises(MemoryError) as raised:
        storage.save_vars({"x": 3})

    assert raised.value is primary
    storage.configure_storage(str(tmp_path))
    assert storage.load_vars() == {"x": 2}
    assert json.loads((tmp_path / "vars.json.bak").read_text(
        encoding="utf-8")) == {"x": 1}


def test_variable_writer_promotes_cleanup_oom_over_ordinary_write_error(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    primary = RuntimeError("injected vars write failure")
    cleanup = MemoryError("injected vars cleanup OOM")

    def fail_write(target, variables):
        raise primary

    def cleanup_oom(path):
        raise cleanup

    monkeypatch.setattr(storage, "_write_variables_json", fail_write)
    monkeypatch.setattr(storage, "_remove_if_exists", cleanup_oom)

    with pytest.raises(MemoryError) as raised:
        storage.save_vars({"x": 1})

    assert raised.value is cleanup


def test_settings_writer_promotes_cleanup_oom_over_ordinary_write_error(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    primary = RuntimeError("injected settings write failure")
    cleanup = MemoryError("injected settings cleanup OOM")

    def fail_write(*args, **kwargs):
        raise primary

    def cleanup_oom(path):
        raise cleanup

    monkeypatch.setattr(storage.json, "dump", fail_write)
    monkeypatch.setattr(storage, "_remove_if_exists", cleanup_oom)

    with pytest.raises(MemoryError) as raised:
        storage.save_settings({"angle_mode": 1})

    assert raised.value is cleanup


def test_variable_writer_streams_scalars_without_json_dump_or_encoded_table(
        tmp_path, monkeypatch):
    storage.configure_storage(str(tmp_path))
    calls = []

    def forbidden_dump(*args, **kwargs):
        raise AssertionError("vars writer must not build a whole encoded tree")

    real_dumps = storage.json.dumps

    def record_dumps(value):
        calls.append(value)
        return real_dumps(value)

    monkeypatch.setattr(storage.json, "dump", forbidden_dump)
    monkeypatch.setattr(storage.json, "dumps", record_dumps)

    number = Number.parse("1.234567890123456789e20")
    assert storage.save_vars({"answer": number, "label": "small"}) is True
    assert json.loads((tmp_path / "vars.json").read_text(encoding="utf-8")) == {
        "answer": {"__sci_calc_number__": number.to_literal()},
        "label": "small",
    }
    assert calls == [number.to_literal(), "small"]


def test_variable_primary_is_validated_once_before_streaming_commits(
        tmp_path, monkeypatch):
    (tmp_path / "vars.json").write_text('{"x":1}', encoding="utf-8")
    storage.configure_storage(str(tmp_path))
    calls = []
    real_validate = storage._is_valid_variables_primary

    def record_validate(path):
        calls.append(path)
        return real_validate(path)

    monkeypatch.setattr(storage, "_is_valid_variables_primary", record_validate)

    assert storage.save_vars({"x": 2}) is True
    assert storage.save_vars({"x": 3}) is True
    assert calls == [str(tmp_path / "vars.json")]


def test_deferred_storage_does_not_convert_memory_error_to_a_retry():
    def exhaust_heap(value):
        raise MemoryError("injected deferred write")

    deferred = storage.DeferredStorage(vars_writer=exhaust_heap)
    deferred.request_vars({"x": 1})

    with pytest.raises(MemoryError, match="injected deferred write"):
        deferred.flush(0)
