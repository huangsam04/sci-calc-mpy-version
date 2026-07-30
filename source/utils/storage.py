"""Crash-tolerant JSON persistence for settings and calculator variables."""
import json
import os
import time

from calc.limits import (MAX_ENABLED_FUNCTIONS, MAX_ENABLED_PLUGINS,
                         MAX_FUNCTION_NAME_LENGTH,
                         MAX_JSON_DEPTH, MAX_SETTINGS_FILE_BYTES,
                         MAX_VARIABLE_LITERAL_LENGTH, MAX_VARIABLE_NAME_LENGTH,
                         MAX_VARIABLES, MAX_VARIABLES_FILE_BYTES,
                         MAX_VARIABLE_TEXT_LENGTH, is_ascii_identifier,
                         is_plugin_name)


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
MAX_STRUCTURAL_RETRY_MS = 30_000
_NUMBER_TAG = "__sci_calc_number__"

_ERROR_NONE = 0
_ERROR_TRANSIENT = 1
_ERROR_STRUCTURAL = 2

_storage_override = None
_settings_cache = None
_vars_cache = None
# ``None`` means the primary has not been inspected since configure_storage().
# Once known, this prevents every later variable commit from decoding the old
# JSON tree merely to decide whether it deserves a .bak slot.
_vars_primary_valid = None
_last_error = ""
_last_error_kind = _ERROR_NONE


def configure_storage(directory=None):
    """Override the storage directory; primarily useful for host tests."""
    global _storage_override, _settings_cache, _vars_cache, _vars_primary_valid
    global _last_error, _last_error_kind
    _storage_override = directory
    _settings_cache = None
    _vars_cache = None
    _vars_primary_valid = None
    _last_error = ""
    _last_error_kind = _ERROR_NONE


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


def _file_size(path):
    stat = os.stat(path)
    size = getattr(stat, "st_size", None)
    return int(stat[6] if size is None else size)


def _check_json_depth(text, maximum_depth):
    """Reject a deeply nested payload before the JSON decoder builds it."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{" or char == "[":
            depth += 1
            if depth > maximum_depth:
                raise ValueError("JSON nesting limit exceeded")
        elif char == "}" or char == "]":
            depth -= 1
            if depth < 0:
                raise ValueError("Malformed JSON nesting")
    if in_string or depth != 0:
        raise ValueError("Malformed JSON nesting")


def _read_json_file(path, maximum_bytes):
    if _file_size(path) > maximum_bytes:
        raise ValueError("JSON file exceeds the storage limit")
    with open(path, "r") as source:
        text = source.read(maximum_bytes + 1)
    if len(text) > maximum_bytes:
        raise ValueError("JSON file exceeds the storage limit")
    _check_json_depth(text, MAX_JSON_DEPTH)
    return json.loads(text)


def _normalise_enabled_functions(value):
    """Validate, deduplicate and bound the persisted add-on selection."""
    if not isinstance(value, list):
        raise ValueError("enabled_functions must be a list")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Function selection names must be strings")
        if item.startswith("plugin:"):
            valid = is_plugin_name(item[7:])
        else:
            valid = is_ascii_identifier(item, MAX_FUNCTION_NAME_LENGTH)
        if not valid:
            raise ValueError("Function selection name is invalid or too long")
        if item in result:
            continue
        if len(result) >= MAX_ENABLED_FUNCTIONS:
            raise ValueError("Enabled function limit reached")
        result.append(item)
    plugin_count = 0
    for item in result:
        if item.startswith("plugin:"):
            plugin_count += 1
    if plugin_count > MAX_ENABLED_PLUGINS:
        raise ValueError("Enabled add-on limit reached")
    return result


def _validate_settings_payload(value):
    if len(value) > len(DEFAULTS) + 1:
        raise ValueError("Too many settings fields")
    for key in value:
        if key not in DEFAULTS and key != "version":
            raise ValueError("Unknown settings field")
    if "enabled_functions" in value:
        value["enabled_functions"] = _normalise_enabled_functions(
            value["enabled_functions"])


def _normalise_settings(value):
    """Build the only settings shape that may enter the live application."""
    if not isinstance(value, dict):
        raise ValueError("Settings must be an object")
    _validate_settings_payload(value)
    merged = _copy_defaults()
    for key in DEFAULTS:
        if key in value:
            merged[key] = value[key]
    merged["enabled_functions"] = _normalise_enabled_functions(
        merged["enabled_functions"])
    if type(merged["angle_mode"]) is not int or merged["angle_mode"] not in (0, 1):
        merged["angle_mode"] = DEFAULTS["angle_mode"]
    if type(merged["cursor_mode"]) is not int or merged["cursor_mode"] not in (0, 1):
        merged["cursor_mode"] = DEFAULTS["cursor_mode"]
    if type(merged["diagnostics"]) is not bool:
        merged["diagnostics"] = DEFAULTS["diagnostics"]
    timeout = merged["sleep_timeout_s"]
    if type(timeout) is not int or timeout < 0:
        merged["sleep_timeout_s"] = DEFAULTS["sleep_timeout_s"]
    elif timeout > MAX_SLEEP_TIMEOUT_S:
        merged["sleep_timeout_s"] = MAX_SLEEP_TIMEOUT_S
    brightness = merged["brightness"]
    if type(brightness) is not int or brightness < 10 or brightness > 100:
        merged["brightness"] = DEFAULTS["brightness"]
    display_digits = merged["display_digits"]
    if (type(display_digits) is not int
            or display_digits < MIN_DISPLAY_DIGITS
            or display_digits > MAX_DISPLAY_DIGITS):
        merged["display_digits"] = DEFAULTS["display_digits"]
    return merged


def _validate_raw_variable_value(value):
    if isinstance(value, bool):
        raise ValueError("Boolean variables are not supported")
    if isinstance(value, str):
        if len(value) > MAX_VARIABLE_TEXT_LENGTH:
            raise ValueError("Variable text is too long")
        return
    if isinstance(value, (int, float)):
        if len(str(value)) > MAX_VARIABLE_LITERAL_LENGTH:
            raise ValueError("Variable literal is too long")
        return
    if (isinstance(value, dict)
            and len(value) == 1
            and _NUMBER_TAG in value
            and isinstance(value[_NUMBER_TAG], str)
            and len(value[_NUMBER_TAG]) <= MAX_VARIABLE_LITERAL_LENGTH):
        return
    raise ValueError("Variable value type is not supported")


def _validate_raw_variables(value):
    if len(value) > MAX_VARIABLES:
        raise ValueError("Variable limit reached")
    for name in value:
        if not is_ascii_identifier(name, MAX_VARIABLE_NAME_LENGTH):
            raise ValueError("Variable name is invalid or too long")
        _validate_raw_variable_value(value[name])


def _validate_runtime_variables(value):
    try:
        from calc.number import Number
    except ImportError:
        Number = ()
    if not isinstance(value, dict) or len(value) > MAX_VARIABLES:
        raise ValueError("Variable limit reached")
    for name in value:
        if not is_ascii_identifier(name, MAX_VARIABLE_NAME_LENGTH):
            raise ValueError("Variable name is invalid or too long")
        item = value[name]
        if isinstance(item, Number):
            continue
        _validate_raw_variable_value(item)


def _number_type():
    """Import Number only on the persistence paths that actually need it."""
    try:
        from calc.number import Number
    except ImportError:
        return ()
    return Number


_NUMBER_FIELD_PREFIX = '{"' + _NUMBER_TAG + '":'


def _write_variables_json(target, variables):
    """Stream a validated flat variable table without an encoded clone.

    Keys are already ASCII identifiers.  Each scalar JSON fragment is bounded
    by the variable contracts, so a single ``json.dumps`` result is tiny while
    the complete table is never materialized as an extra dict or string.
    """
    Number = _number_type()
    first = True
    target.write("{")
    for name in variables:
        if first:
            first = False
        else:
            target.write(",")
        target.write('"')
        target.write(name)
        target.write('":')
        item = variables[name]
        if isinstance(item, Number):
            target.write(_NUMBER_FIELD_PREFIX)
            target.write(json.dumps(item.to_literal()))
            target.write("}")
        else:
            target.write(json.dumps(item))
    target.write("}")


def _decode_numbers(value):
    """Decode validated Number tags in place, avoiding a second vars table."""
    try:
        from calc.number import Number
    except ImportError:
        raise ValueError("Number support is unavailable")
    for name in value:
        item = value[name]
        if isinstance(item, dict):
            try:
                value[name] = Number.parse(item[_NUMBER_TAG])
            except (TypeError, ValueError):
                raise ValueError("Saved number is invalid")
    return value


def _read_with_backup(path, default, maximum_bytes, validator=None,
                      decoder=None):
    global _last_error
    first_error = ""
    for candidate in (path, path + ".bak"):
        try:
            value = _read_json_file(candidate, maximum_bytes)
            if not isinstance(value, dict):
                raise ValueError("JSON root must be an object")
            if validator is not None:
                validator(value)
            if decoder is not None:
                value = decoder(value)
            _last_error = ""
            return value
        except (OSError, TypeError, ValueError) as error:
            if not first_error or not isinstance(error, OSError):
                first_error = str(error)
    _last_error = first_error
    return dict(default)


def _read_vars_with_backup(path):
    """Load the flat vars table and remember whether primary was trustworthy."""
    global _last_error, _vars_primary_valid
    first_error = ""
    # A power cut between ``.bak -> .bak.hold`` and the next rename can leave
    # the hold file as the only intact recovery copy.  It stays a fallback,
    # never a primary trust signal, and goes through the same bounded decoder.
    for primary, candidate in ((True, path), (False, path + ".bak"),
                               (False, path + ".bak.hold")):
        try:
            value = _read_json_file(candidate, MAX_VARIABLES_FILE_BYTES)
            if not isinstance(value, dict):
                raise ValueError("JSON root must be an object")
            _validate_raw_variables(value)
            value = _decode_numbers(value)
            _last_error = ""
            _vars_primary_valid = primary
            return value
        except (OSError, TypeError, ValueError) as error:
            if not first_error or not isinstance(error, OSError):
                first_error = str(error)
    _last_error = first_error
    _vars_primary_valid = False
    return {}


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


def _is_valid_object(path, maximum_bytes):
    try:
        return isinstance(_read_json_file(path, maximum_bytes), dict)
    except (OSError, ValueError):
        return False


def _is_valid_variables_primary(path):
    """Inspect an unknown vars primary once, before any new write is staged."""
    try:
        value = _read_json_file(path, MAX_VARIABLES_FILE_BYTES)
        if not isinstance(value, dict):
            return False
        _validate_raw_variables(value)
        # Raw shape validation accepts a bounded Number literal.  The writer
        # must also reject literals that the load path cannot decode, or a
        # malformed primary could displace the last good backup.
        _decode_numbers(value)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _ensure_vars_primary_state(path):
    """Return the cached validity of ``path`` without repeat JSON parsing."""
    global _vars_primary_valid
    if _vars_primary_valid is not None:
        return _vars_primary_valid
    if not _exists(path):
        _vars_primary_valid = False
    else:
        _vars_primary_valid = _is_valid_variables_primary(path)
    return _vars_primary_valid


class _BoundedJsonWriter:
    """Stream JSON to disk while enforcing a byte budget without a second blob."""
    __slots__ = ("_target", "_remaining")

    def __init__(self, target, maximum_bytes):
        self._target = target
        self._remaining = maximum_bytes

    def write(self, value):
        # Text-mode files write UTF-8 on both host and device.  Counting
        # characters would let a non-ASCII value exceed the on-disk byte
        # contract, while ``encode`` would allocate another transient blob.
        if isinstance(value, str):
            size = 0
            for char in value:
                code = ord(char)
                if code < 0x80:
                    size += 1
                elif code < 0x800:
                    size += 2
                elif code < 0x10000:
                    size += 3
                else:
                    size += 4
        else:
            size = len(value)
        if size > self._remaining:
            raise ValueError("Serialized data exceeds the storage limit")
        self._remaining -= size
        return self._target.write(value)

    def flush(self):
        return self._target.flush()


def _atomic_write_json(path, data, maximum_bytes=MAX_VARIABLES_FILE_BYTES):
    """Commit path.tmp, preserving the prior valid file as path.bak."""
    global _last_error
    temporary = path + ".tmp"
    backup = path + ".bak"
    damaged = path + ".bad"
    moved_valid_primary = False
    try:
        with open(temporary, "w") as raw_target:
            target = _BoundedJsonWriter(raw_target, maximum_bytes)
            json.dump(data, target)
            target.flush()
        if _exists(path):
            if _is_valid_object(path, maximum_bytes):
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
    except MemoryError:
        # Heap exhaustion is the recovery signal.  Cleanup is best effort and
        # must not replace the exact primary exception with a second OOM.
        try:
            _remove_if_exists(temporary)
        except MemoryError:
            pass
        if moved_valid_primary:
            try:
                if not _exists(path) and _exists(backup):
                    try:
                        os.rename(backup, path)
                    except OSError:
                        pass
            except MemoryError:
                pass
        raise
    except Exception as error:
        _last_error = str(error)
        _remove_if_exists(temporary)
        if moved_valid_primary and not _exists(path) and _exists(backup):
            try:
                os.rename(backup, path)
            except OSError:
                pass
        return False


def _atomic_write_vars(path, variables,
                       maximum_bytes=MAX_VARIABLES_FILE_BYTES):
    """Commit flat variables with streaming JSON and one-time primary trust.

    Settings retain their generic object writer.  Variables are the hot path:
    the live table remains its only long-lived table, each JSON scalar is
    bounded, and the old primary is decoded at most once per storage session.
    """
    global _last_error, _last_error_kind, _vars_primary_valid
    temporary = path + ".tmp"
    backup = path + ".bak"
    held_backup = backup + ".hold"
    damaged = path + ".bad"
    valid_primary_move_attempted = False
    primary_move_attempted = False
    primary_was_valid = False
    preserved_backup = False
    try:
        # Establish this before creating the new file, while no encoded clone
        # exists.  Later retries reuse the recorded answer.
        primary_was_valid = _ensure_vars_primary_state(path)
        with open(temporary, "w") as raw_target:
            target = _BoundedJsonWriter(raw_target, maximum_bytes)
            _write_variables_json(target, variables)
            target.flush()
        if _exists(path):
            if primary_was_valid:
                # Do not discard the older backup until the new primary has
                # landed.  Cached trust avoids a repeated JSON parse, while
                # this one-file hold still protects data if outside code
                # corrupted a same-session primary behind our back.
                if _exists(backup):
                    _remove_if_exists(held_backup)
                    os.rename(backup, held_backup)
                    preserved_backup = True
                # A filesystem can complete a rename and still report an
                # error.  Arm recovery before the call so cleanup treats the
                # primary as possibly residing at either path.
                valid_primary_move_attempted = True
                primary_move_attempted = True
                os.rename(path, backup)
            else:
                # Preserve a previous good backup; only quarantine the known
                # bad/unknown primary that must not become the next backup.
                _remove_if_exists(damaged)
                primary_move_attempted = True
                os.rename(path, damaged)
        os.rename(temporary, path)
        sync = getattr(os, "sync", None)
        if sync:
            sync()
        # A prior power loss can leave an older .hold file behind.  Once the
        # replacement primary is durable it is no longer the only recovery
        # copy, so clean it whether this invocation created it or inherited it.
        _remove_if_exists(held_backup)
        _vars_primary_valid = True
        _last_error = ""
        _last_error_kind = _ERROR_NONE
        return True
    except MemoryError:
        # Keep the original OOM object for the runtime recovery seam even if
        # deleting or restoring files needs more heap.  Ordinary-error cleanup
        # below deliberately still promotes a cleanup OOM.
        if primary_move_attempted:
            # A failed rename can be reported after the filesystem changed.
            # Do not keep trusting an old in-memory verdict on the next retry.
            _vars_primary_valid = None
        try:
            _remove_if_exists(temporary)
        except MemoryError:
            pass
        primary_available = False
        if valid_primary_move_attempted:
            try:
                if _exists(path):
                    primary_available = True
                elif _exists(backup):
                    try:
                        os.rename(backup, path)
                        primary_available = True
                    except OSError:
                        _vars_primary_valid = None
            except MemoryError:
                _vars_primary_valid = None
        if preserved_backup:
            try:
                # Do not overwrite a backup that may be the valid primary
                # moved just before an error was reported.  A .hold file is a
                # supported fallback, so leaving it is safer than deleting
                # either uncertain copy.
                if (primary_available and not _exists(backup)
                        and _exists(held_backup)):
                    try:
                        os.rename(held_backup, backup)
                    except OSError:
                        _vars_primary_valid = None
            except MemoryError:
                _vars_primary_valid = None
        raise
    except Exception as error:
        _last_error = str(error)
        _last_error_kind = (_ERROR_TRANSIENT
                            if isinstance(error, OSError)
                            else _ERROR_STRUCTURAL)
        if primary_move_attempted:
            # See the MemoryError branch: after any interrupted replacement
            # the primary must be inspected again before it can replace .bak.
            _vars_primary_valid = None
        _remove_if_exists(temporary)
        primary_available = False
        if valid_primary_move_attempted:
            if _exists(path):
                primary_available = True
            elif _exists(backup):
                try:
                    os.rename(backup, path)
                    primary_available = True
                except OSError:
                    _vars_primary_valid = None
        if (preserved_backup and primary_available and not _exists(backup)
                and _exists(held_backup)):
            try:
                os.rename(held_backup, backup)
            except OSError:
                _vars_primary_valid = None
        return False


def load_settings():
    global _settings_cache
    if _settings_cache is None:
        loaded = _read_with_backup(
            _join(_storage_dir(), "settings.json"), {},
            MAX_SETTINGS_FILE_BYTES, _validate_settings_payload)
        _settings_cache = _normalise_settings(loaded)
    return _settings_cache


def save_settings(settings):
    global _last_error, _settings_cache
    try:
        merged = _normalise_settings(settings)
    except MemoryError:
        raise
    except (TypeError, ValueError) as error:
        _last_error = str(error)
        return False
    _settings_cache = merged
    return _atomic_write_json(
        _join(_storage_dir(), "settings.json"), merged,
        MAX_SETTINGS_FILE_BYTES)


def load_vars():
    global _vars_cache
    if _vars_cache is None:
        _vars_cache = _read_vars_with_backup(
            _join(_storage_dir(), "vars.json"))
    return _vars_cache


def save_vars(variables):
    global _last_error, _last_error_kind, _vars_cache
    # Keep the cache identical to the live calculator table: a failed write
    # must never clone (or discard) the user's newest in-memory value.
    _vars_cache = variables
    try:
        _validate_runtime_variables(variables)
    except MemoryError:
        raise
    except (TypeError, ValueError, OverflowError) as error:
        _last_error = str(error)
        _last_error_kind = _ERROR_STRUCTURAL
        return False
    return _atomic_write_vars(_join(_storage_dir(), "vars.json"), variables)


class DeferredStorage:
    """Coalesce persistence requests and commit them only from an idle loop."""

    __slots__ = (
        "_settings_writer", "_vars_writer", "_retry_ms",
        "_structural_retry_max_ms", "_settings_pending", "_vars_pending",
        "_settings_due", "_vars_due", "_vars_structural_retry_ms")

    def __init__(self, settings_writer=save_settings, vars_writer=save_vars,
                 retry_ms=2000,
                 structural_retry_max_ms=MAX_STRUCTURAL_RETRY_MS):
        self._settings_writer = settings_writer
        self._vars_writer = vars_writer
        self._retry_ms = retry_ms
        self._structural_retry_max_ms = max(
            retry_ms, structural_retry_max_ms)
        self._settings_pending = None
        self._vars_pending = None
        self._settings_due = None
        self._vars_due = None
        self._vars_structural_retry_ms = 0

    def request_settings(self, settings, callback=None, owner=None):
        # The single-threaded loop coalesces to the latest live object.  Copying
        # a nested settings tree here used to compete with the animation that
        # the same key press had just started; encoding now happens at flush.
        self._settings_pending = (settings, callback, owner)
        self._settings_due = None

    def request_vars(self, variables, callback=None, owner=None):
        self._vars_pending = (variables, callback, owner)
        self._vars_due = None
        # A new user edit may have fixed the structural problem; allow it to
        # flush at the next quiet loop rather than waiting for an old delay.
        self._vars_structural_retry_ms = 0

    def _retry_delay(self, kind, writer):
        """Back off only repeated structural vars writes, never I/O recovery."""
        if (kind == "vars" and writer is save_vars
                and _last_error_kind == _ERROR_STRUCTURAL):
            previous = self._vars_structural_retry_ms
            if previous:
                delay = min(self._structural_retry_max_ms, previous * 2)
            else:
                delay = self._retry_ms
            self._vars_structural_retry_ms = delay
            return delay
        if kind == "vars":
            self._vars_structural_retry_ms = 0
        return self._retry_ms

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
        except MemoryError:
            raise
        except Exception:
            success = False

        if success:
            setattr(self, pending_name, None)
            setattr(self, due_name, None)
            if kind == "vars":
                self._vars_structural_retry_ms = 0
        else:
            setattr(self, due_name, time.ticks_add(
                now, self._retry_delay(kind, writer)))

        if callback is not None:
            try:
                callback(success)
            except MemoryError:
                raise
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
