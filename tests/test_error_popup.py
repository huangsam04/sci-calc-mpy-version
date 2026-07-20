from ui.error_popup import ErrorPopup, friendly_error


def test_internal_errors_become_actionable_messages():
    assert friendly_error("Division by zero") == (
        "Cannot divide by zero", "Change the denominator")
    assert friendly_error("Undefined variable: 'tax'") == (
        "Unknown variable", "Define it first, e.g. x=2")
    assert friendly_error("Function is no longer loaded: 'sin'") == (
        "Function disabled", "Enable it in Functions")


def test_popup_timeout_is_independent_of_page_input(monkeypatch):
    now = [100]
    monkeypatch.setattr("ui.error_popup.time.ticks_ms", lambda: now[0])
    monkeypatch.setattr("ui.error_popup.time.ticks_diff", lambda a, b: a - b)
    popup = ErrorPopup()
    popup.show("1/0", "Division by zero")

    now[0] = 10_099
    assert popup.expired() is False
    now[0] = 10_100
    assert popup.expired() is True
