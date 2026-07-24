"""Crash-tolerant JSON persistence for settings and calculator variables."""
import json
import os
import time


DEFAULTS = {
    "angle_mode": 0,
    "cursor_mode": 1,
    "enabled_functions": ["basic", "trig", "math", "list"],
    "diagnostics": False,
    "sleep_timeout_s": 180,
    "brightness": 100,
    "display_digits": 4,
}
MAX_SLEEP_TIMEOUT_S = 86_400
MIN_DISPLAY_DIGITS = 1
MAX_DISPLAY_DIGITS = 12
_NUMBER_TAG = "__sci_calc_number__"

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


def _encode_numbers(value):
    """Turn high-precision variables into explicit JSON-safe literals."""
    try:
        from calc.number import Number
    except ImportError:
        Number = ()
    if isinstance(value, Number):
        return {_NUMBER_TAG: value.to_literal()}
    if isinstance(value, dict):
        return {key: _encode_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_numbers(item) for item in value]
    return value


def _decode_numbers(value):
    """Restore values written by :func:`_encode_numbers` after a reboot."""
    if isinstance(value, dict):
        if set(value) == {_NUMBER_TAG} and isinstance(value[_NUMBER_TAG], str):
            try:
                from calc.number import Number
                return Number.parse(value[_NUMBER_TAG])
            except (ImportError, TypeError, ValueError):
                # Preserve malformed third-party data so it can still be
                # inspected or replaced rather than silently deleting it.
                return value
        return {key: _decode_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_numbers(item) for item in value]
    return value


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
        # Version is firmware metadata, not a user preference.  Drop the
        # legacy field when reading settings written by releases <= 1.1.0.
        merged.pop("version", None)
        if not isinstance(merged.get("enabled_functions"), list):
            merged["enabled_functions"] = list(DEFAULTS["enabled_functions"])
        if merged.get("angle_mode") not in (0, 1):
            merged["angle_mode"] = 0
        timeout = merged.get("sleep_timeout_s")
        if not isinstance(timeout, int) or timeout < 0:
            merged["sleep_timeout_s"] = DEFAULTS["sleep_timeout_s"]
        elif timeout > MAX_SLEEP_TIMEOUT_S:
            merged["sleep_timeout_s"] = MAX_SLEEP_TIMEOUT_S
        brightness = merged.get("brightness")
        if (not isinstance(brightness, int)
                or brightness < 10 or brightness > 100):
            merged["brightness"] = DEFAULTS["brightness"]
        display_digits = merged.get("display_digits")
        if (not isinstance(display_digits, int)
                or display_digits < MIN_DISPLAY_DIGITS
                or display_digits > MAX_DISPLAY_DIGITS):
            merged["display_digits"] = DEFAULTS["display_digits"]
        _settings_cache = merged
    return _settings_cache


def save_settings(settings):
    global _settings_cache
    merged = _copy_defaults()
    merged.update(settings)
    merged.pop("version", None)
    _settings_cache = merged
    return _atomic_write_json(_join(_storage_dir(), "settings.json"), merged)


def load_vars():
    global _vars_cache
    if _vars_cache is None:
        _vars_cache = _decode_numbers(
            _read_with_backup(_join(_storage_dir(), "vars.json"), {}))
    return _vars_cache


def save_vars(variables):
    global _vars_cache
    _vars_cache = dict(variables)
    return _atomic_write_json(_join(_storage_dir(), "vars.json"),
                              _encode_numbers(variables))


class DeferredStorage:
    """Coalesce persistence requests and commit them only from an idle loop."""

    def __init__(self, settings_writer=save_settings, vars_writer=save_vars,
                 retry_ms=2000):
        self._settings_writer = settings_writer
        self._vars_writer = vars_writer
        self._retry_ms = retry_ms
        self._settings_pending = None
        self._vars_pending = None
        self._settings_due = None
        self._vars_due = None

    def request_settings(self, settings, callback=None, owner=None):
        # The single-threaded loop coalesces to the latest live object.  Copying
        # a nested settings tree here used to compete with the animation that
        # the same key press had just started; encoding now happens at flush.
        self._settings_pending = (settings, callback, owner)
        self._settings_due = None

    def request_vars(self, variables, callback=None, owner=None):
        self._vars_pending = (variables, callback, owner)
        self._vars_due = None

    def detach_callbacks(self, owner):
        """Keep pending data without retaining a disposable page instance."""
        for name in ("_settings_pending", "_vars_pending"):
            pending = getattr(self, name)
            if pending is None:
                continue
            value, callback, callback_owner = pending
            if callback_owner is owner:
                setattr(self, name, (value, None, None))

    def _flush_pending(self, kind, writer, now):
        pending_name = "_" + kind + "_pending"
        due_name = "_" + kind + "_due"
        pending = getattr(self, pending_name)
        if pending is None:
            return None
        due = getattr(self, due_name)
        if due is not None and time.ticks_diff(now, due) < 0:
            return None

        value, callback, _owner = pending
        try:
            success = bool(writer(value))
        except Exception:
            success = False

        if success:
            setattr(self, pending_name, None)
            setattr(self, due_name, None)
        else:
            setattr(self, due_name, time.ticks_add(now, self._retry_ms))

        if callback is not None:
            try:
                callback(success)
            except Exception:
                pass
        return kind, success

    def flush(self, now):
        """Commit at most one pending write and report its kind and result."""
        result = self._flush_pending("settings", self._settings_writer, now)
        if result is not None:
            return result
        return self._flush_pending("vars", self._vars_writer, now)


def last_error():
    return _last_error
