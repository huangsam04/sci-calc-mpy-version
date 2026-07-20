"""Example symbolic-operator plugin."""

WELCOME = "Modulo operator loaded."


def _mod(left, right, context):
    if right == 0:
        raise ZeroDivisionError("Modulo by zero")
    return left % right


def register(registry):
    registry.infix("%", _mod, precedence=30, associativity="left")
