"""Function registry and built-in operations for SCI-CALC.

The public seam is intentionally small: plugins receive a FunctionRegistry and
register callbacks by kind.  Internally definitions stay as compact tuples to
keep heap use predictable on MicroPython.
"""
import math


KIND_INFIX = "infix"
KIND_PREFIX = "prefix"
KIND_POSTFIX = "postfix"
KIND_LIST = "list"
_KINDS = (KIND_INFIX, KIND_PREFIX, KIND_POSTFIX, KIND_LIST)


def _is_alpha(char):
    code = ord(char)
    return 65 <= code <= 90 or 97 <= code <= 122


def _is_alnum(char):
    code = ord(char)
    return 48 <= code <= 57 or 65 <= code <= 90 or 97 <= code <= 122


class EvalContext:
    """Mutable state shared by one or more evaluations."""

    def __init__(self, variables, registry):
        self.variables = variables
        self.registry = registry
        self.dirty = False

    @property
    def angle_mode(self):
        return self.registry.angle_mode

    def set_var(self, name, value):
        if self.variables.get(name, _MISSING) != value:
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


_MISSING = object()


class FunctionRegistry:
    """Live registry shared by calculator, plotter and plugins.

    Definition tuple: (name, precedence, kind, min_args, associativity, callback)
    """

    def __init__(self):
        self._defs = {}
        self.angle_mode = 0
        self.plugin_errors = []

    def _add(self, name, callback, kind, precedence, associativity, min_args):
        if not isinstance(name, str) or not name:
            raise ValueError("Function name must be a non-empty string")
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
        self._defs[name] = (name, precedence, kind, min_args, associativity, callback)
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

    def __contains__(self, name):
        return name in self._defs

    def __len__(self):
        return len(self._defs)

    def clear(self):
        self._defs.clear()

    def replace(self, other):
        """Replace definitions in-place so existing users keep a live reference."""
        self._defs.clear()
        self._defs.update(other._defs)
        self.angle_mode = other.angle_mode
        self.plugin_errors = list(other.plugin_errors)

    def merge(self, other):
        for name in other._defs:
            if name in self._defs:
                raise ValueError("Function already registered: " + name)
        self._defs.update(other._defs)
        return self

    def symbolic_names(self):
        names = []
        for name in self._defs:
            if not (_is_alpha(name[0]) or name[0] == "_"):
                names.append(name)
        names.sort(key=len, reverse=True)
        return names


def _to_rad(value, context):
    return value * math.pi / 180.0 if context.angle_mode else value


def _from_rad(value, context):
    return value * 180.0 / math.pi if context.angle_mode else value


def _add(a, b, context):
    return a + b


def _sub(a, b, context):
    return a - b


def _mul(a, b, context):
    return a * b


def _div(a, b, context):
    if b == 0:
        raise ZeroDivisionError("Division by zero")
    return a / b


def _pow(a, b, context):
    return math.pow(a, b)


def _assign(name, value, context):
    return context.set_var(name, value)


def _negative(value, context):
    return -value


def _positive(value, context):
    return value


def _sin(value, context):
    return math.sin(_to_rad(value, context))


def _cos(value, context):
    return math.cos(_to_rad(value, context))


def _tan(value, context):
    return math.tan(_to_rad(value, context))


def _asin(value, context):
    return _from_rad(math.asin(value), context)


def _acos(value, context):
    return _from_rad(math.acos(value), context)


def _atan(value, context):
    return _from_rad(math.atan(value), context)


def _sec(value, context):
    return 1.0 / math.cos(_to_rad(value, context))


def _csc(value, context):
    return 1.0 / math.sin(_to_rad(value, context))


def _cot(value, context):
    return 1.0 / math.tan(_to_rad(value, context))


def _sqrt(value, context):
    return math.sqrt(value)


def _ln(value, context):
    return math.log(value)


def _exp(value, context):
    return math.exp(value)


def _log(value, context):
    return math.log10(value)


def _abs(value, context):
    return abs(value)


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
    enabled = set(enabled)
    if "basic" in enabled:
        registry.infix("+", _add, 20)
        registry.infix("-", _sub, 20)
        registry.infix("*", _mul, 30)
        registry.infix("/", _div, 30)
        registry.infix("^", _pow, 40, "right")
        registry.infix("=", _assign, 10, "right")
    if "trig" in enabled:
        for name, callback in (("sin", _sin), ("cos", _cos), ("tan", _tan),
                               ("asin", _asin), ("acos", _acos), ("atan", _atan),
                               ("sec", _sec), ("csc", _csc), ("cot", _cot)):
            registry.prefix(name, callback)
    if "math" in enabled:
        for name, callback in (("sqrt", _sqrt), ("ln", _ln), ("exp", _exp),
                               ("log", _log), ("abs", _abs)):
            registry.prefix(name, callback)
    if "list" in enabled:
        registry.list_function("max", _max, 1)
        registry.list_function("min", _min, 1)
    return registry


def build_registry(enabled_groups=None):
    return register_builtins(FunctionRegistry(), enabled_groups)


# Unary callbacks are parser grammar, but kept here so they share angle/context
# conventions with registered callbacks.
UNARY_CALLBACKS = {"-": _negative, "+": _positive}
