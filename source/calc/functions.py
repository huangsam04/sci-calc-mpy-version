# ponytail: all built-in math operators as pluggable functions
"""Built-in function definitions for the calculator.

Each function receives individual arguments (not a list, except list-type)
and the vars dict. Returns (result, vars_dict).

All trig functions work in radians. Angle mode conversion is handled
by the global settings checked at call time (via angle_mode argument
passed through).
"""
import math

# Global state - set by main loop
ANGLE_MODE = 0  # 0=rad, 1=deg


def to_rad(x):
    """Convert degrees to radians if in degree mode."""
    return math.radians(x) if ANGLE_MODE else x


def from_rad(x):
    """Convert radians to degrees if in degree mode."""
    return math.degrees(x) if ANGLE_MODE else x


# --- Infix operators ---

def add_func(a, b, vars_dict):
    if a is None:
        return b, vars_dict
    return a + b, vars_dict


def sub_func(a, b, vars_dict):
    if a is None:
        return -b, vars_dict
    return a - b, vars_dict


def mul_func(a, b, vars_dict):
    if a is None:
        return 0, vars_dict
    return a * b, vars_dict


def div_func(a, b, vars_dict):
    if a is None:
        return 0, vars_dict
    if b == 0:
        raise ZeroDivisionError("Division by zero")
    return a / b, vars_dict


def pow_func(a, b, vars_dict):
    if a is None:
        return 0, vars_dict
    return math.pow(a, b), vars_dict


def assign_func(a, b, vars_dict):
    """Assignment: a is variable name (str), b is value."""
    vars_dict[a] = b
    return b, vars_dict


def comma_func(a, b, vars_dict):
    """Comma: build a tuple/list. Used internally by list functions."""
    if isinstance(a, tuple):
        return a + (b,), vars_dict
    return (a, b), vars_dict


# --- Prefix functions ---

def sin_func(a, vars_dict):
    return math.sin(to_rad(a)), vars_dict


def cos_func(a, vars_dict):
    return math.cos(to_rad(a)), vars_dict


def tan_func(a, vars_dict):
    return math.tan(to_rad(a)), vars_dict


def asin_func(a, vars_dict):
    return from_rad(math.asin(a)), vars_dict


def acos_func(a, vars_dict):
    return from_rad(math.acos(a)), vars_dict


def atan_func(a, vars_dict):
    return from_rad(math.atan(a)), vars_dict


def sec_func(a, vars_dict):
    return 1.0 / math.cos(to_rad(a)), vars_dict


def csc_func(a, vars_dict):
    return 1.0 / math.sin(to_rad(a)), vars_dict


def cot_func(a, vars_dict):
    return 1.0 / math.tan(to_rad(a)), vars_dict


def sqrt_func(a, vars_dict):
    return math.sqrt(a), vars_dict


def ln_func(a, vars_dict):
    return math.log(a), vars_dict


def exp_func(a, vars_dict):
    return math.exp(a), vars_dict


def log_func(a, vars_dict):
    return math.log10(a), vars_dict


def abs_func(a, vars_dict):
    return abs(a), vars_dict


def neg_func(a, vars_dict):
    return -a, vars_dict


# --- List functions ---

def max_func(args, vars_dict):
    if not args:
        return 0, vars_dict
    return max(args), vars_dict


def min_func(args, vars_dict):
    if not args:
        return 0, vars_dict
    return min(args), vars_dict


# --- Master function table ---
# Format: (name, priority, kind, arity, associativity, callable)
# name: used as trigger string in expressions
# priority: 0=lowest, higher = evaluated first
# kind: "infix" | "prefix" | "postfix" | "list"
# arity: for list functions, min number of args (0 = any)
# associativity: "left" | "right" | None

BUILTIN_FUNCTIONS = {
    # Basic infix operators
    "+":    ("+",    1, "infix",   0, "left",   add_func),
    "-":    ("-",    1, "infix",   0, "left",   sub_func),
    "*":    ("*",    2, "infix",   0, "left",   mul_func),
    "/":    ("/",    2, "infix",   0, "left",   div_func),
    "^":    ("^",    3, "infix",   0, "right",  pow_func),
    "=":    ("=",    0, "infix",   0, "left",   assign_func),
    ",":    (",",   -1, "infix",   0, "left",   comma_func),

    # Prefix functions (trig, math)
    "sin":  ("sin",  4, "prefix",  0, None,     sin_func),
    "cos":  ("cos",  4, "prefix",  0, None,     cos_func),
    "tan":  ("tan",  4, "prefix",  0, None,     tan_func),
    "asin": ("asin", 4, "prefix",  0, None,     asin_func),
    "acos": ("acos", 4, "prefix",  0, None,     acos_func),
    "atan": ("atan", 4, "prefix",  0, None,     atan_func),
    "sec":  ("sec",  4, "prefix",  0, None,     sec_func),
    "csc":  ("csc",  4, "prefix",  0, None,     csc_func),
    "cot":  ("cot",  4, "prefix",  0, None,     cot_func),
    "sqrt": ("sqrt", 4, "prefix",  0, None,     sqrt_func),
    "ln":   ("ln",   4, "prefix",  0, None,     ln_func),
    "exp":  ("exp",  4, "prefix",  0, None,     exp_func),
    "log":  ("log",  4, "prefix",  0, None,     log_func),
    "abs":  ("abs",  4, "prefix",  0, None,     abs_func),

    # List functions
    "max":  ("max",  4, "list",    0, None,     max_func),
    "min":  ("min",  4, "list",    0, None,     min_func),
}


def merge_functions(base_table, new_defs):
    """Merge new function definitions into the table. Conflicts: last wins."""
    for name, prio, kind, arity, assoc, func in new_defs:
        base_table[name] = (name, prio, kind, arity, assoc, func)
    return base_table


# ponytail: group definitions for function panel toggling
# Each group maps to function names in BUILTIN_FUNCTIONS
FUNCTION_GROUPS = {
    "basic":    ["+", "-", "*", "/", "^", "=", ","],
    "trig":     ["sin", "cos", "tan", "asin", "acos", "atan", "sec", "csc", "cot"],
    "math":     ["sqrt", "ln", "exp", "log", "abs"],
    "list":     ["max", "min"],
}

# Default: all groups enabled
DEFAULT_ENABLED_GROUPS = ["basic", "trig", "math", "list"]

# Global reference to the active function table — set by main.py after
# SD extensions are loaded. Used by solve() and other meta-functions
# that need to re-evaluate expressions internally.
_current_func_table = {}


def build_func_table(enabled_groups=None, extra_defs=None):
    """Build a function table from enabled builtin groups + optional extras.

    Args:
        enabled_groups: list of group names to enable, or None for all
        extra_defs: list of (name, prio, kind, arity, assoc, func) to add on top

    Returns:
        dict: {name: (name, prio, kind, arity, assoc, func)}
    """
    if enabled_groups is None:
        enabled_groups = DEFAULT_ENABLED_GROUPS

    table = {}
    for group_name in enabled_groups:
        if group_name in FUNCTION_GROUPS:
            for func_name in FUNCTION_GROUPS[group_name]:
                if func_name in BUILTIN_FUNCTIONS:
                    table[func_name] = BUILTIN_FUNCTIONS[func_name]

    # Add extra definitions on top (overrides)
    if extra_defs:
        merge_functions(table, extra_defs)

    return table
