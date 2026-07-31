import time

from ui.element import UIElement
from ui import theme as _theme


PICKER_CURSOR_MS = 96
PICKER_PAGE_MS = 160
_PAGE_MOTION = 1 << 28


class FunctionPicker(UIElement):
    transition_title = "Functions"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("_state",)

    def __init__(self, font, calc_screen):
        # calc, names, cursor, offset, notice, scenario lease, motion start,
        # packed motion, fixed visible-label cache, cached footer right text.
        # Selection and page movement share the same 2 scalar motion slots.
        # Build the only names backing block while the boot heap is contiguous.
        # Later activations refill and sort the same list in place.
        names = sorted(calc_screen.context.registry.keys())
        labels = [None] * 20
        labels[17] = -1
        labels[18] = -1
        labels[19] = -1
        self._state = [
            calc_screen, names, 0, 0, "", None, -1, 0, labels, ""]

    @staticmethod
    def _position(index, page):
        relative = max(0, index - page)
        x = 4 if relative < 4 else 105
        return x | ((15 + (relative % 4) * 10) << 8)

    @staticmethod
    def _pack_cursor(start_x, start_y, current_x, current_y):
        return ((start_x & 255) | ((start_y & 63) << 8)
                | ((current_x & 255) << 14)
                | ((current_y & 63) << 22))

    def _snap_motion(self):
        state = self._state
        packed = state[7]
        if state[6] >= 0 and packed & _PAGE_MOTION:
            labels = state[8]
            index = 0
            while index < 8:
                labels[index] = labels[8 + index]
                labels[8 + index] = None
                index += 1
            labels[16] = state[1]
            labels[17] = state[3]
        position = self._position(state[2], state[3])
        x = position & 255
        y = position >> 8
        state[6] = -1
        state[7] = self._pack_cursor(x, y, x, y)

    def _cache_labels(self, page, offset):
        state = self._state
        names = state[1]
        count = len(names)
        labels = state[8]
        relative = 0
        while relative < 8:
            index = page + relative
            if index < count:
                name = names[index]
                labels[offset + relative] = (
                    name if len(name) <= 12 else name[:12])
            else:
                labels[offset + relative] = None
            relative += 1
        if offset == 0:
            labels[16] = names
            labels[17] = page

    def _cache_footer(self):
        state = self._state
        count = len(state[1])
        state[9] = (str(state[2] + 1) + "/" + str(count)
                    if count else "")
        labels = state[8]
        labels[18] = state[2]
        labels[19] = count

    def _ensure_render_cache(self, page):
        state = self._state
        labels = state[8]
        if labels[16] is not state[1] or labels[17] != page:
            self._cache_labels(page, 0)
        count = len(state[1])
        if labels[18] != state[2] or labels[19] != count:
            self._cache_footer()

    def _start_cursor_motion(self, old_x, old_y):
        state = self._state
        position = self._position(state[2], state[3])
        target_x = position & 255
        target_y = position >> 8
        if old_x != target_x:
            old_x += 2 if target_x > old_x else -2
        if old_y != target_y:
            old_y += 2 if target_y > old_y else -2
        state[6] = time.ticks_ms()
        state[7] = self._pack_cursor(old_x, old_y, old_x, old_y)

    def _start_page_motion(self, old_page, direction):
        state = self._state
        self._ensure_render_cache(old_page)
        self._cache_labels(state[3], 8)
        state[6] = time.ticks_ms()
        state[7] = (_PAGE_MOTION | (old_page & 255)
                    | ((1 if direction > 0 else 0) << 8)
                    | (2 << 9))

    @property
    def motion_active(self):
        return self._state[6] >= 0

    def advance_motion(self, now):
        state = self._state
        started = state[6]
        if started < 0:
            return False
        elapsed = time.ticks_diff(now, started)
        if elapsed < 0:
            elapsed = 0
        packed = state[7]
        if packed & _PAGE_MOTION:
            if elapsed >= PICKER_PAGE_MS:
                self._snap_motion()
                return True
            remaining = PICKER_PAGE_MS - elapsed
            distance = (210 - 210 * remaining * remaining
                        // (PICKER_PAGE_MS * PICKER_PAGE_MS))
            if distance < 2:
                distance = 2
            state[7] = ((packed & ((1 << 9) - 1))
                        | _PAGE_MOTION | (distance << 9))
            return True

        start_x = packed & 255
        start_y = (packed >> 8) & 63
        old_x = (packed >> 14) & 255
        old_y = (packed >> 22) & 63
        position = self._position(state[2], state[3])
        target_x = position & 255
        target_y = position >> 8
        if elapsed >= PICKER_CURSOR_MS:
            self._snap_motion()
            return True
        remaining = PICKER_CURSOR_MS - elapsed
        denominator = PICKER_CURSOR_MS * PICKER_CURSOR_MS
        distance = target_x - start_x
        residual = abs(distance) * remaining * remaining // denominator
        current_x = (target_x - residual if distance >= 0
                     else target_x + residual)
        distance = target_y - start_y
        residual = abs(distance) * remaining * remaining // denominator
        current_y = (target_y - residual if distance >= 0
                     else target_y + residual)
        state[7] = self._pack_cursor(
            start_x, start_y, current_x, current_y)
        return current_x != old_x or current_y != old_y

    def activate(self):
        state = self._state
        if state[5] is not None:
            raise RuntimeError("Function picker scenario transaction is active")
        names = state[1]
        names[:] = ()
        for name in state[0].context.registry.keys():
            names.append(name)
        names.sort()
        state[2] = 0
        state[3] = 0
        state[4] = ""
        self._cache_labels(0, 0)
        self._cache_footer()
        self._snap_motion()

    def release_memory(self):
        state = self._state
        labels = state[8]
        released = bool(state[4] or state[9] or labels[16] is not None)
        state[2] = 0
        state[3] = 0
        state[4] = ""
        self._snap_motion()
        index = 0
        while index < 20:
            labels[index] = None
            index += 1
        labels[17] = -1
        labels[18] = -1
        labels[19] = -1
        state[9] = ""
        return released

    def open_scenario_transaction(self):
        from screens.function_picker_scenario import (
            FunctionPickerScenarioTransaction)
        return FunctionPickerScenarioTransaction(self)

    def _clamp(self):
        state = self._state
        count = len(state[1])
        if count:
            state[2] = max(0, min(state[2], count - 1))
            if not state[3] <= state[2] < state[3] + 8:
                state[3] = state[2] // 8 * 8
            state[3] = min(state[3], (count - 1) // 8 * 8)

    def _draw_page(self, display, page, x_offset, highlight,
                   paint_highlight=True, cache_offset=0):
        state = self._state
        names = state[1]
        labels = state[8]
        count = len(names)
        row = 0
        while row < 4:
            y = 15 + row * 10
            column = 0
            while column < 2:
                index = page + row + column * 4
                label = labels[cache_offset + row + column * 4]
                if index < count and label is not None:
                    selected = highlight and index == state[2]
                    x = (4 if column == 0 else 105) + x_offset
                    if selected and paint_highlight:
                        display.fill_rectangle(x, y, 96, 8, 12)
                    display.draw_text8x8(
                        x + 2, y, label, gs=0 if selected else 15)
                column += 1
            row += 1

    def draw(self, display):
        state = self._state
        names = state[1]
        _theme.draw_header_fast(display, "Functions", b"Functions", None)
        display.draw_rectangle(0, 13, self.width, 40, 15)
        self._clamp()
        count = len(names)
        if state[6] < 0:
            self._snap_motion()
        packed = state[7]
        begin = getattr(type(display), "begin_content_draw", None)
        end = getattr(type(display), "end_content_draw", None)
        if state[6] >= 0 and packed & _PAGE_MOTION:
            old_page = packed & 255
            forward = bool(packed & (1 << 8))
            distance = (packed >> 9) & 255
            old_offset = -distance if forward else distance
            new_offset = (self.width - distance if forward
                          else distance - self.width)
            if begin is None:
                self._draw_page(display, old_page, old_offset, False)
                self._draw_page(
                    display, state[3], new_offset, True,
                    cache_offset=8)
            else:
                begin(display, old_offset, 0, self.width)
                try:
                    self._draw_page(display, old_page, 0, False)
                finally:
                    end(display)
                begin(display, new_offset, 0, self.width)
                try:
                    self._draw_page(
                        display, state[3], 0, True,
                        cache_offset=8)
                finally:
                    end(display)
        else:
            self._ensure_render_cache(state[3])
            current_x = (packed >> 14) & 255
            current_y = (packed >> 22) & 63
            display.fill_rectangle(current_x, current_y, 96, 8, 12)
            position = self._position(state[2], state[3])
            target_x = position & 255
            target_y = position >> 8
            at_target = (abs(current_x - target_x) <= 2
                         and abs(current_y - target_y) <= 2)
            self._draw_page(
                display, state[3], 0, at_target, False)
            if end is not None and begin is not None:
                # A prior interrupted device frame must never leave clipping
                # active when the canonical page is redrawn.
                end(display)
        if not count:
            hint = "No functions"
            hint_bytes = b"No functions"
            right = ""
        elif state[4]:
            hint = "Input full"
            hint_bytes = b"Input full"
            right = state[9]
        else:
            hint = "ENT UP/DN 4/6"
            hint_bytes = b"ENT UP/DN 4/6"
            right = state[9]
        _theme.draw_footer_fast(display, hint, hint_bytes, None, right)

    def update(self, kb, event=None):
        state = self._state
        if state[5] is not None or event is None:
            return None
        row, col, _shift = event
        names = state[1]
        count = len(names)
        previous = state[2]
        previous_page = state[3]
        if state[6] >= 0:
            self._snap_motion()
        old_position = self._position(previous, previous_page)
        old_x = old_position & 255
        old_y = old_position >> 8
        if row == 3 and col == 1 and state[2] < count - 1:
            state[2] += 1
        elif row == 1 and col == 1 and state[2] > 0:
            state[2] -= 1
        elif row == 2 and col == 0 and state[2] >= 4:
            state[2] -= 4
        elif row == 2 and col == 2 and state[2] + 4 < count:
            state[2] += 4
        elif row == 3 and col == 3:
            if not names:
                return "FUNC_PICKER_DONE"
            name = names[state[2]]
            calc_screen = state[0]
            entry = calc_screen.context.registry.get(name)
            kind = entry[2] if entry else None
            text = (name + "(" if kind == "prefix" or kind == "list"
                    else name)
            if calc_screen.input_box.try_insert(text):
                state[4] = ""
                return "FUNC_PICKER_DONE"
            if state[4] == "Input full":
                return None
            state[4] = "Input full"
            return "REDRAW"
        elif row == 0 and col == 0:
            return "FUNC_PICKER_DONE"
        if state[2] != previous:
            target_page = state[2] // 8 * 8
            state[3] = target_page
            self._cache_footer()
            if target_page != previous_page:
                self._start_page_motion(
                    previous_page, 1 if target_page > previous_page else -1)
            else:
                self._start_cursor_motion(old_x, old_y)
            return "REDRAW"
        return None
