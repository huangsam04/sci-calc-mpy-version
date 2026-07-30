import json

import pytest

from calc import number as number_module
from calc.functions import EvalContext, build_registry
from calc.number import Number
from calc.parser import ParseError, evaluate
from screens.calculator import CalculatorScreen
from utils import storage


def test_super_large_expressions_remain_finite_high_precision_numbers():
    context = EvalContext({}, build_registry())

    result = evaluate("10^100000 * 10^100000", context)

    assert isinstance(result, Number)
    assert result.to_scientific(4) == "1.0000*10^200000"
    assert "inf" not in str(result).lower()


def test_large_addends_with_different_coefficient_lengths_are_not_discarded():
    context = EvalContext({}, build_registry())

    result = evaluate("999999999999999999999999999999+1e34", context)

    assert result.to_scientific(4) == "1.0001*10^34"


def test_large_integer_normalisation_does_not_need_a_decimal_string(monkeypatch):
    def reject_decimal_string(_value):
        raise MemoryError("decimal rendering unavailable")

    monkeypatch.setattr(
        number_module, "str", reject_decimal_string, raising=False)

    result = Number(10 ** 95)

    assert result.coefficient == 1
    assert result.exponent == 95


def test_high_precision_display_digits_do_not_change_the_stored_result():
    screen = CalculatorScreen(
        None, registry=build_registry(), variables={}, display_digits=2)
    value = evaluate("12345", screen.context)

    assert screen._fmt(value) == "1.23*10^4"
    screen.set_display_digits(5)
    assert screen._fmt(value) == "1.23450*10^4"
    assert value == Number.parse("12345")


def test_plugin_float_infinity_is_rejected_at_the_evaluation_seam():
    registry = build_registry()
    registry.prefix("bad", lambda value, context: float("inf"))

    with pytest.raises(ParseError, match="Non-finite"):
        evaluate("bad(1)", EvalContext({}, registry))

    context = EvalContext({}, build_registry())
    context.variables["bad_value"] = float("inf")
    with pytest.raises(ParseError, match="Non-finite"):
        evaluate("bad_value", context)


def test_scientific_functions_use_the_same_high_precision_number_path():
    context = EvalContext({}, build_registry())

    assert float(evaluate("sqrt(2)", context)) == pytest.approx(1.41421356237)
    assert float(evaluate("sin(pi/2)", context)) == pytest.approx(1.0)
    assert float(evaluate("ln(exp(2))", context)) == pytest.approx(2.0)


def test_high_precision_variables_round_trip_through_json_storage(tmp_path):
    storage.configure_storage(str(tmp_path))
    original = Number.parse("1.2345678901234567890123456789e12345")

    assert storage.save_vars({"large": original}) is True
    raw = json.loads((tmp_path / "vars.json").read_text(encoding="utf-8"))
    assert raw["large"]["__sci_calc_number__"] == original.to_literal()

    storage.configure_storage(str(tmp_path))
    restored = storage.load_vars()["large"]
    assert restored == original
