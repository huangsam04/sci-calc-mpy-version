"""Function registry and built-in operations for SCI-CALC.

The public seam is intentionally small: plugins receive a FunctionRegistry and
register callbacks by kind.  Internally definitions stay as compact tuples to
keep heap use predictable on MicroPython.
"""
from calc import number as numeric
from calc.limits import (MAX_FUNCTION_NAME_LENGTH, MAX_PLUGIN_EXPORTS,
                         MAX_VARIABLES,
                         MAX_VARIABLE_TEXT_LENGTH,
                         is_ascii_identifier, is_plugin_name)
from calc.number import Number, coerce


KIND_INFIX = "infix"
KIND_PREFIX = "prefix"
KIND_POSTFIX = "postfix"
KIND_LIST = "list"
_KINDS = (KIND_INFIX, KIND_PREFIX, KIND_POSTFIX, KIND_LIST)
_DICT_TYPE = dict


def _is_alpha(char):
    code = ord(char)
    return 65 <= code <= 90 or 97 <= code <= 122


def _is_alnum(char):
    code = ord(char)
    return 48 <= code <= 57 or 65 <= code <= 90 or 97 <= code <= 122


class EvalContext:
    """Mutable state shared by one or more evaluations."""

    def __init__(self, variables, registry):
        if len(variables) > MAX_VARIABLES:
            raise ValueError("Variable limit exceeded")
        self.variables = variables
        self.registry = registry
        # Variables loaded from older JSON files are ordinary ints/floats.
        # Normalize them once at the evaluation seam so old persisted values
        # participate in the new arithmetic without a migration step.
        for name in variables:
            value = variables[name]
            if (isinstance(value, (Number, int, float))
                    and not isinstance(value, bool)):
                variables[name] = coerce(value)
        # Add-ons can use the high-precision helpers without importing a
        # particular internal implementation file.
        self.numeric = numeric
        self.dirty = False

    @property
    def angle_mode(self):
        return self.registry.angle_mode

    def set_var(self, name, value):
        if not is_ascii_identifier(name):
            raise ValueError("Invalid or too long variable name")
        is_boolean = isinstance(value, bool)
        if isinstance(value, (Number, int, float)) and not is_boolean:
            value = coerce(value)
        if (not isinstance(value, (Number, int, float, str))
                or is_boolean
                or (isinstance(value, str)
                    and len(value) > MAX_VARIABLE_TEXT_LENGTH)):
            raise ValueError("Unsupported variable value")
        existing = self.variables.get(name, _MISSING)
        if existing is _MISSING:
            if len(self.variables) >= MAX_VARIABLES:
                raise ValueError("Variable limit reached")
        if existing != value:
            self.variables[name] = value
            self.dirty = True
        return value

    def delete_var(self, name):
        if name in self.variables:
            del self.variables[name]
            self.dirty = True

    def consume_dirty(self):
        value = self.dirty
        self.dirty = False
        return value

    def mark_dirty(self):
        """Request another persistence attempt without changing variables."""
        self.dirty = True

    def plugin(self, name):
        """Return exports from a declared, loaded add-on dependency."""
        return self.registry.plugin(name)


_MISSING = object()


class FunctionRegistry:
    """Live registry shared by calculator, plotter and plugins.

    Definition tuple: (name, precedence, kind, min_args, associativity, callback)
    """

    def __init__(self, max_functions=None):
        self._defs = {}
        self._function_limit = max_functions
        self._revision = 0
        self.angle_mode = 0
        self.plugin_errors = []
        # The initial loader already knows these summaries.  Retaining the
        # report lets the Function Panel describe active add-ons without
        # compiling every SD source file for a second time during boot.
        self.plugin_functions = {}
        self.plugin_dependencies = {}
        self.plugin_files = ()
        self._plugin_exports = {}
        self._dependency_exports = {}
        self._symbolic_names = None

    def _add(self, name, callback, kind, precedence, associativity, min_args):
        if not isinstance(name, str) or not name:
            raise ValueError("Function name must be a non-empty string")
        if len(name) > MAX_FUNCTION_NAME_LENGTH:
            raise ValueError("Function name is too long")
        if (any(c in "(),;'\"" for c in name)
                or any(c.isspace() for c in name)):
            raise ValueError("Reserved or whitespace function name: " + name)
        identifier = _is_alpha(name[0]) or name[0] == "_"
        if identifier:
            if not all(_is_alnum(c) or c == "_" for c in name):
                raise ValueError("Invalid identifier function name: " + name)
        elif any(_is_alnum(c) or c == "_" for c in name):
            raise ValueError("Symbol operators cannot mix letters or digits: " + name)
        if kind not in _KINDS:
            raise ValueError("Invalid function kind: " + str(kind))
        if kind in (KIND_PREFIX, KIND_LIST) and not identifier:
            raise ValueError("Prefix and list functions require an identifier: " + name)
        if not callable(callback):
            raise ValueError("Function callback is not callable: " + name)
        if not isinstance(precedence, int) or precedence < 0:
            raise ValueError("Precedence must be a non-negative integer")
        if kind == KIND_INFIX and associativity not in ("left", "right"):
            raise ValueError("Infix associativity must be left or right")
        if kind != KIND_INFIX:
            associativity = None
        if not isinstance(min_args, int) or min_args < 0:
            raise ValueError("min_args must be a non-negative integer")
        if name in self._defs:
            raise ValueError("Function already registered: " + name)
        if (self._function_limit is not None
                and len(self._defs) >= self._function_limit):
            raise ValueError("Function registration limit reached")
        self._defs[name] = (name, precedence, kind, min_args, associativity, callback)
        self._symbolic_names = None
        self._revision += 1
        return self

    def _add_known(self, name, callback, kind, precedence, associativity,
                   min_args):
        """Insert a firmware-owned definition whose constants were reviewed."""
        if name in self._defs:
            raise ValueError("Function already registered: " + name)
        if (self._function_limit is not None
                and len(self._defs) >= self._function_limit):
            raise ValueError("Function registration limit reached")
        self._defs[name] = (
            name, precedence, kind, min_args, associativity, callback)
        self._symbolic_names = None
        self._revision += 1
        return self

    def infix(self, name, callback, precedence=10, associativity="left"):
        return self._add(name, callback, KIND_INFIX, precedence, associativity, 0)

    def prefix(self, name, callback, precedence=50):
        return self._add(name, callback, KIND_PREFIX, precedence, None, 0)

    def postfix(self, name, callback, precedence=60):
        return self._add(name, callback, KIND_POSTFIX, precedence, None, 0)

    def list_function(self, name, callback, min_args=0, precedence=50):
        return self._add(name, callback, KIND_LIST, precedence, None, min_args)

    def get(self, name, default=None):
        return self._defs.get(name, default)

    def keys(self):
        return self._defs.keys()

    def items(self):
        return self._defs.items()

    @property
    def revision(self):
        """Increase whenever expression parsing rules can change."""
        return self._revision

    def __contains__(self, name):
        return name in self._defs

    def __len__(self):
        return len(self._defs)

    def clear(self):
        if self._defs:
            self._defs.clear()
            self._symbolic_names = None
            self._revision += 1
        self._plugin_exports.clear()
        self._dependency_exports.clear()
        self.plugin_functions = {}
        self.plugin_dependencies = {}
        self.plugin_files = ()

    def clear_for_reload(self):
        """Forget callbacks while retaining the live definition table.

        MicroPython releases a dict's whole hash table from ``clear()``.
        Reallocating that 640-byte run after Calculator state has fragmented
        the heap was the remaining supported reload failure.  Repeated
        ``popitem()`` calls avoid a full key snapshot while preserving the
        already-paid table for replacement definitions.
        """
        if self._defs:
            while self._defs:
                self._defs.popitem()
            self._symbolic_names = None
            self._revision += 1
        self._plugin_exports.clear()
        self._dependency_exports.clear()
        self.plugin_functions = {}
        self.plugin_dependencies = {}
        self.plugin_files = ()

    def replace(self, other):
        """Atomically adopt a fully built registry without changing our identity.

        ``other`` is a private staging registry.  All copying and plug-in
        execution must have completed before this method is called: assigning
        its already-built tables makes the live registry change as one small,
        non-allocating commit.  That keeps callers holding ``self`` valid when
        staging exhausts the heap.
        """
        if not isinstance(other, FunctionRegistry):
            raise TypeError("Replacement must be a FunctionRegistry")

        # Precompute the only potentially allocating value before changing any
        # live reference.  Small integers are immediate values on the target,
        # but this ordering keeps the rollback contract correct on every port.
        next_revision = self._revision + 1

        self._defs = other._defs
        self.angle_mode = other.angle_mode
        self.plugin_errors = other.plugin_errors
        self.plugin_functions = other.plugin_functions
        self.plugin_dependencies = other.plugin_dependencies
        self.plugin_files = other.plugin_files
        self._plugin_exports = other._plugin_exports
        self._dependency_exports = other._dependency_exports
        self._symbolic_names = other._symbolic_names
        self._revision = next_revision
        return self

    def _merged_definitions(self, other):
        """Build a combined definition table without touching the live one."""
        for name in other._defs:
            if name in self._defs:
                raise ValueError("Function already registered: " + name)
        if not other._defs:
            return self._defs
        # Build the merged table before changing the live one.  ``dict.update``
        # can fail part-way through on MicroPython; assigning only the finished
        # table keeps the registry valid when a plugin exhausts the heap.
        merged_defs = dict(self._defs)
        merged_defs.update(other._defs)
        return merged_defs

    def merge(self, other):
        merged_defs = self._merged_definitions(other)
        if merged_defs is self._defs:
            return self
        # Preflight the revision before exposing the new table.  This is
        # normally a tagged small integer on-device, but the transaction must
        # not rely on that representation detail.
        next_revision = self._revision + 1
        self._defs = merged_defs
        self._symbolic_names = None
        self._revision = next_revision
        return self

    def commit_plugin(self, name, staging, exports, in_place=False):
        """Atomically make one staged add-on visible to live evaluations.

        Every potentially allocating copy completes before the live definitions,
        exports, parser cache or revision change.  The loader is therefore able
        to let ``MemoryError`` reach the runtime recovery seam without a
        partially registered plugin surviving it.
        """
        if not is_plugin_name(name):
            raise ValueError("Plugin name is invalid or too long")
        if not isinstance(exports, _DICT_TYPE):
            raise ValueError("Plugin EXPORTS must be a dictionary")
        if len(exports) > MAX_PLUGIN_EXPORTS:
            raise ValueError("Plugin EXPORTS limit reached")
        for export_name in exports:
            if not is_ascii_identifier(export_name, MAX_FUNCTION_NAME_LENGTH):
                raise ValueError("Plugin EXPORTS key is invalid or too long")

        if in_place:
            for function_name in staging._defs:
                if function_name in self._defs:
                    raise ValueError(
                        "Function already registered: " + function_name)
            plugin_exports = dict(exports)
            next_revision = self._revision
            if staging._defs:
                next_revision += 1
            # The caller has retained a sufficiently sized live table through
            # clear_for_reload().  Populate that table directly; the outer
            # reload seam restores built-ins if any bounded insertion fails.
            for function_name in staging._defs:
                self._defs[function_name] = staging._defs[function_name]
            self._plugin_exports[name] = plugin_exports
            if staging._defs:
                self._symbolic_names = None
            self._revision = next_revision
            return self

        merged_defs = self._merged_definitions(staging)
        merged_exports = dict(self._plugin_exports)
        merged_exports[name] = dict(exports)
        next_revision = self._revision
        if merged_defs is not self._defs:
            # Complete the only arithmetic/allocation-sensitive operation
            # before touching either live table.
            next_revision = self._revision + 1

        if merged_defs is not self._defs:
            self._defs = merged_defs
            self._symbolic_names = None
        self._plugin_exports = merged_exports
        self._revision = next_revision
        return self

    def _plugin_commit_state(self):
        """Return a no-copy rollback token for the loader's final metadata step."""
        return (
            self._defs,
            self._plugin_exports,
            self._symbolic_names,
            self._revision,
        )

    def _restore_plugin_commit_state(self, state):
        """Restore a token produced by :meth:`_plugin_commit_state`."""
        (self._defs, self._plugin_exports, self._symbolic_names,
         self._revision) = state

    def _plugin_reload_state(self):
        """Return the complete no-copy checkpoint used by staged reloads.

        A reload replaces more than definitions: its error/report tables and
        dependency exports are live evaluator state too.  Keeping every field
        in one token lets a late failure restore the original registry identity
        without cloning any table or callback.
        """
        return (
            self._defs,
            self._function_limit,
            self._revision,
            self.angle_mode,
            self.plugin_errors,
            self.plugin_functions,
            self.plugin_dependencies,
            self._plugin_exports,
            self._dependency_exports,
            self._symbolic_names,
            self.plugin_files,
        )

    def _restore_plugin_reload_state(self, state):
        """Restore a token produced by :meth:`_plugin_reload_state`."""
        (self._defs, self._function_limit, self._revision, self.angle_mode,
         self.plugin_errors, self.plugin_functions, self.plugin_dependencies,
         self._plugin_exports, self._dependency_exports,
         self._symbolic_names, self.plugin_files) = state

    def set_plugin_dependencies(self, exports):
        """Internal loader hook exposing only declared dependencies to a plugin."""
        self._dependency_exports = dict(exports)

    def register_plugin(self, name, exports):
        """Store explicitly exported helpers after a plugin registered safely."""
        if not is_plugin_name(name):
            raise ValueError("Plugin name is invalid or too long")
        if not isinstance(exports, dict):
            raise ValueError("Plugin EXPORTS must be a dictionary")
        if len(exports) > MAX_PLUGIN_EXPORTS:
            raise ValueError("Plugin EXPORTS limit reached")
        for export_name in exports:
            if not is_ascii_identifier(export_name, MAX_FUNCTION_NAME_LENGTH):
                raise ValueError("Plugin EXPORTS key is invalid or too long")
        self._plugin_exports[name] = dict(exports)

    def plugin(self, name):
        """Get exports for a declared dependency or any live loaded add-on."""
        if name in self._dependency_exports:
            return self._dependency_exports[name]
        if name in self._plugin_exports:
            return self._plugin_exports[name]
        raise ValueError("Plugin dependency is not loaded: " + str(name))

    dependency = plugin

    def symbolic_names(self):
        names = self._symbolic_names
        if names is None:
            pending = []
            for name in self._defs:
                if not (_is_alpha(name[0]) or name[0] == "_"):
                    pending.append(name)
            pending.sort(key=len, reverse=True)
            names = tuple(pending)
            self._symbolic_names = names
        return names


def _to_rad(value, context):
    return value * numeric.PI / Number(180) if context.angle_mode else value


def _from_rad(value, context):
    return value * Number(180) / numeric.PI if context.angle_mode else value


def _add(a, b, context):
    return coerce(a) + coerce(b)


def _sub(a, b, context):
    return coerce(a) - coerce(b)


def _mul(a, b, context):
    return coerce(a) * coerce(b)


def _div(a, b, context):
    b = coerce(b)
    if b == 0:
        raise ZeroDivisionError("Division by zero")
    return coerce(a) / b


def _pow(a, b, context):
    return coerce(a) ** coerce(b)


def _assign(name, value, context):
    return context.set_var(name, value)


def _negative(value, context):
    return -coerce(value)


def _positive(value, context):
    return coerce(value)


def _sin(value, context):
    return numeric.sin(_to_rad(coerce(value), context))


def _cos(value, context):
    return numeric.cos(_to_rad(coerce(value), context))


def _tan(value, context):
    return numeric.tan(_to_rad(coerce(value), context))


def _asin(value, context):
    return _from_rad(numeric.asin(coerce(value)), context)


def _acos(value, context):
    return _from_rad(numeric.acos(coerce(value)), context)


def _atan(value, context):
    return _from_rad(numeric.atan(coerce(value)), context)


def _sec(value, context):
    return Number(1) / numeric.cos(_to_rad(coerce(value), context))


def _csc(value, context):
    return Number(1) / numeric.sin(_to_rad(coerce(value), context))


def _cot(value, context):
    return Number(1) / numeric.tan(_to_rad(coerce(value), context))


def _sqrt(value, context):
    return numeric.sqrt(coerce(value))


def _ln(value, context):
    return numeric.ln(coerce(value))


def _exp(value, context):
    return numeric.exp(coerce(value))


def _log(value, context):
    return numeric.log10(coerce(value))


def _abs(value, context):
    return abs(coerce(value))


def _max(args, context):
    return max(args)


def _min(args, context):
    return min(args)


FUNCTION_GROUPS = {
    "basic": ("+", "-", "*", "/", "^", "="),
    "trig": ("sin", "cos", "tan", "asin", "acos", "atan", "sec", "csc", "cot"),
    "math": ("sqrt", "ln", "exp", "log", "abs"),
    "list": ("max", "min"),
}
FUNCTION_GROUP_LABELS = {
    "basic": "Arithmetic",
    "trig": "Trigonometry",
    "math": "Scientific",
    "list": "List tools",
}
DEFAULT_ENABLED_GROUPS = ("basic", "trig", "math", "list")


def register_builtins(registry, enabled_groups=None):
    enabled = enabled_groups if enabled_groups is not None else DEFAULT_ENABLED_GROUPS
    if "basic" in enabled:
        registry._add_known("+", _add, KIND_INFIX, 20, "left", 0)
        registry._add_known("-", _sub, KIND_INFIX, 20, "left", 0)
        registry._add_known("*", _mul, KIND_INFIX, 30, "left", 0)
        registry._add_known("/", _div, KIND_INFIX, 30, "left", 0)
        registry._add_known("^", _pow, KIND_INFIX, 40, "right", 0)
        registry._add_known("=", _assign, KIND_INFIX, 10, "right", 0)
    if "trig" in enabled:
        for name, callback in (("sin", _sin), ("cos", _cos), ("tan", _tan),
                               ("asin", _asin), ("acos", _acos), ("atan", _atan),
                               ("sec", _sec), ("csc", _csc), ("cot", _cot)):
            registry._add_known(
                name, callback, KIND_PREFIX, 50, None, 0)
    if "math" in enabled:
        for name, callback in (("sqrt", _sqrt), ("ln", _ln), ("exp", _exp),
                               ("log", _log), ("abs", _abs)):
            registry._add_known(
                name, callback, KIND_PREFIX, 50, None, 0)
    if "list" in enabled:
        registry._add_known("max", _max, KIND_LIST, 50, None, 1)
        registry._add_known("min", _min, KIND_LIST, 50, None, 1)
    return registry


def build_registry(enabled_groups=None, max_functions=None):
    """Build a private registry, optionally preserving a caller's quota."""
    return register_builtins(FunctionRegistry(max_functions), enabled_groups)


# Unary callbacks are parser grammar, but kept here so they share angle/context
# conventions with registered callbacks.
UNARY_CALLBACKS = {"-": _negative, "+": _positive}
