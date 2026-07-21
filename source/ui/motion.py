"""Shared motion timings for responsive, consistent calculator UI."""

# Long-distance motion gets more time; high-frequency cursor feedback must
# settle before the next deliberate key press.
PAGE_TRANSITION_MS = 190
PANEL_SLIDE_MS = 130
MENU_CURSOR_MS = 100
TEXT_CURSOR_MS = 70
CONTROL_MOTION_MS = 100
MOTION_EASING = "OUT_QUAD"
