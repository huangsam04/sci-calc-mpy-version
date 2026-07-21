"""Shared motion timings for responsive, consistent calculator UI."""

# Long-distance motion gets more time; high-frequency cursor feedback must
# settle before the next deliberate key press.
PAGE_TRANSITION_MS = 190
PANEL_SLIDE_MS = 130
MENU_CURSOR_MS = 100
TEXT_CURSOR_MS = 70
CONTROL_MOTION_MS = 100
MOTION_EASING = "OUT_QUAD"

# 34ms caps active rendering at about 29.4 FPS. This leaves headroom for the
# full-frame SPI transfer and avoids chasing an unsustainable 50 FPS deadline.
ACTIVE_FRAME_MS = 34
IDLE_FRAME_MS = 66
ACTIVE_LOOP_SLEEP_MS = 8
IDLE_LOOP_SLEEP_MS = 10
SLEEP_SCAN_MS = 25
