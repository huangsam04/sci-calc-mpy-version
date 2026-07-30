"""Compiled implementations for the three firmware-bundled add-ons."""

from calc.functions import EvalContext
from calc.number import Number, coerce
from calc.parser import compile_expression, evaluate_program


def _solve(args, parent_context):
    if len(args) < 3:
        raise ValueError("solve needs 3 args: expression, variable, guess")
    expression = str(args[0])
    variable = str(args[1])
    value = coerce(args[2])
    program = compile_expression(expression, parent_context.registry)
    variables = dict(parent_context.variables)
    context = EvalContext(variables, parent_context.registry)
    tolerance = Number.parse("1e-18")
    step_size = Number.parse("1e-10")

    def sample(point):
        variables[variable] = point
        return coerce(evaluate_program(program, context))

    for _ in range(60):
        function_value = sample(value)
        if abs(function_value) < tolerance:
            return value
        derivative = ((sample(value + step_size) - sample(value - step_size))
                      / (Number(2) * step_size))
        if abs(derivative) < Number.parse("1e-15"):
            raise ValueError(
                "solve derivative is too small; choose another guess")
        delta = function_value / derivative
        value -= delta
        if abs(delta) < tolerance * max(Number(1), abs(value)):
            return value
    raise ValueError("solve did not converge")


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


def _pi(_args, context):
    return context.numeric.PI


def _mod(left, right, _context):
    return left % right


def register_basic(registry):
    registry._add_known("%", _mod, "infix", 30, "left", 0)


def register_solve(registry):
    registry._add_known("solve", _solve, "list", 50, None, 3)


def register_trig(registry):
    registry._add_known("sinh", _sinh, "prefix", 50, None, 0)
    registry._add_known("cosh", _cosh, "prefix", 50, None, 0)
    registry._add_known("tanh", _tanh, "prefix", 50, None, 0)
    registry._add_known("sind", _sind, "prefix", 50, None, 0)
    registry._add_known("cosd", _cosd, "prefix", 50, None, 0)
    registry._add_known("tand", _tand, "prefix", 50, None, 0)
    registry._add_known("PI", _pi, "list", 50, None, 0)


def register_bundled(name, registry):
    """Register one canonical SD add-on without compiling its source shim."""
    if name == "basic":
        register_basic(registry)
    elif name == "solve":
        register_solve(registry)
    elif name == "trig":
        register_trig(registry)
    else:
        return False
    return True
