# ponytail: JSON persistence with in-memory cache
"""Persistent storage using JSON files. In-memory cache ensures toggles
survive screen switches even if SD write fails."""
import json
import os

DEFAULTS = {
    "angle_mode": 0,
    "cursor_mode": 1,
    "enabled_functions": ["basic", "trig", "math", "list"],
    "version": "1.0.2",
}

# In-memory caches — survive across function calls within a session
_settings_cache = None
_vars_cache = None


def _storage_dir():
    try:
        os.stat("/sd")
        return "/sd"
    except OSError:
        return ""


def _settings_path():
    return _storage_dir() + "/settings.json"


def _vars_path():
    return _storage_dir() + "/vars.json"


def _read_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return dict(default) if isinstance(default, dict) else default


def _write_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
            f.flush()
        return True
    except OSError:
        return False


def load_settings():
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = _read_json(_settings_path(), DEFAULTS)
    return _settings_cache


def save_settings(settings):
    global _settings_cache
    _settings_cache = dict(settings)  # update cache immediately
    return _write_json(_settings_path(), settings)


def load_vars():
    global _vars_cache
    if _vars_cache is None:
        _vars_cache = _read_json(_vars_path(), {})
    return _vars_cache


def save_vars(vars_dict):
    global _vars_cache
    _vars_cache = dict(vars_dict)
    return _write_json(_vars_path(), vars_dict)
