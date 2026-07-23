"""Hyperbolic and explicit-degree trigonometry plugin."""
from calc.number import Number

WELCOME = "Hyperbolic and degree trig functions loaded."


def _sinh(value, context):
    return context.numeric.sinh(value)


def _cosh(value, context):
    return context.numeric.cosh(value)


def _tanh(value, context):
    return context.numeric.tanh(value)


def _sind(value, context):
    return context.numeric.sin(value * context.numeric.PI / Number(180))


def _cosd(value, context):
    return context.numeric.cos(value * context.numeric.PI / Number(180))


def _tand(value, context):
    return context.numeric.tan(value * context.numeric.PI / Number(180))


def _pi(args, context):
    return context.numeric.PI


def register(registry):
    registry.prefix("sinh", _sinh)
    registry.prefix("cosh", _cosh)
    registry.prefix("tanh", _tanh)
    registry.prefix("sind", _sind)
    registry.prefix("cosd", _cosd)
    registry.prefix("tand", _tand)
    registry.list_function("PI", _pi)
