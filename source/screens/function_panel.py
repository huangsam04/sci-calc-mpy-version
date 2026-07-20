"""Function panel: toggle which function groups/files are active."""
from ui.element import UIElement
from ui.menu import Menu
from utils.storage import save_settings, load_settings
from ui.theme import draw_footer, draw_header


class FunctionPanel(UIElement):
    def __init__(self, font):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self.menu = Menu(0, 13, 210, 4, 10, font)
        self._items = []       # list of (name, is_on, is_group)
        self._toggled = {}     # Unsaved choices retained while rebuilding labels.
        self._dirty = False
        self._save_error = ""

    def activate(self):
        self._dirty = False
        self._save_error = ""
        self._toggled = {}
        self._refresh()
        self.menu.cursor_pos = 0
        self.menu.view_offset = 0
        self.menu.activate()

    def animation_children(self):
        return (self.menu,)

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
            setting_name = "plugin:" + name
            if setting_name in self._toggled:
                is_on = self._toggled[setting_name]
            else:
                is_on = setting_name in saved_enabled
            prefix = "[x]" if is_on else "[ ]"
            label = f"{prefix} {name}"
            self.menu.add_item(label, None)
            self._items.append((setting_name, is_on, False))

    def get_enabled_list(self):
        """Return list of enabled group/file names."""
        return [item[0] for item in self._items if item[1]]

    def _save(self):
        settings = load_settings()
        settings["enabled_functions"] = self.get_enabled_list()
        if save_settings(settings):
            self._dirty = False
            self._save_error = ""
            return True
        self._save_error = "Not saved - check SD"
        return False

    def draw(self, display):
        draw_header(display, "Functions", self.font)
        self.menu.draw(display)
        if self._save_error:
            draw_footer(display, self._save_error, self.font, "ESC retry")
        else:
            draw_footer(display, "ENT toggle", self.font, "ESC back")

    def update(self, kb, event=None):
        action = self.menu.update(kb, event)
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
            if self._dirty and not self._save():
                return None
            return "FUNC_PANEL_DONE"
        return None
