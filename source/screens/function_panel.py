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
        self._plugin_dependencies = {}
        self._plugin_files = ()
        self._dependency_notice = ""
        # Plugin inspection executes arbitrary add-on source in an isolated
        # registry. Do it while the boot progress screen is visible, never in
        # the navigation path that starts a page slide.
        self._load_plugin_catalog()

    def _load_plugin_catalog(self):
        """Inspect add-ons outside the normal page-transition path."""
        from calc.loader import (describe_function_files,
                                 describe_plugin_dependencies,
                                 list_function_files)
        self._plugin_files = list_function_files()
        self._plugin_functions = describe_function_files()
        self._plugin_dependencies = describe_plugin_dependencies(
            files=self._plugin_files)

    def _reload_plugin_catalog(self):
        """Explicitly rescan SD add-ons while preserving the selected item."""
        selected = None
        if 0 <= self.menu.cursor_pos < len(self._items):
            selected = self._items[self.menu.cursor_pos][0]
        self._load_plugin_catalog()
        self._ensure_all_enabled_dependencies()
        self._refresh()
        if selected is not None:
            for index, item in enumerate(self._items):
                if item[0] == selected:
                    self.menu.cursor_pos = index
                    break
        # A removed add-on can leave the old cursor past the rebuilt menu.
        # Clamp before Menu recalculates its view and animation target.
        self.menu.cursor_pos = max(
            0, min(self.menu.cursor_pos, len(self._items) - 1))
        self.menu._clamp_view()
        self.menu._update_cursor_target()

    def activate(self):
        self._dirty = False
        self._toggled = {}
        self._dependency_notice = ""
        self._ensure_all_enabled_dependencies()
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

    def _settings_value(self):
        settings = self._settings
        return settings if settings is not None else load_settings()

    def _saved_enabled(self):
        from calc.functions import DEFAULT_ENABLED_GROUPS
        return self._settings_value().get("enabled_functions", DEFAULT_ENABLED_GROUPS)

    def _is_enabled(self, setting_name):
        if setting_name in self._toggled:
            return self._toggled[setting_name]
        return setting_name in self._saved_enabled()

    def _plugin_name(self, setting_name):
        return setting_name[7:] if setting_name.startswith("plugin:") else setting_name

    def _dependency_settings(self, plugin_name):
        return ["plugin:" + self._plugin_name(name)
                for name in self._plugin_dependencies.get(plugin_name, ())]

    def _enable_dependencies(self, plugin_names):
        """Enable the complete available dependency closure for selected add-ons."""
        available = set(name for name, _ in self._plugin_files)
        auto_enabled = []
        missing = []
        visited = set()

        def visit(plugin_name):
            if plugin_name in visited:
                return
            visited.add(plugin_name)
            for setting_name in self._dependency_settings(plugin_name):
                dependency = self._plugin_name(setting_name)
                if dependency not in available:
                    if dependency not in missing:
                        missing.append(dependency)
                    continue
                if not self._is_enabled(setting_name):
                    self._toggled[setting_name] = True
                    auto_enabled.append(dependency)
                visit(dependency)

        for plugin_name in plugin_names:
            visit(plugin_name)
        return auto_enabled, missing

    def _ensure_all_enabled_dependencies(self):
        enabled = []
        for name, _ in self._plugin_files:
            if self._is_enabled("plugin:" + name):
                enabled.append(name)
        auto_enabled, missing = self._enable_dependencies(enabled)
        if auto_enabled:
            self._dirty = True
            self._dependency_notice = "Auto on: " + ", ".join(auto_enabled)
        elif missing:
            self._dependency_notice = "Missing dependency: " + ", ".join(missing)
        return auto_enabled, missing

    def _disable_dependents(self, plugin_name):
        """Turning off a dependency also turns off enabled dependents."""
        disabled = []
        pending = [plugin_name]
        while pending:
            dependency = pending.pop()
            for candidate, _ in self._plugin_files:
                setting_name = "plugin:" + candidate
                if not self._is_enabled(setting_name):
                    continue
                required = [self._plugin_name(item)
                            for item in self._plugin_dependencies.get(candidate, ())]
                if dependency in required:
                    self._toggled[setting_name] = False
                    disabled.append(candidate)
                    pending.append(candidate)
        return disabled

    def _refresh(self):
        """Rebuild menu items. Uses _toggled for session state, settings for defaults."""
        from calc.functions import (FUNCTION_GROUPS, FUNCTION_GROUP_LABELS,
                                    DEFAULT_ENABLED_GROUPS)

        self.menu.clear_items()
        self._items = []

        # --- Built-in groups ---
        for group_name in DEFAULT_ENABLED_GROUPS:
            if group_name not in FUNCTION_GROUPS:
                continue
            # Session toggle overrides saved settings
            is_on = self._is_enabled(group_name)
            func_names = FUNCTION_GROUPS[group_name]
            prefix = "[x]" if is_on else "[ ]"
            display_name = FUNCTION_GROUP_LABELS.get(group_name, group_name)
            label = f"{prefix} {display_name} ({', '.join(func_names[:3])}...)"
            self.menu.add_item(label, None)
            self._items.append((group_name, is_on, True))

        # --- SD card files ---
        for name, filename in self._plugin_files:
            setting_name = "plugin:" + name
            is_on = self._is_enabled(setting_name)
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
        settings = self._settings_value()
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
        elif self._dependency_notice:
            draw_footer(display, self._dependency_notice, self.font, "ESC back")
        else:
            draw_footer(display, "ENT toggle Sh+ENT reload", self.font,
                        "ESC back")

    def update(self, kb, event=None):
        action = self.menu.update(kb, event)
        if action == "ENTER":
            if event is not None and event[2]:
                # Plugin inspection executes SD source. Keep that work behind
                # an explicit command so entering this screen stays responsive.
                self._reload_plugin_catalog()
                return None
            idx = self.menu.cursor_pos
            if 0 <= idx < len(self._items):
                name, is_on, is_group = self._items[idx]
                # Store in session toggle dict so _refresh preserves it
                self._toggled[name] = not is_on
                self._dirty = True
                self._dependency_notice = ""
                if not is_group:
                    plugin_name = self._plugin_name(name)
                    if not is_on:
                        auto_enabled, missing = self._enable_dependencies(
                            [plugin_name])
                        if auto_enabled:
                            self._dependency_notice = (
                                "Auto on: " + ", ".join(auto_enabled))
                        elif missing:
                            self._dependency_notice = (
                                "Missing dependency: " + ", ".join(missing))
                    else:
                        disabled = self._disable_dependents(plugin_name)
                        if disabled:
                            self._dependency_notice = (
                                "Disabled dependents: " + ", ".join(disabled))
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
