import pytest

from calc.functions import EvalContext, FunctionRegistry, register_builtins
from calc.parser import compile_expression, evaluate, evaluate_program


@pytest.fixture
def registry():
    value = FunctionRegistry()
    register_builtins(value)
    return value


def test_compiled_expression_obeys_precedence(registry):
    context = EvalContext({}, registry)
    program = compile_expression("2 + 3 * 4", registry)

    assert evaluate_program(program, context) == 14
    assert evaluate("2 + 3 * 4", context) == 14


@pytest.mark.parametrize(
    ("expression", "expected"),
    (("2^3^2", 512), ("-2^2", -4), ("(-2)^2", 4), ("2^-2", 0.25)),
)
def test_scientific_power_and_unary_semantics(registry, expression, expected):
    assert evaluate(expression, EvalContext({}, registry)) == expected


def test_assignment_is_right_associative_and_marks_context_dirty(registry):
    context = EvalContext({}, registry)

    assert evaluate("x=y=2; x+y", context) == 4
    assert context.variables == {"x": 2.0, "y": 2.0}
    assert context.consume_dirty() is True
    assert context.consume_dirty() is False
    context.mark_dirty()
    assert context.consume_dirty() is True


def test_plugin_registry_supports_symbolic_operators_and_validation(registry):
    registry.infix("%", lambda a, b, context: a % b, precedence=30)
    registry.infix("mod", lambda a, b, context: a % b, precedence=30)

    assert evaluate("10%4", EvalContext({}, registry)) == 2
    assert evaluate("10 mod 4", EvalContext({}, registry)) == 2
    with pytest.raises(ValueError, match="Reserved"):
        registry.prefix("(", lambda value, context: value)


def test_list_functions_require_well_formed_arguments(registry):
    context = EvalContext({}, registry)

    assert evaluate("max(3, 5, 4)", context) == 5
    with pytest.raises(Exception, match="Missing argument"):
        evaluate("max(3,)", context)
    with pytest.raises(Exception, match="at least 1"):
        evaluate("max()", context)
