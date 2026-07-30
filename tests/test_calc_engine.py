import pytest

from calc import functions as functions_module
from calc import parser as parser_module
from calc.functions import EvalContext, FunctionRegistry, register_builtins
from calc.limits import MAX_VARIABLE_TEXT_LENGTH, MAX_VARIABLES
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


def test_bare_numeric_literal_skips_the_allocating_general_parser(
        registry, monkeypatch):
    expression = "0e+" + "0" * 41 + "19"

    def general_parser_forbidden(*_args):
        raise AssertionError("bare numeric literal entered general parser")

    def number_parse_forbidden(_cls, _text):
        raise AssertionError("zero literal allocated a temporary Number")

    monkeypatch.setattr(parser_module, "_Compiler", general_parser_forbidden)
    monkeypatch.setattr(
        parser_module.Number, "parse", classmethod(number_parse_forbidden))

    assert len(expression) == 46
    program = compile_expression(expression, registry)
    assert program[0] == "literal"
    assert program[1] == 0
    assert evaluate(expression, EvalContext({}, registry)) is parser_module.numeric.ZERO
    with pytest.raises(parser_module.ParseError, match="Invalid number"):
        evaluate("0e1000000000", EvalContext({}, registry))
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


def test_symbol_operator_cannot_contain_parser_syntax():
    registry = FunctionRegistry()
    callback = lambda left, right, context: left

    for name in ("+(", "!!;", "?'", '!"'):
        with pytest.raises(ValueError, match="Reserved"):
            registry.infix(name, callback, 20)


def test_list_functions_require_well_formed_arguments(registry):
    context = EvalContext({}, registry)

    assert evaluate("max(3, 5, 4)", context) == 5
    with pytest.raises(Exception, match="Missing argument"):
        evaluate("max(3,)", context)
    with pytest.raises(Exception, match="at least 1"):
        evaluate("max()", context)


def test_evaluation_memory_error_reaches_the_resource_exhaustion_seam(
        registry):
    def exhaust_heap(value, context):
        raise MemoryError("injected")

    registry.prefix("oom", exhaust_heap)

    with pytest.raises(MemoryError, match="injected"):
        evaluate("oom 1", EvalContext({}, registry))


def test_compilation_memory_error_reaches_the_resource_exhaustion_seam(
        registry, monkeypatch):
    class ExhaustingCompiler:
        def __init__(self, expression, active_registry):
            raise MemoryError("injected compile")

    monkeypatch.setattr(parser_module, "_Compiler", ExhaustingCompiler)

    with pytest.raises(MemoryError, match="injected compile"):
        compile_expression("1+1", registry)


def test_variable_capacity_rejects_new_entries_but_allows_replacement(
        registry):
    context = EvalContext({}, registry)

    for index in range(MAX_VARIABLES):
        context.set_var("v" + str(index), index)

    with pytest.raises(ValueError, match="Variable limit reached"):
        context.set_var("overflow", 1)

    context.set_var("v0", 999)

    assert str(context.variables["v0"]) == "999"

    with pytest.raises(ValueError, match="Unsupported variable value"):
        context.set_var("v0", "x" * (MAX_VARIABLE_TEXT_LENGTH + 1))

    assert str(context.variables["v0"]) == "999"


def test_reload_clear_drains_the_live_table_without_a_key_snapshot(
        registry, monkeypatch):
    definitions = registry._defs

    def snapshot_forbidden(*_args):
        raise MemoryError("reload key snapshot")

    monkeypatch.setattr(
        functions_module, "tuple", snapshot_forbidden, raising=False)

    registry.clear_for_reload()

    assert registry._defs is definitions
    assert len(registry) == 0
