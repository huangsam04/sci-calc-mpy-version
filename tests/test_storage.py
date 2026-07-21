import json

from utils import storage


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
    monkeypatch.setattr(storage, "_atomic_write_json", lambda path, data: False)

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
