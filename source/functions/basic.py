def register(registry):
    registry.infix("%", lambda left, right, _context: left % right, 30, "left")
