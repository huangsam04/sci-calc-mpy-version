"""Shared motion timings for responsive, consistent calculator UI."""

# Long-distance motion gets more time; high-frequency cursor feedback must
# settle before the next deliberate key press.
PAGE_TRANSITION_MS = 190
PANEL_SLIDE_MS = 130
MENU_CURSOR_MS = 100
TEXT_CURSOR_MS = 70
CONTROL_MOTION_MS = 100
MOTION_EASING = "OUT_QUAD"

# The native transition compositor leaves enough time for a 16ms cadence,
# giving full-page slides at least twelve visible positions.
ACTIVE_FRAME_MS = 16
IDLE_FRAME_MS = 66
ACTIVE_LOOP_SLEEP_MS = 1
IDLE_LOOP_SLEEP_MS = 10
SLEEP_SCAN_MS = 25
