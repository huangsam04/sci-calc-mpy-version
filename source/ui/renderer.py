"""Single-frame renderer with optional row-only OLED updates."""
import time

from ui.motion import DAMAGE_FULL, DAMAGE_NONE, DAMAGE_PARTIAL, DamageMap
from ui.theme import CONTENT_W, SCREEN_H


class Renderer:
    __slots__ = (
        "display", "sidebar", "last_present_us", "_visible_screen",
        "_sidebar_dirty", "_sidebar_polled", "_damage", "_hook_screen",
        "_collect_hook", "_partial_draw_hook", "_draw_hook", "_mark_hook")

    def __init__(self, display, sidebar, memory=None):
        self.display = display
        self.sidebar = sidebar
        self.last_present_us = 0
        self._visible_screen = None
        self._sidebar_dirty = True
        # The first boot frame uses Sidebar's fixed cached placeholder; ADC
        # construction and reads happen only through poll_sidebar() in a quiet
        # scheduler slot.
        self._sidebar_polled = True
        self._damage = DamageMap()
        # The steady present path invokes fixed class-level functions with the
        # visible screen explicitly, so partial frames never allocate bound
        # methods.
        self._hook_screen = None
        self._collect_hook = None
        self._partial_draw_hook = None
        self._draw_hook = None
        self._mark_hook = None

    def _present(self, rows):
        if self._sidebar_dirty:
            self.sidebar.draw(
                self.display, refresh=not self._sidebar_polled)
            self._sidebar_dirty = False
            self._sidebar_polled = False
        started = time.ticks_us()
        if rows is None:
            self.display.present()
        else:
            self.display.present_rows(rows)
        self.last_present_us = time.ticks_diff(time.ticks_us(), started)

    def _cache_screen_hooks(self, screen):
        """Resolve fixed class hooks once for one visible screen identity."""
        if self._hook_screen is screen:
            return
        screen_type = type(screen)
        self._collect_hook = getattr(
            screen_type, "collect_present_damage", None)
        self._partial_draw_hook = getattr(
            screen_type, "draw_present_rows", None)
        self._draw_hook = getattr(screen_type, "draw", None)
        self._mark_hook = getattr(screen_type, "mark_presented", None)
        self._hook_screen = screen

    def _collect_damage(self, screen):
        """Ask the visible screen for an explicit full/partial/no-damage result."""
        self._cache_screen_hooks(screen)
        damage = self._damage
        damage.clear()
        if self._visible_screen is not screen:
            damage.request_full()
            return DAMAGE_FULL
        collector = self._collect_hook
        if collector is not None:
            result = collector(screen, damage)
            if result == DAMAGE_FULL or damage.full:
                damage.request_full()
                return DAMAGE_FULL
            if result == DAMAGE_NONE or damage.count == 0:
                return DAMAGE_NONE
            return DAMAGE_PARTIAL
        damage.request_full()
        return DAMAGE_FULL

    def present(self, screen):
        damage_state = self._collect_damage(screen)
        if self._sidebar_dirty:
            damage_state = DAMAGE_FULL
        if damage_state == DAMAGE_NONE:
            return False
        partial = (self._partial_draw_hook
                   if damage_state == DAMAGE_PARTIAL else None)
        if partial is None:
            if self._sidebar_dirty:
                self.display.clear_buffers(0)
            else:
                self.display.fill_rectangle(
                    0, 0, CONTENT_W, SCREEN_H, 0)
            drawer = self._draw_hook
            drawer(screen, self.display)
        else:
            partial(screen, self.display)
        self._present(self._damage.ranges if partial is not None else None)
        self._visible_screen = screen
        marker = self._mark_hook
        if marker is not None:
            marker(screen)
        return True

    def prepare_slide(self, screen):
        """Release the departed screen identity while retaining its pixels."""
        self._cache_screen_hooks(screen)
        self._visible_screen = None

    def present_slide(self, screen, direction, distance, delta):
        """Compose one directional page frame in the sole framebuffer."""
        self._cache_screen_hooks(screen)
        display = self.display
        display_type = type(display)
        display_type.shift_content(display, direction * delta, CONTENT_W)
        if direction < 0:
            offset = CONTENT_W - distance
            clip_start = offset
            clip_end = CONTENT_W
        else:
            offset = distance - CONTENT_W
            clip_start = 0
            clip_end = distance
        display_type.begin_content_draw(
            display, offset, clip_start, clip_end)
        try:
            self._draw_hook(screen, display)
        finally:
            display_type.end_content_draw(display)
        self._present(None)
        if distance >= CONTENT_W:
            self._visible_screen = screen
            marker = self._mark_hook
            if marker is not None:
                marker(screen)
        return True

    def invalidate(self):
        self._visible_screen = None
        self._sidebar_dirty = True
        self._sidebar_polled = True
        self._hook_screen = None
        self._collect_hook = None
        self._partial_draw_hook = None
        self._draw_hook = None
        self._mark_hook = None

    def invalidate_sidebar(self):
        self._sidebar_dirty = True
        # Callers such as the angle toggle already know which sidebar pixels
        # changed.  Reuse the cached battery sample for this input frame; ADC
        # work is reserved for ``poll_sidebar()`` in a quiet scheduler slot.
        self._sidebar_polled = True

    def poll_sidebar(self):
        """Poll slow chrome only from a scheduler-approved quiet interval."""
        if self._sidebar_dirty:
            return False
        refresh_needed = getattr(self.sidebar, "refresh_needed", None)
        if refresh_needed is None or not refresh_needed():
            return False
        self._sidebar_dirty = True
        self._sidebar_polled = True
        return True
