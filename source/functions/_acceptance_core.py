"""Release-owned core fixture for bounded acceptance reloads."""

EXPORTS = {"acceptance_seed": 17}


def register(registry):
    registry.prefix("acceptance_core", lambda value, context: value)
