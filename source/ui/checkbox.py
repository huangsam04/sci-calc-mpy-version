# ponytail: checkbox with animated toggle
"""Checkbox widget with animated check mark."""
from ui.element import UIElement
from anim.engine import insert_animation


class Checkbox(UIElement):
    def __init__(self, x=0, y=0, label="", checked=False, font=None):
        super().__init__(x, y, 12, 12)
        self.label = label
        self.checked = checked
        self.font = font
        self._ani_val = 6 if checked else 0  # animation progress 0-6
        self._link = None     # optional external bool to sync
        self.gs = 15

    def set_link(self, bool_ref):
        """Link to an external boolean variable."""
        self._link = bool_ref

    def toggle(self):
        self.checked = not self.checked
        if self._link is not None:
            self._link[0] = self.checked
        # Animate the fill
        import time
        target_val = 6 if self.checked else 0
        insert_animation(
            {'ani_val': self._ani_val, '_target': self},
            '_ani_val_proxy', self._ani_val, target_val, 100, "INDENT"
        )
        # ponytail: use a dict proxy for animation target
        self._ani_anim = {
            'target': self,
            'start': self._ani_val,
            'end': target_val,
            'start_time': time.ticks_ms(),
            'duration': 100,
        }

    def _update_ani(self):
        import time
        if not hasattr(self, '_ani_anim'):
            return
        a = self._ani_anim
        elapsed = time.ticks_diff(time.ticks_ms(), a['start_time'])
        t = min(1.0, elapsed / a['duration'])
        import math
        eased = 1 - math.pow(2, -10 * t)
        self._ani_val = int(a['start'] + (a['end'] - a['start']) * eased)
        if t >= 1.0:
            self._ani_val = a['end']
            del self._ani_anim

    def draw(self, display):
        self._update_ani()
        # Draw label
        if self.label:
            if self.font:
                display.draw_text(self.x + 14, self.y, self.label, self.font, gs=self.gs)
            else:
                display.draw_text8x8(self.x + 14, self.y, self.label, gs=self.gs)
        # Draw checkbox frame (10x10)
        display.draw_rectangle(self.x, self.y, 10, 10, self.gs)
        # Draw fill based on animation value (0-6 fills inward)
        if self._ani_val > 0:
            inset = 6 - self._ani_val
            fill_x = self.x + 2 + inset
            fill_y = self.y + 2 + inset
            fill_w = max(0, 6 - inset * 2)
            fill_h = max(0, 6 - inset * 2)
            if fill_w > 0 and fill_h > 0:
                display.fill_rectangle(fill_x, fill_y, fill_w, fill_h, self.gs)

    def update(self, kb):
        event = kb.pop_key_event()
        if event is None:
            return None
        r, c, _ = event  # checkbox ignores shift — ENT is at fixed position (3,3)
        if r == 3 and c == 3:  # ENT toggles when checkbox is focused
            self.toggle()
            return "TOGGLE"
        return None
