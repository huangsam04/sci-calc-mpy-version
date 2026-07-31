import time
from ui.element import SETTLE_REDRAW
from ui.inputbox import InputBox
from calc.functions import EvalContext
from calc.number import (DEFAULT_DISPLAY_DIGITS, MAX_DISPLAY_DIGITS,
                         MIN_DISPLAY_DIGITS, Number, format_number)
from calc.parser import evaluate, ParseError
from input.keyboard import get_key_label
from ui.motion import DAMAGE_FULL, DAMAGE_NONE, DAMAGE_PARTIAL
from ui.theme import (draw_footer_cached, draw_text, fit_text,
                      get_direct_text_draw, text_width)
from ui.error_popup import ErrorPopup

MAX_EXPRESSION_CHARS = 96
MAX_HISTORY_ENTRIES = 20
MAX_HISTORY_EXPRESSION_CHARS = 768
_INPUT_FOOTER_HINT = "ENT calc  Tab ~"
_HISTORY_FOOTER_HINT = "ENT ans  4 ins~"
_DAMAGE_FOOTER = 3


class CalculatorScreen:
    transition_title = "Calculator"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("input_box", "mode", "context", "_state")

    def __init__(self, font, small_font=None, registry=None, variables=None,
                 display_digits=DEFAULT_DISPLAY_DIGITS,
                 retained_state=None):
        # Construct child instance blocks before the four-key Calculator map.
        # MicroPython stores instance attributes in a growing hash table even
        # though CPython honours __slots__; keeping four keys and no retained
        # table wider than four references avoids one large contiguous block.
        # One fixed column-major table retains the four visible history rows:
        # the immutable history entry and its two rendered representations.
        # Four result x coordinates are packed into one state integer.  A row
        # keeps bytes for the production direct drawer or text for a
        # compatibility drawer, never both.
        # Cursor, view offset, five status scalars, scenario lease, presented
        # editor state, footer cache, history cache, and packed result x values
        # share this fixed outer table.
        font = font if font is not None else small_font
        if retained_state is None:
            input_box = InputBox(0, 0, 210, 12,
                                 MAX_EXPRESSION_CHARS, font,
                                 visible_rows=2)
            context = EvalContext(
                variables if variables is not None else {}, registry)
            history = []
            status = [0, "", DEFAULT_DISPLAY_DIGITS, ["", 0]]
            meta = [status, 0, 0, None]
        else:
            history = retained_state[0]
            input_box = retained_state[1]
            context = retained_state[2]
            meta = retained_state[3]
        error_popup = ErrorPopup(font, font)
        render = [
            [None] * 10,
            ["", b"", "", b"", 0, None, -1, -1, -1, None, None],
            None,
            0,
        ]
        state = (retained_state if retained_state is not None
                 else [history, None, None, meta])
        state[0] = history
        state[1] = error_popup
        state[2] = render
        state[3] = meta

        self.input_box = input_box
        self.mode = 0
        self.context = context
        self._state = state
        # Derived render tables stay absent until the inactive Calculator is
        # actually shown.
        if retained_state is None:
            self.set_display_digits(display_digits)
        self._clear_presented_editor_state()

    def activate(self):
        self._activate_visible_state()

    def _activate_visible_state(self):
        """Apply the small visible state change used by normal activation."""
        self.input_box.activate()
        self.mode = 0
        self._state[3][0][1] = ""
        self._clear_presented_editor_state()
        self._ensure_footer_cache()

    def deactivate(self):
        self._clear_presented_editor_state()

    def open_scenario_transaction(self):
        """Reserve this resident screen for one bounded diagnostic lease."""
        if self._state[3][3] is not None:
            raise RuntimeError("Calculator scenario transaction is already open")

        # The constructor allocates its fixed snapshot before this method
        # changes any visible or lossless Calculator state.
        from screens.calculator_scenario import CalculatorScenarioTransaction
        transaction = CalculatorScenarioTransaction(self)
        self._state[3][3] = transaction
        self._clear_history_cache()
        self.input_box.release_memory()
        self._clear_presented_editor_state()
        self._invalidate_footer_cache()
        self._state[1].dismiss()
        self.mode = 0
        self._state[3][0][1] = ""
        self.input_box.activate()
        return transaction

    def blocks_global_shortcuts(self):
        """An error popup owns every edge until it is dismissed."""
        return self.mode == 2

    def letter_input_target(self):
        """Expose the editor only while Calculator is in input context."""
        return self.input_box if self.mode == 0 else None

    def release_memory(self):
        """Release derived editor/error state while preserving 20-entry history."""
        released = self._clear_history_cache()
        render = self._state[2]
        if render[2] is not None:
            render[2] = None
            released = True
        if render[0] is not None:
            render[0] = None
            released = True
        if render[1] is not None:
            render[1] = None
            released = True
        if self._state[1].release_memory():
            released = True
        if self.input_box.release_memory():
            released = True
        return released

    def detach_state(self):
        """Move lossless state out while dropping Calculator-only objects."""
        state = self._state
        if state[3][3] is not None:
            raise RuntimeError("Calculator scenario transaction is active")
        self.deactivate()
        self.release_memory()
        state[1] = self.input_box
        state[2] = self.context
        self.input_box = None
        self.context = None
        self._state = None
        self.mode = 0
        return state

    def settle_step(self):
        """Expire quiet transient UI state without restoring periodic frames."""
        now = time.ticks_ms()
        changed = False
        presented = self._state[2][0]
        if (presented is not None and presented[0] is not None
                and presented[0] & 4):
            presented[0] = (presented[0] & ~4) | 8
            changed = True
        popup = self._state[1]
        if self.mode == 2 and popup.expired(now):
            self.mode = 0
            popup.dismiss()
            changed = True
        storage = self._state[3][0][3]
        if (storage[0]
                and time.ticks_diff(now, storage[1]) >= 5000):
            storage[0] = ""
            changed = True
        return SETTLE_REDRAW if changed else 0

    def _enter(self):
        expr = self.input_box.get_str().strip()
        if not expr:
            return
        history = self._state[0]
        try:
            result = evaluate(expr, self.context)
            # Failed input must not evict a valid record.  Only after a result
            # exists do we make room for the new lossless entry, keeping both
            # the count and total retained expression text bounded.
            history_chars = len(expr)
            for item in history:
                history_chars += len(item[0])
            while history and (
                    len(history) >= MAX_HISTORY_ENTRIES
                    or history_chars > MAX_HISTORY_EXPRESSION_CHARS):
                history_chars -= len(history[-1][0])
                history.pop()
            history.insert(0, (expr, result))
            self._clear_history_cache()
            self.input_box.clear_str()
        except MemoryError:
            # Let the runtime reclaim optional state at its single recovery
            # seam; a popup can require more allocations than remain.
            raise
        except ParseError as e:
            self._state[1].show(e.expr if e.expr else expr, e, e.pos)
            self.mode = 2
        except Exception as e:
            self._state[1].show(expr, e)
            self.mode = 2

    def _fmt(self, val):
        if isinstance(val, (Number, int, float)):
            return format_number(val, self._state[3][0][2])
        return str(val)

    def _history_literal(self, result):
        """Return the bounded, lossless input form for a numeric result.

        History stores result objects, never their OLED-rendered text.  Number
        literals retain all working digits while the display formatter remains
        free to round.  Other plug-in return values stay display-only so an
        arbitrary string is never silently treated as calculator source.
        """
        if isinstance(result, Number):
            return result.to_literal()
        if isinstance(result, bool):
            return None
        if isinstance(result, int):
            return Number(result).to_literal()
        if isinstance(result, float):
            try:
                return Number.from_float(result).to_literal()
            except (TypeError, ValueError, OverflowError):
                return None
        return None

    def _insert_history_text(self, text):
        """Insert a known fragment or keep history selection on failure."""
        status = self._state[3][0]
        if text is None:
            status[1] = "Result is display-only"
            return False
        if not self.input_box.try_insert(text):
            status[1] = "Input full"
            return False
        status[1] = ""
        self.mode = 0
        return True

    def _edit_history_expression(self, expression):
        """Replace the editor with an exact historical expression when it fits."""
        status = self._state[3][0]
        if len(expression) > ((self.input_box._state[0] >> 19) & 511):
            status[1] = "Input full"
            return False
        self.input_box.set_str(expression, immediate=True)
        self.input_box.move_cursor_end()
        status[1] = ""
        self.mode = 0
        return True

    def set_display_digits(self, digits):
        """Apply the user preference without changing stored calculation values."""
        if not isinstance(digits, int):
            digits = DEFAULT_DISPLAY_DIGITS
        digits = max(MIN_DISPLAY_DIGITS, min(MAX_DISPLAY_DIGITS, digits))
        status = self._state[3][0]
        if status[2] != digits:
            status[2] = digits
            self._clear_history_cache()

    def _refresh_panel_layout(self):
        """Apply editor height without allocating a layout tuple."""
        if self.input_box.active_rows > 1:
            input_height = 22
        else:
            input_height = 12
        self.input_box.set_height(input_height)

    def _clear_presented_editor_state(self):
        presented = self._state[2][0]
        if presented is None:
            return
        # Mode is the validity tag.  The input and storage strings are the
        # only retained rebuildable references; scalar fields are overwritten
        # by the next successful present.
        presented[0] = None
        presented[1] = None
        presented[9] = None

    def _invalidate_footer_cache(self):
        """Release dynamic footer text without rebuilding it during recovery."""
        footer = self._state[2][1]
        if footer is None:
            return
        footer[0] = ""
        footer[1] = b""
        footer[2] = ""
        footer[3] = b""
        # Cached mode is the validity tag; numeric layout fields are scalars
        # and are overwritten when the mode mismatch rebuilds the footer.
        footer[5] = None
        footer[9] = None
        footer[10] = None

    def _editor_damage_state(self):
        """Classify editor repainting without constructing a state tuple."""
        self._refresh_panel_layout()
        meta = self._state[3]
        presented = self._state[2][0]
        if presented is None:
            return DAMAGE_FULL
        if (self.mode != 0 or presented[0] is None
                or (presented[0] & 3) != 0):
            return DAMAGE_FULL
        if (self.input_box.height != presented[5]
                or len(self._state[0]) != presented[6]
                or meta[1] != presented[7]
                or meta[2] != presented[8]
                or meta[0][3][0] != presented[9]):
            return DAMAGE_FULL
        if (self.input_box.str == presented[1]
                and self.input_box.cursor_pos == presented[2]
                and self.input_box.cursor.x == presented[3]
                and self.input_box.cursor.y == presented[4]):
            return _DAMAGE_FOOTER if presented[0] & 8 else DAMAGE_NONE
        return DAMAGE_PARTIAL

    def collect_present_damage(self, damage):
        state = self._editor_damage_state()
        if state == DAMAGE_PARTIAL:
            damage.add(0, self.input_box.height)
            presented = self._state[2][0]
            presented[0] = (presented[0] & ~8) | 4
        elif state == _DAMAGE_FOOTER:
            damage.add(54, 10)
        return state

    def mark_presented(self):
        self._refresh_panel_layout()
        meta = self._state[3]
        render = self._state[2]
        presented = render[0]
        if presented is None:
            presented = [None] * 10
            render[0] = presented
        pending = (
            presented[0] & 12 if presented[0] is not None else 0)
        presented[0] = self.mode | pending
        presented[1] = self.input_box.str
        presented[2] = self.input_box.cursor_pos
        presented[3] = self.input_box.cursor.x
        presented[4] = self.input_box.cursor.y
        presented[5] = self.input_box.height
        presented[6] = len(self._state[0])
        presented[7] = meta[1]
        presented[8] = meta[2]
        presented[9] = meta[0][3][0]

    def _clamp_view(self, history_visible):
        meta = self._state[3]
        cursor = meta[1]
        view_offset = meta[2]
        max_off = max(0, len(self._state[0]) - history_visible)
        if cursor < view_offset:
            view_offset = cursor
        if cursor >= view_offset + history_visible:
            view_offset = cursor - history_visible + 1
        if view_offset > max_off:
            view_offset = max_off
        if view_offset < 0:
            view_offset = 0
        meta[2] = view_offset

    def _select_scenario_history_index(self, index):
        """Select one existing history row through the page-owned seam.

        The bounded acceptance transaction uses this instead of assigning the
        private cursor from its future controller.  Ordinary keyboard history
        navigation keeps its existing behavior and shares the same invariants.
        """
        if (not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(self._state[0])):
            return False
        self.mode = 1
        self._state[3][1] = index
        self._state[3][0][1] = ""
        self._clamp_view(3 if self.input_box.active_rows > 1 else 4)
        return True

    def _clear_history_cache(self):
        """Release the fixed rendered window without touching lossless history."""
        render = self._state[2]
        cache = render[2]
        if cache is None:
            render[3] = 0
            return False
        released = False
        index = 0
        while index < 12:
            if cache[index] is not None:
                released = True
            cache[index] = None
            index += 1
        render[3] = 0
        return released

    def _history_cache_matches(self, history, total, history_visible,
                               encoded):
        cache = self._state[2][2]
        if cache is None:
            return False
        index = self._state[3][2]
        slot = 0
        while slot < history_visible:
            if index < total:
                entry = history[index]
                # Calculator history entries are immutable tuples; replacing
                # an entry changes identity and invalidates its derived row.
                if (cache[slot] is not entry
                        or cache[4 + slot] is None
                        or cache[8 + slot] is None
                        or isinstance(cache[4 + slot], bytes) != encoded
                        or isinstance(cache[8 + slot], bytes) != encoded):
                    return False
            elif cache[slot] is not None:
                return False
            index += 1
            slot += 1
        while slot < 4:
            if cache[slot] is not None:
                return False
            slot += 1
        return True

    def _ensure_history_cache(self, history_visible, encoded=None):
        """Pre-fit and encode only the current 3–4 rendered history rows."""
        history = self._state[0]
        total = len(history)
        font = self.input_box.font
        render = self._state[2]
        if total == 0:
            self._clear_history_cache()
            render[2] = None
            return
        if encoded is None:
            encoded = font is not None
        if self._history_cache_matches(
                history, total, history_visible, encoded):
            return
        # Drop old derived strings before formatting new rows so a page switch
        # never keeps two rendered windows at its allocation peak.
        self._clear_history_cache()
        cache = render[2]
        if cache is None:
            cache = [None] * 12
            render[2] = cache
        packed_x = 0
        index = self._state[3][2]
        slot = 0
        while slot < history_visible:
            if index < total:
                entry = history[index]
                expr = entry[0]
                result = entry[1]
                result_text = fit_text("= " + self._fmt(result), 78, font)
                x = max(108, self.width - text_width(result_text, font) - 4)
                expr_text = fit_text(expr, max(24, x - 8), font)
                cache[slot] = entry
                if encoded:
                    expr_text = expr_text.encode()
                    result_text = result_text.encode()
                cache[4 + slot] = expr_text
                cache[8 + slot] = result_text
                packed_x |= x << (slot * 8)
            index += 1
            slot += 1
        render[3] = packed_x

    def _draw_cached_history_row(self, display, y, slot, selected, direct):
        if selected:
            display.fill_rectangle(2, y, 206, 8, 12)
        render = self._state[2]
        cache = render[2]
        font = self.input_box.font
        expr_text = cache[4 + slot]
        result_text = cache[8 + slot]
        result_x = (render[3] >> (slot * 8)) & 255
        if font and direct is not None:
            gs = 0 if selected else 15
            direct(display, 4, y, expr_text, font, gs=gs)
            direct(display, result_x, y, result_text, font, gs=gs)
            return
        draw_text(display, 4, y, expr_text, font,
                  gs=14 if selected else 15, invert=selected, raw=True)
        draw_text(display, result_x, y, result_text, font,
                  gs=14 if selected else 15, invert=selected, raw=True)

    def _draw_editor(self, display):
        self.input_box.y = 0
        self._refresh_panel_layout()
        self.input_box.cursor.is_visible = (self.mode == 0)
        self.input_box.draw(display)
        display.draw_hline(0, self.input_box.height + 1, 210, 8)

    def _ensure_footer_cache(self):
        """Keep one bounded footer cache current outside steady drawing."""
        input_len = len(self.input_box.str)
        history_len = len(self._state[0])
        meta = self._state[3]
        render = self._state[2]
        footer = render[1]
        if footer is None:
            footer = ["", b"", "", b"", 0, None,
                      -1, -1, -1, None, None]
            render[1] = footer
        font = self.input_box.font
        status = meta[0]
        storage_error = status[3][0]
        if (footer[5] == self.mode
                and footer[6] == input_len
                and footer[7] == history_len
                and footer[8] == meta[1]
                and footer[9] == status[1]
                and footer[10] == storage_error):
            return

        if storage_error:
            hint = fit_text(storage_error, 126, font)
            hint_bytes = hint.encode() if font else b""
            right = ""
        elif self.mode == 0:
            hint = _INPUT_FOOTER_HINT
            hint_bytes = hint.encode() if font else b""
            right = str(input_len) + "/" + str(MAX_EXPRESSION_CHARS)
        else:
            right = str(meta[1] + 1) + "/" + str(history_len)
            if status[1]:
                hint = fit_text(status[1], 126, font)
                hint_bytes = hint.encode() if font else b""
            else:
                hint = _HISTORY_FOOTER_HINT
                hint_bytes = hint.encode() if font else b""

        if right:
            right = fit_text(right, 76, font)
            right_bytes = right.encode() if font else b""
            right_x = max(130, self.width - text_width(right, font) - 2)
        else:
            right_bytes = b""
            right_x = 0

        footer[0] = hint
        footer[1] = hint_bytes
        footer[2] = right
        footer[3] = right_bytes
        footer[4] = right_x
        footer[5] = self.mode
        footer[6] = input_len
        footer[7] = history_len
        footer[8] = meta[1]
        footer[9] = status[1]
        footer[10] = storage_error

    def _draw_footer(self, display):
        self._ensure_footer_cache()
        footer = self._state[2][1]
        draw_footer_cached(
            display, footer[0], footer[1], self.input_box.font,
            footer[2], footer[3], footer[4])

    def _draw_footer_right(self, display):
        self._ensure_footer_cache()
        footer = self._state[2][1]
        display.fill_rectangle(130, 54, 80, 10, 0)
        display.draw_hline(130, 54, 80, 8)
        if not footer[2]:
            return
        font = self.input_box.font
        direct = get_direct_text_draw(display) if font else None
        if direct is not None and footer[3]:
            direct(display, footer[4], 56, footer[3], font, gs=15)
        else:
            draw_text(display, footer[4], 56, footer[2], font, 15, raw=True)

    def draw_present_rows(self, display):
        """Redraw only the rows declared by ``collect_present_damage``."""
        presented = self._state[2][0]
        if presented[0] & 8:
            self._draw_footer_right(display)
            presented[0] &= ~12
        else:
            self._draw_editor(display)

    def draw(self, display):
        if self.mode == 2:
            self._state[1].draw(display)
            presented = self._state[2][0]
            if presented[0] is not None:
                presented[0] &= ~12
            return

        # --- One-line editor that expands to two rows only when needed ---
        self._draw_editor(display)
        input_height = self.input_box.height
        hist_start_y = input_height + 3
        hist_visible = 3 if input_height == 22 else 4

        # --- History: four rows with a compact editor, three when expanded ---
        self._clamp_view(hist_visible)
        meta = self._state[3]
        history = self._state[0]
        direct = (get_direct_text_draw(display)
                  if self.input_box.font else None)
        self._ensure_history_cache(hist_visible, direct is not None)
        i = 0
        while i < hist_visible:
            hist_idx = meta[2] + i
            if hist_idx >= len(history):
                break
            y = hist_start_y + i * 9
            is_selected = (self.mode == 1 and hist_idx == meta[1])
            self._draw_cached_history_row(display, y, i, is_selected, direct)
            i += 1

        # --- Status line (y=55..63) ---
        self._draw_footer(display)
        presented = self._state[2][0]
        if presented[0] is not None:
            presented[0] &= ~12

    def update(self, kb, event=None):
        meta = self._state[3]
        status = meta[0]
        history = self._state[0]
        if self.mode == 2:
            if event is not None:
                self.mode = 0
                self._state[1].dismiss()
                return "REDRAW"
            return None

        # Long-hold ESC: go back
        if kb.consume_long_press(0, 0, 1000):
            return "BACK"

        if self.mode == 0:
            action = self.input_box.update(kb, event)
            if action in ("MOVE", "CHANGE"):
                return "REDRAW"
            if action == "ENT":
                if event is not None and event[2]:
                    return ("REDRAW" if self.input_box.insert_str("=")
                            else None)
                else:
                    if self.input_box.get_str():
                        self._enter()
                        return "REDRAW"
            elif action == "tab":
                if history:
                    self.mode = 1
                    meta[1] = 0
                    meta[2] = 0
                    status[1] = ""
                    return "REDRAW"
            elif action == "stab":
                return "VARIABLE_PANEL"
            elif action == "ESC":
                # Guard: ignore ESC within 500ms of leaving history mode
                if time.ticks_diff(time.ticks_ms(), status[0]) < 500:
                    return None
                if self.input_box.get_str():
                    self.input_box.clear_str()
                    return "REDRAW"
                else:
                    return "BACK"
            elif action == "rpn":
                if event is None or not event[2]:
                    return "FUNC_PICKER"
            elif action == "DELETE":
                # Repeated DEL has no new edge event; explicitly request a frame.
                return "REDRAW"
        else:
            # History nav mode
            if event is None:
                return None

            r, c, shift = event
            label = get_key_label(r, c, shift)
            changed = False

            if label in ("2", "down"):
                if meta[1] < len(history) - 1:
                    meta[1] += 1
                    status[1] = ""
                    changed = True
            elif label in ("8", "up"):
                if meta[1] > 0:
                    meta[1] -= 1
                    status[1] = ""
                    changed = True
            elif label == "ENT":
                # Append only an exact numeric literal to existing input.
                if history:
                    _, result = history[meta[1]]
                    previous_notice = status[1]
                    changed = (self._insert_history_text(
                        self._history_literal(result))
                        or status[1] != previous_notice)
            # Physical 4 inserts the expression; physical 6 opens it for edit.
            elif r == 2 and c == 0:
                if history:
                    expr_str, _ = history[meta[1]]
                    previous_notice = status[1]
                    changed = (self._insert_history_text(expr_str)
                        or status[1] != previous_notice)
            elif r == 2 and c == 2:
                if history:
                    expr_str, _ = history[meta[1]]
                    previous_notice = status[1]
                    changed = (self._edit_history_expression(expr_str)
                        or status[1] != previous_notice)
            elif label == "tab":
                self.mode = 0
                status[1] = ""
                changed = True
            elif label == "stab":
                return "VARIABLE_PANEL"
            elif label == "ESC":
                self.mode = 0
                status[1] = ""
                status[0] = time.ticks_ms()
                changed = True

            return "REDRAW" if changed else None

        return None

    @property
    def vars(self):
        return self.context.variables

    def set_storage_error(self, message):
        storage = self._state[3][0][3]
        storage[0] = message
        storage[1] = time.ticks_ms()
