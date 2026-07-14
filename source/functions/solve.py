# SCI-CALC: Equation solver extension
# Newton's method root finder — uses string arguments for expression + variable
#
# Usage:  solve("x^2 - 4", "x", 1)   → finds root of x²-4=0 near x=1  → 2.0
#         solve("sin(x)", "x", 3)     → finds root of sin(x)=0 near x=3 → 3.14159...
#
# Requires: string support in parser (calc/parser.py with T_STR token).

from calc.parser import evaluate
from calc.functions import _current_func_table


def flist():
    """Return custom function definitions."""
    return [
        ("solve", 4, "list", 3, None, solve_func),
    ]


def welcome():
    return "Solver loaded: solve(expr, var, guess)"


def solve_func(args, vars_dict):
    """Newton's method root finder.

    solve("expression", "variable", guess)

    Args:
        args[0]: expression string, e.g. "x^2 - 4"
        args[1]: variable name, e.g. "x"
        args[2]: initial guess (number)

    Returns (root, vars_dict).
    """
    # --- Parse arguments ---
    if len(args) < 3:
        raise ValueError("solve needs 3 args: solve(\"expr\", \"var\", guess)")

    expr = str(args[0])
    var = str(args[1])
    x = float(args[2])

    # Use the full function table (including SD extensions) set by main.py
    ft = _current_func_table or {}
    if not ft:
        # Fallback: build basic table if global hasn't been set yet
        from calc.functions import build_func_table
        ft = build_func_table(["basic", "trig", "math", "list"])

    # --- Newton iteration ---
    max_iter = 100
    tol = 1e-10
    h = 1e-7  # step for numerical derivative

    def _eval(val):
        """Evaluate expression at var=val, return float result."""
        test_vars = dict(vars_dict)
        test_vars[var] = val
        result, _ = evaluate(expr, test_vars, ft)
        return float(result)

    for i in range(max_iter):
        fx = _eval(x)

        if abs(fx) < tol:
            return x, vars_dict

        # Central difference derivative
        deriv = (_eval(x + h) - _eval(x - h)) / (2.0 * h)

        if abs(deriv) < 1e-15:
            raise ValueError(
                f"solve: derivative too small at x={x:.6g} — try a different guess"
            )

        step = fx / deriv
        x = x - step

        # Early exit if step is tiny (converged)
        if abs(step) < tol * max(1.0, abs(x)):
            return x, vars_dict

    # Max iterations reached — return best approximation
    return x, vars_dict
