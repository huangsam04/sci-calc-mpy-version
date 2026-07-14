"""Function panel: toggle which function groups/files are active."""
from ui.element import UIElement
from ui.menu import Menu
from utils.storage import save_settings, load_settings


class FunctionPanel(UIElement):
    def __init__(self, font):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.menu = Menu(0, 13, 210, 4, 10, font)
        self._items = []       # list of (name, is_on, is_group)
        self._toggled = {}     # ponytail: session overrides so _refresh doesn't undo toggles
        self._dirty = False

    def activate(self):
        self._dirty = False
        self._toggled = {}
        self._refresh()
        self.menu.cursor_pos = 0
        self.menu.view_offset = 0
        self.menu.activate()

    def deactivate(self):
        if self._dirty:
            self._save()

    def _refresh(self):
        """Rebuild menu items. Uses _toggled for session state, settings for defaults."""
        from calc.functions import FUNCTION_GROUPS, DEFAULT_ENABLED_GROUPS
        from calc.loader import list_function_files

        settings = load_settings()
        saved_enabled = settings.get("enabled_functions", DEFAULT_ENABLED_GROUPS)

        self.menu.clear_items()
        self._items = []

        # --- Built-in groups ---
        for group_name in DEFAULT_ENABLED_GROUPS:
            if group_name not in FUNCTION_GROUPS:
                continue
            # Session toggle overrides saved settings
            if group_name in self._toggled:
                is_on = self._toggled[group_name]
            else:
                is_on = group_name in saved_enabled
            func_names = FUNCTION_GROUPS[group_name]
            prefix = "[x]" if is_on else "[ ]"
            label = f"{prefix} {group_name} ({', '.join(func_names[:3])}...)"
            self.menu.add_item(label, None)
            self._items.append((group_name, is_on, True))

        # --- SD card files ---
        sd_files = list_function_files()
        for name, filename in sd_files:
            if name in self._toggled:
                is_on = self._toggled[name]
            else:
                is_on = name in saved_enabled
            prefix = "[x]" if is_on else "[ ]"
            label = f"{prefix} {name}"
            self.menu.add_item(label, None)
            self._items.append((name, is_on, False))

    def get_enabled_list(self):
        """Return list of enabled group/file names."""
        return [item[0] for item in self._items if item[1]]

    def _save(self):
        settings = load_settings()
        settings["enabled_functions"] = self.get_enabled_list()
        save_settings(settings)
        self._dirty = False

    def draw(self, display):
        if self.font:
            display.draw_text(2, 1, "Functions", self.font, gs=15)
        else:
            display.draw_text8x8(2, 1, "Functions", gs=15)
        display.draw_hline(0, 11, 210, 15)
        self.menu.draw(display)
        hint = "ENT:toggle  ESC:back"
        if self.font:
            display.draw_text(2, 55, hint, self.font, gs=15)
        else:
            display.draw_text8x8(2, 55, hint, gs=15)

    def update(self, kb):
        action = self.menu.update(kb)
        if action == "ENTER":
            idx = self.menu.cursor_pos
            if 0 <= idx < len(self._items):
                name, is_on, is_group = self._items[idx]
                # Store in session toggle dict so _refresh preserves it
                self._toggled[name] = not is_on
                self._dirty = True
                self._refresh()
                # Restore cursor position
                self.menu.cursor_pos = min(idx, len(self._items) - 1)
                self.menu._clamp_view()
                self.menu._update_cursor_target()
        elif action == "BACK":
            if self._dirty:
                self._save()
            return "FUNC_PANEL_DONE"
        return None
