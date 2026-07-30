"""Release-owned missing-dependency fixture for bounded acceptance reloads."""

DEPENDENCIES = ("_acceptance_absent",)


def register(registry):
    registry.prefix("acceptance_missing", lambda value, context: value)
