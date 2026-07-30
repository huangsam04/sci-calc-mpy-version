"""Release-owned dependent fixture for bounded acceptance reloads."""

DEPENDENCIES = ("_acceptance_core",)
EXPORTS = {"acceptance_result": 23}


def register(registry):
    registry.prefix("acceptance_dependent", lambda value, context: value)
