# SCI-CALC: Trigonometry function extensions
# The core trig functions (sin, cos, tan) are built-in.
# This file demonstrates how to add custom functions.

import math


def flist():
    """Return custom trig function definitions."""
    return [
        # Hyperbolic functions
        ("sinh", 4, "prefix", 0, None, sinh_func),
        ("cosh", 4, "prefix", 0, None, cosh_func),
        ("tanh", 4, "prefix", 0, None, tanh_func),

        # Degrees-based trig (always in degrees regardless of mode)
        ("sind", 4, "prefix", 0, None, sind_func),
        ("cosd", 4, "prefix", 0, None, cosd_func),
        ("tand", 4, "prefix", 0, None, tand_func),

        # Constants via zero-arg list functions
        ("PI", 5, "list", 0, None, pi_func),
    ]


def welcome():
    print("Trig extensions loaded.")
    return "Trig extensions (sinh, cosh, tanh, sind, cosd, tand) loaded."


def sinh_func(a, vars_dict):
    return math.sinh(a), vars_dict


def cosh_func(a, vars_dict):
    return math.cosh(a), vars_dict


def tanh_func(a, vars_dict):
    return math.tanh(a), vars_dict


def sind_func(a, vars_dict):
    return math.sin(math.radians(a)), vars_dict


def cosd_func(a, vars_dict):
    return math.cos(math.radians(a)), vars_dict


def tand_func(a, vars_dict):
    return math.tan(math.radians(a)), vars_dict


def pi_func(args, vars_dict):
    return math.pi, vars_dict
