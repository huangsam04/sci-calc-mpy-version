"""Newton root solver plugin using a once-compiled expression."""

from calc.functions import EvalContext
from calc.number import Number, coerce
from calc.parser import compile_expression, evaluate_program

WELCOME = 'Solver loaded: solve("expr", "var", guess)'


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
            raise ValueError("solve derivative is too small; choose another guess")
        delta = function_value / derivative
        value -= delta
        if abs(delta) < tolerance * max(Number(1), abs(value)):
            return value
    raise ValueError("solve did not converge")


def register(registry):
    registry.list_function("solve", _solve, min_args=3)
