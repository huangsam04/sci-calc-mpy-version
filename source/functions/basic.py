# SCI-CALC: Basic arithmetic operators
# These are mostly handled by built-in functions in calc/functions.py
# This file provides extensions and overrides if needed.

def flist():
    """Return custom function definitions."""
    return [
        # You can add custom operators here.
        # Format: (name, priority, kind, arity, associativity, callable)
        # Example: modulo operator
        # ("%", 2, "infix", 0, "left", mod_func),
    ]


def welcome():
    print("Basic operators active.")
    return "Basic operators loaded."


# Optional: custom functions
def mod_func(a, b, vars_dict):
    if a is None:
        return 0, vars_dict
    if b == 0:
        raise ZeroDivisionError("Modulo by zero")
    return a % b, vars_dict
