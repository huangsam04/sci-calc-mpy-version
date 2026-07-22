"""Function panel: toggle which function groups/files are active."""
from ui.element import UIElement
from ui.menu import Menu
from utils.storage import load_settings
from ui.theme import draw_footer, draw_header


class FunctionPanel(UIElement):
    def __init__(self, font, request_settings=None, settings=None):
        super().__init__(0, 0, 210, 64)
        self.font = font
        self._request_settings = request_settings
        self._settings = settings
        self.menu = Menu(0, 13, 210, 4, 10, font)
        self._items = []       # list of (name, is_on, is_group)
        self._toggled = {}     # Unsaved choices retained while rebuilding labels.
        self._dirty = False
        self._save_error = ""
        self._load_error = ""
        self._load_error_detail = ""
        self._plugin_functions = {}
        self._plugin_files = ()
        # Plugin inspection executes arbitrary add-on source in an isolated
        # registry. Do it while the boot progress screen is visible, never in
        # the navigation path that starts a page slide.
        self._load_plugin_catalog()

    def _load_plugin_catalog(self):
        from calc.loader import describe_function_files, list_function_files
        self._plugin_files = list_function_files()
        self._plugin_functions = describe_function_files()

    def activate(self):
        self._dirty = False
        self._toggled = {}
        self._refresh()
        if not self._focus_load_error():
            self.menu.cursor_pos = 0
            self.menu.view_offset = 0
        self.menu.activate()

    def animation_children(self):
        return (self.menu,)

    def deactivate(self):
        if self._dirty:
            self._queue_save()

    def set_load_errors(self, errors):
        """Keep the first failed plugin visible until the user acknowledges it."""
        if errors:
            self._load_error, self._load_error_detail = errors[0]
            self._focus_load_error()
        else:
            self._load_error = ""
            self._load_error_detail = ""

    def _focus_load_error(self):
        target = "plugin:" + self._load_error
        for index, item in enumerate(self._items):
            if item[0] == target:
                self.menu.cursor_pos = index
                self.menu._clamp_view()
                return True
        return False

    def _refresh(self):
        """Rebuild menu items. Uses _toggled for session state, settings for defaults."""
        from calc.functions import (FUNCTION_GROUPS, FUNCTION_GROUP_LABELS,
                                    DEFAULT_ENABLED_GROUPS)
        settings = self._settings
        if settings is None:
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
            display_name = FUNCTION_GROUP_LABELS.get(group_name, group_name)
            label = f"{prefix} {display_name} ({', '.join(func_names[:3])}...)"
            self.menu.add_item(label, None)
            self._items.append((group_name, is_on, True))

        # --- SD card files ---
        for name, filename in self._plugin_files:
            setting_name = "plugin:" + name
            if setting_name in self._toggled:
                is_on = self._toggled[setting_name]
            else:
                is_on = setting_name in saved_enabled
            prefix = "[x]" if is_on else "[ ]"
            label = f"{prefix} Add-on: {name}"
            function_names = self._plugin_functions.get(name, ())
            if function_names:
                # Add-on labels include a longer fixed prefix than built-ins;
                # two names keep the common trig add-on within the menu width.
                summary = ", ".join(function_names[:2])
                if len(function_names) > 2:
                    summary += "..."
                label += " (" + summary + ")"
            self.menu.add_item(label, None)
            self._items.append((setting_name, is_on, False))

    def get_enabled_list(self):
        """Return list of enabled group/file names."""
        return [item[0] for item in self._items if item[1]]

    def _queue_save(self):
        settings = self._settings
        if settings is None:
            settings = load_settings()
        settings["enabled_functions"] = self.get_enabled_list()
        if self._request_settings is None:
            self._save_error = "Not saved - check SD"
            return False
        self._request_settings(settings, self._on_save_result)
        self._dirty = False
        self._save_error = ""
        return True

    def _on_save_result(self, success):
        self._save_error = "" if success else "Not saved - check SD"

    def draw(self, display):
        title = "Plugin: " + self._load_error if self._load_error else "Functions"
        draw_header(display, title, self.font)
        self.menu.draw(display)
        if self._load_error:
            draw_footer(display, self._load_error_detail, self.font, "ENT off")
        elif self._save_error:
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
                if name == "plugin:" + self._load_error and is_on:
                    self._load_error = ""
                    self._load_error_detail = ""
        elif action == "BACK":
            if self._load_error:
                self._load_error = ""
                self._load_error_detail = ""
                return "FUNC_PANEL_CANCEL"
            if self._dirty and not self._queue_save():
                return None
            return "FUNC_PANEL_DONE"
        return None
