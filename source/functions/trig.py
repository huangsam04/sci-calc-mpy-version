"""Hyperbolic and explicit-degree trigonometry plugin."""
import math

WELCOME = "Hyperbolic and degree trig functions loaded."


def _sinh(value, context):
    return math.sinh(value)


def _cosh(value, context):
    return math.cosh(value)


def _tanh(value, context):
    return math.tanh(value)


def _sind(value, context):
    return math.sin(value * math.pi / 180.0)


def _cosd(value, context):
    return math.cos(value * math.pi / 180.0)


def _tand(value, context):
    return math.tan(value * math.pi / 180.0)


def _pi(args, context):
    return math.pi


def register(registry):
    registry.prefix("sinh", _sinh)
    registry.prefix("cosh", _cosh)
    registry.prefix("tanh", _tanh)
    registry.prefix("sind", _sind)
    registry.prefix("cosd", _cosd)
    registry.prefix("tand", _tand)
    registry.list_function("PI", _pi)
