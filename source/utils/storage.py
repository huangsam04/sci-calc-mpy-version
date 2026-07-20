"""Crash-tolerant JSON persistence for settings and calculator variables."""
import json
import os


DEFAULTS = {
    "angle_mode": 0,
    "cursor_mode": 1,
    "enabled_functions": ["basic", "trig", "math", "list"],
    "version": "1.1.0",
    "diagnostics": False,
}

_storage_override = None
_settings_cache = None
_vars_cache = None
_last_error = ""


def configure_storage(directory=None):
    """Override the storage directory; primarily useful for host tests."""
    global _storage_override, _settings_cache, _vars_cache
    _storage_override = directory
    _settings_cache = None
    _vars_cache = None


def _storage_dir():
    if _storage_override is not None:
        return _storage_override
    try:
        os.stat("/sd")
        return "/sd"
    except OSError:
        return ""


def _join(directory, filename):
    if not directory:
        return "/" + filename
    if directory.endswith("/") or directory.endswith("\\"):
        return directory + filename
    separator = "\\" if "\\" in directory and "/" not in directory else "/"
    return directory + separator + filename


def _copy_defaults():
    result = dict(DEFAULTS)
    result["enabled_functions"] = list(DEFAULTS["enabled_functions"])
    return result


def _read_json_file(path):
    with open(path, "r") as source:
        return json.load(source)


def _read_with_backup(path, default):
    global _last_error
    for candidate in (path, path + ".bak"):
        try:
            value = _read_json_file(candidate)
            if not isinstance(value, dict):
                raise ValueError("JSON root must be an object")
            _last_error = ""
            return value
        except (OSError, ValueError) as error:
            _last_error = str(error)
    return dict(default)


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _remove_if_exists(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _is_valid_object(path):
    try:
        return isinstance(_read_json_file(path), dict)
    except (OSError, ValueError):
        return False


def _atomic_write_json(path, data):
    """Commit path.tmp, preserving the prior valid file as path.bak."""
    global _last_error
    temporary = path + ".tmp"
    backup = path + ".bak"
    damaged = path + ".bad"
    moved_valid_primary = False
    try:
        with open(temporary, "w") as target:
            json.dump(data, target)
            target.flush()
        if _exists(path):
            if _is_valid_object(path):
                _remove_if_exists(backup)
                os.rename(path, backup)
                moved_valid_primary = True
            else:
                # Never replace a known-good backup with a corrupt primary.
                _remove_if_exists(damaged)
                os.rename(path, damaged)
        os.rename(temporary, path)
        sync = getattr(os, "sync", None)
        if sync:
            sync()
        _last_error = ""
        return True
    except Exception as error:
        _last_error = str(error)
        _remove_if_exists(temporary)
        if moved_valid_primary and not _exists(path) and _exists(backup):
            try:
                os.rename(backup, path)
            except OSError:
                pass
        return False


def load_settings():
    global _settings_cache
    if _settings_cache is None:
        loaded = _read_with_backup(_join(_storage_dir(), "settings.json"), DEFAULTS)
        merged = _copy_defaults()
        merged.update(loaded)
        if not isinstance(merged.get("enabled_functions"), list):
            merged["enabled_functions"] = list(DEFAULTS["enabled_functions"])
        if merged.get("angle_mode") not in (0, 1):
            merged["angle_mode"] = 0
        _settings_cache = merged
    return _settings_cache


def save_settings(settings):
    global _settings_cache
    merged = _copy_defaults()
    merged.update(settings)
    _settings_cache = merged
    return _atomic_write_json(_join(_storage_dir(), "settings.json"), merged)


def load_vars():
    global _vars_cache
    if _vars_cache is None:
        _vars_cache = _read_with_backup(_join(_storage_dir(), "vars.json"), {})
    return _vars_cache


def save_vars(variables):
    global _vars_cache
    _vars_cache = dict(variables)
    return _atomic_write_json(_join(_storage_dir(), "vars.json"), variables)


def last_error():
    return _last_error
