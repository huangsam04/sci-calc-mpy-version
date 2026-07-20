"""Newton root solver plugin using a once-compiled expression."""
import math

from calc.functions import EvalContext
from calc.parser import compile_expression, evaluate_program

WELCOME = 'Solver loaded: solve("expr", "var", guess)'


def _solve(args, parent_context):
    if len(args) < 3:
        raise ValueError("solve needs 3 args: expression, variable, guess")
    expression = str(args[0])
    variable = str(args[1])
    value = float(args[2])
    program = compile_expression(expression, parent_context.registry)
    variables = dict(parent_context.variables)
    context = EvalContext(variables, parent_context.registry)
    tolerance = 1e-9
    step_size = 1e-5

    def sample(point):
        variables[variable] = point
        result = float(evaluate_program(program, context))
        if not math.isfinite(result):
            raise ValueError("solve produced a non-finite value")
        return result

    for _ in range(60):
        function_value = sample(value)
        if abs(function_value) < tolerance:
            return value
        derivative = (sample(value + step_size) - sample(value - step_size)) / (2.0 * step_size)
        if abs(derivative) < 1e-12:
            raise ValueError("solve derivative is too small; choose another guess")
        delta = function_value / derivative
        value -= delta
        if abs(delta) < tolerance * max(1.0, abs(value)):
            return value
    raise ValueError("solve did not converge")


def register(registry):
    registry.list_function("solve", _solve, min_args=3)
