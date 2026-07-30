"""Small, shared resource contracts for untrusted calculator input."""

# These limits deliberately leave room for the resident display, Plot workspace
# and recovery path on the target's constrained heap.  They are contracts, not
# UI hints: callers must reject excess input before allocating an unbounded
# object graph.
MAX_VARIABLES = 16
MAX_VARIABLE_NAME_LENGTH = 24
MAX_VARIABLE_TEXT_LENGTH = 96
MAX_VARIABLE_LITERAL_LENGTH = 96

MAX_SETTINGS_FILE_BYTES = 1024
MAX_VARIABLES_FILE_BYTES = 2048
MAX_JSON_DEPTH = 4

MAX_ENABLED_FUNCTIONS = 12
MAX_FUNCTION_NAME_LENGTH = 32
MAX_ENABLED_PLUGINS = 8
MAX_DISCOVERED_PLUGIN_FILES = 16
MAX_PLUGIN_SOURCE_BYTES = 4096
MAX_PLUGIN_FUNCTIONS = 16
MAX_PLUGIN_DEPENDENCIES = 8
MAX_PLUGIN_DEPENDENCY_DEPTH = 4
MAX_PLUGIN_EXPORTS = 16


def is_ascii_identifier(value, maximum_length=MAX_VARIABLE_NAME_LENGTH):
    """Return whether a persisted identifier fits the fixed input contract."""
    if not isinstance(value, str) or not value or len(value) > maximum_length:
        return False
    first = value[0]
    first_code = ord(first)
    if not (65 <= first_code <= 90 or 97 <= first_code <= 122 or first == "_"):
        return False
    for char in value[1:]:
        code = ord(char)
        if not (48 <= code <= 57 or 65 <= code <= 90 or 97 <= code <= 122
                or char == "_"):
            return False
    return True


def is_plugin_name(value):
    """Keep add-on names short and safe to use in a generated module name."""
    return is_ascii_identifier(value, MAX_FUNCTION_NAME_LENGTH)
