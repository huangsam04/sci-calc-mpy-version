# Function panel: toggle which function groups/files are active.
from ui.menu import Menu
from ui.motion import DAMAGE_FULL
from ui import theme as _theme


_BUILTIN_GROUP_DETAILS = {
    "basic": "Basic (+ - * / ...)",
    "trig": "Trig (sin cos tan)",
    "math": "Science (sqrt ln exp)",
    "list": "Lists (max min ...)",
}


def _update_plugin_dependencies(panel, name, is_on):
    plugin_name = name[7:]
    if not is_on:
        auto_enabled, missing = panel._enable_dependencies([plugin_name])
        if auto_enabled:
            panel._state[1][2] = "Auto on: " + ", ".join(auto_enabled)
        elif missing:
            panel._state[1][2] = (
                "Missing dependency: " + ", ".join(missing))
        return
    disabled = panel._disable_dependents(plugin_name)
    if disabled:
        panel._state[1][2] = (
            "Disabled dependents: " + ", ".join(disabled))


def _toggle_current(panel):
    idx = panel.menu.cursor_pos
    if idx < 0 or idx >= len(panel._items):
        return None
    name, is_on, is_group = panel._items[idx]
    panel._state[0][2][name] = not is_on
    panel._flags |= 1
    panel._state[1][2] = ""
    if not is_group:
        _update_plugin_dependencies(panel, name, is_on)
    panel._refresh()
    panel.menu.cursor_pos = min(idx, len(panel._items) - 1)
    panel.menu._clamp_view()
    panel.menu._update_cursor_target()
    load_error = panel._state[1][1]
    if (load_error is not None
            and name == "plugin:" + load_error[0] and is_on):
        panel._state[1][1] = None
    return "REDRAW"


class FunctionPanel:
    transition_title = "Functions"
    x = 0
    y = 0
    width = 210
    height = 64

    __slots__ = ("_menu", "_items", "_flags", "_state")

    def __init__(self, request_settings, settings, plugin_dependencies=None,
                 plugin_files=()):
        self._menu = None
        self._items = ()       # list of (name, is_on, is_group) while active
        # Bits: dirty=1, pending key missing=2, menu built=4,
        # plugin scan active=8, persistence visual dirty=16, reload active=32.
        self._flags = 0
        # Fixed tables keep the instance block at four keys. Each retained
        # table is no wider than the allocations already proven at boot.
        self._state = (
            [request_settings, settings, {}, None],
            ["", None, "", None],
            [plugin_dependencies, plugin_files],
        )

    @property
    def menu(self):
        if self._menu is None:
            self._menu = Menu(0, 13, 210, 4, 10)
        return self._menu

    def _reserve_menu_rows(self):
        """Reserve the exact bounded row lists before dynamic label work."""
        from calc.functions import FUNCTION_GROUPS, DEFAULT_ENABLED_GROUPS

        row_count = len(self._state[2][1])
        for group_name in DEFAULT_ENABLED_GROUPS:
            if group_name in FUNCTION_GROUPS:
                row_count += 1
        menu = self.menu
        if not isinstance(self._items, list) or len(self._items) != row_count:
            self._items = [None] * row_count
        if (not isinstance(menu._state[5], list)
                or len(menu._state[5]) != row_count):
            menu._state[5] = [None] * row_count
        return menu

    def set_plugin_catalog(self, dependencies):
        self._state[2][0] = dependencies

    def set_plugin_scan_active(self, active):
        active = bool(active)
        if active == bool(self._flags & 8):
            return False
        if active:
            self._flags |= 8
        else:
            self._flags &= ~8
        if self._menu is not None:
            self._menu.invalidate_presented()
        return True

    def set_plugin_reload_active(self, active):
        active = bool(active)
        if active == bool(self._flags & 32):
            return False
        if active:
            self._flags |= 32
        else:
            self._flags &= ~32
        if self._menu is not None:
            self._menu.invalidate_presented()
        return True

    def adopt_plugin_catalog(self, report):
        selected = None
        menu = self._menu
        if menu is not None and 0 <= menu.cursor_pos < len(self._items):
            selected = self._items[menu.cursor_pos][0]
        self._state[2][1] = report.files
        self.set_plugin_catalog(report.dependencies)
        self._ensure_all_enabled_dependencies()
        if menu is not None:
            self._refresh()
            if selected is not None:
                for index, item in enumerate(self._items):
                    if item[0] == selected:
                        self.menu.cursor_pos = index
                        break
            self.menu.cursor_pos = max(
                0, min(self.menu.cursor_pos, len(self._items) - 1))
            self.menu._clamp_view()
            self.menu._update_cursor_target()
        else:
            # A quiet scan may finish after this page was released on back.
            # Keep report metadata only; rebuilding Menu would retain labels
            # and encoded text while the panel is invisible.
            self._flags &= ~4
        self.set_load_errors(report.errors)

    def adopt_plugin_files(self, files):
        """Adopt one bounded filename refresh without executing add-ons."""
        self._state[2][1] = files
        if self._menu is not None:
            cursor_pos = self._menu.cursor_pos
            view_offset = self._menu.view_offset
            self._refresh()
            self.menu.cursor_pos = max(
                0, min(cursor_pos, len(self._items) - 1))
            self.menu.view_offset = view_offset
            self.menu._clamp_view()
            self.menu._update_cursor_target()
        else:
            self._flags &= ~4

    def activate(self):
        if self._state[1][3] is not None:
            raise RuntimeError("Function panel scenario transaction is active")
        self._flags &= ~1
        self._state[0][2].clear()
        self._state[1][2] = ""
        auto_enabled, _ = self._ensure_all_enabled_dependencies()
        if not self._flags & 4 or auto_enabled:
            self._refresh()
        if not self._focus_load_error():
            self.menu.cursor_pos = 0
            self.menu.view_offset = 0
        self.menu.activate()

    def collect_present_damage(self, damage):
        if self._menu is None:
            return DAMAGE_FULL
        return self._menu.collect_present_damage(self.height, damage)

    def mark_presented(self):
        if self._menu is not None:
            self._menu.mark_presented()

    def draw_present_rows(self, display):
        if self._menu is None:
            return
        self._menu.draw_present_rows(display)

    @property
    def motion_active(self):
        return self._menu is not None and self._menu.motion_active

    def advance_motion(self, now):
        return (self._menu is not None
                and self._menu.advance_motion(now))

    def deactivate(self):
        self.set_plugin_scan_active(False)
        self.set_plugin_reload_active(False)
        if self._flags & 1:
            self._queue_save()

    def settle_step(self):
        return 0

    def release_memory(self):
        # Drop rebuildable labels while preserving selection and status state.
        if self._state[1][3] is not None:
            raise RuntimeError("Function panel scenario transaction is active")
        released = bool(self._items or self._menu)
        self._items = ()
        self._menu = None
        self._flags &= ~4
        return released

    def open_scenario_transaction(self):
        if self._state[1][3] is not None:
            raise RuntimeError("Function panel scenario transaction is already active")
        from screens.function_panel_scenario import (
            FunctionPanelScenarioTransaction)
        return FunctionPanelScenarioTransaction(self)

    def set_load_errors(self, errors):
        previous_error = self._state[1][1]
        previous_cursor = self._menu.cursor_pos if self._menu is not None else -1
        previous_offset = self._menu.view_offset if self._menu is not None else -1
        if errors:
            self._state[1][1] = errors[0]
            self._focus_load_error()
        else:
            self._state[1][1] = None
        changed = self._state[1][1] != previous_error
        if self._menu is not None and (changed
                                       or self._menu.cursor_pos != previous_cursor
                                       or self._menu.view_offset != previous_offset):
            self._menu.invalidate_presented()
        return changed

    def open_plugin_reload_checkpoint(self, settings=None, selection=None):
        # The loader owns this low-frequency checkpoint and is released again.
        from calc.plugin_reload import open_function_panel_reload_checkpoint
        return open_function_panel_reload_checkpoint(
            self, settings, selection)

    def confirm_plugin_reload(self):
        self._state[0][3] = None
        self._flags &= ~2

    def rollback_plugin_reload(self):
        previous = self._state[0][3]
        was_missing = bool(self._flags & 2)
        if previous is None and not was_missing:
            return False
        settings = self._settings_value()
        if was_missing:
            if "enabled_functions" in settings:
                del settings["enabled_functions"]
        else:
            settings["enabled_functions"] = previous
        self._state[0][3] = None
        self._flags &= ~2
        if self._state[0][2] is not None:
            self._state[0][2].clear()
        self._state[1][2] = ""
        self._flags &= ~4
        return True

    def _focus_load_error(self):
        if self._menu is None:
            return False
        load_error = self._state[1][1]
        if load_error is None:
            return False
        target = "plugin:" + load_error[0]
        for index, item in enumerate(self._items):
            if item[0] == target:
                self.menu.cursor_pos = index
                self.menu._clamp_view()
                self.menu._update_cursor_target()
                return True
        return False

    def _settings_value(self):
        return self._state[0][1]

    def _is_enabled(self, setting_name):
        overrides = self._state[0][2]
        if overrides is not None and setting_name in overrides:
            return overrides[setting_name]
        from calc.functions import DEFAULT_ENABLED_GROUPS
        return setting_name in self._settings_value().get(
            "enabled_functions", DEFAULT_ENABLED_GROUPS)

    def _enable_dependencies(self, plugin_names):
        # Enable the complete available dependency closure.
        available = set(name for name, _ in self._state[2][1])
        dependencies = self._state[2][0]
        auto_enabled = []
        missing = []
        visited = set()

        def visit(plugin_name):
            if plugin_name in visited:
                return
            visited.add(plugin_name)
            required = (() if dependencies is None
                        else dependencies.get(plugin_name, ()))
            for dependency in required:
                if dependency.startswith("plugin:"):
                    dependency = dependency[7:]
                setting_name = "plugin:" + dependency
                if dependency not in available:
                    if dependency not in missing:
                        missing.append(dependency)
                    continue
                if not self._is_enabled(setting_name):
                    if self._state[0][2] is None:
                        self._state[0][2] = {}
                    self._state[0][2][setting_name] = True
                    auto_enabled.append(dependency)
                visit(dependency)

        for plugin_name in plugin_names:
            visit(plugin_name)
        return auto_enabled, missing

    def _ensure_all_enabled_dependencies(self):
        dependencies = self._state[2][0]
        if not dependencies:
            return (), ()
        has_dependencies = False
        for name, _ in self._state[2][1]:
            if dependencies.get(name, ()):
                has_dependencies = True
                break
        if not has_dependencies:
            return (), ()
        enabled = []
        for name, _ in self._state[2][1]:
            if self._is_enabled("plugin:" + name):
                enabled.append(name)
        auto_enabled, missing = self._enable_dependencies(enabled)
        if auto_enabled:
            self._flags |= 1
            self._state[1][2] = "Auto on: " + ", ".join(auto_enabled)
        elif missing:
            self._state[1][2] = "Missing dependency: " + ", ".join(missing)
        return auto_enabled, missing

    def _disable_dependents(self, plugin_name):
        # Turning off a dependency also turns off enabled dependents.
        disabled = []
        pending = [plugin_name]
        dependencies = self._state[2][0]
        while pending:
            dependency = pending.pop()
            for candidate, _ in self._state[2][1]:
                setting_name = "plugin:" + candidate
                if not self._is_enabled(setting_name):
                    continue
                declared = (() if dependencies is None
                            else dependencies.get(candidate, ()))
                required = [item[7:] if item.startswith("plugin:") else item
                            for item in declared]
                if dependency in required:
                    if self._state[0][2] is None:
                        self._state[0][2] = {}
                    self._state[0][2][setting_name] = False
                    disabled.append(candidate)
                    pending.append(candidate)
        return disabled

    def _refresh(self):
        # Rebuild from session toggles over the persisted defaults.
        from calc.functions import (FUNCTION_GROUPS, FUNCTION_GROUP_LABELS,
                                    DEFAULT_ENABLED_GROUPS)

        menu = self._reserve_menu_rows()
        items = self._items
        row = 0
        menu.invalidate_presented()
        menu._state[6] = 0

        # --- Built-in groups ---
        for group_name in DEFAULT_ENABLED_GROUPS:
            if group_name not in FUNCTION_GROUPS:
                continue
            # Session toggle overrides saved settings
            is_on = self._is_enabled(group_name)
            prefix = "[x]" if is_on else "[ ]"
            display_name = _BUILTIN_GROUP_DETAILS.get(
                group_name,
                FUNCTION_GROUP_LABELS.get(group_name, group_name))
            label = f"{prefix} {display_name}"
            menu._state[5][row] = (label, None)
            items[row] = (group_name, is_on, True)
            row += 1

        # --- SD card files ---
        for name, filename in self._state[2][1]:
            setting_name = "plugin:" + name
            is_on = self._is_enabled(setting_name)
            prefix = "[x]" if is_on else "[ ]"
            label = f"{prefix} Add-on: {name}"
            menu._state[5][row] = (label, None)
            items[row] = (setting_name, is_on, False)
            row += 1
        self._flags |= 4

    def get_enabled_list(self):
        return [item[0] for item in self._items if item[1]]

    def _queue_save(self):
        from calc.limits import MAX_ENABLED_FUNCTIONS, MAX_ENABLED_PLUGINS
        settings = self._settings_value()
        enabled = self.get_enabled_list()
        plugin_count = 0
        for name in enabled:
            if name.startswith("plugin:"):
                plugin_count += 1
        if (len(enabled) > MAX_ENABLED_FUNCTIONS
                or plugin_count > MAX_ENABLED_PLUGINS):
            if self._state[1][0] != "Add-on limit reached":
                self._state[1][0] = "Add-on limit reached"
                if self._menu is not None:
                    self._menu.invalidate_presented()
            return False
        if self._state[0][0] is None:
            if self._state[1][0] != "Not saved - check SD":
                self._state[1][0] = "Not saved - check SD"
                if self._menu is not None:
                    self._menu.invalidate_presented()
            return False
        if "enabled_functions" not in settings:
            self._flags |= 2
        else:
            self._flags &= ~2
        self._state[0][3] = settings.get("enabled_functions")
        settings["enabled_functions"] = enabled
        self._state[0][0](settings, self._on_save_result, self)
        self._flags &= ~1
        if self._state[1][0]:
            self._state[1][0] = ""
            if self._menu is not None:
                self._menu.invalidate_presented()
        return True

    def _on_save_result(self, success):
        message = "" if success else "Not saved - check SD"
        if message != self._state[1][0]:
            self._state[1][0] = message
            if self._menu is not None:
                self._menu.invalidate_presented()
            self._flags |= 16

    def consume_persist_visual_change(self):
        changed = bool(self._flags & 16)
        self._flags &= ~16
        return changed

    def draw(self, display):
        load_error = self._state[1][1]
        title = ("Plugin: " + load_error[0]
                 if load_error is not None else "Functions")
        _theme.draw_header(display, title, None, raw=True)
        if self._menu is None:
            display.draw_rectangle(0, 13, self.width, 40, 15)
        else:
            self._menu.draw(display)
        if self._flags & 32:
            hint = "Loading add-ons"
            right = ""
        elif self._flags & 8:
            hint = "Scanning..."
            right = "ESC cancel"
        elif load_error is not None:
            hint = load_error[1]
            right = "ENT off"
        elif self._state[1][0]:
            hint = self._state[1][0]
            right = "ESC retry"
        elif self._state[1][2]:
            hint = self._state[1][2]
            right = "ESC back"
        else:
            hint = "ENT toggle Sh+E"
            right = "ESC back"
        _theme.draw_footer(display, hint, None, right, raw=True)
        if self._flags & 32:
            display.draw_rectangle(130, 57, 76, 5, 9)
            display.fill_rectangle(132, 59, 24, 1, 15)

    def update(self, kb, event=None):
        if self._state[1][3] is not None:
            raise RuntimeError("Function panel scenario transaction is active")
        if self._flags & 32:
            return None
        action = self.menu.update(kb, event)
        if action == "MOVE":
            return "REDRAW"
        if action == "ENTER":
            if event is not None and event[2]:
                # Main owns the environment and only advances it after the
                # quiet grace period.  This input path must not touch SD or
                # execute untrusted top-level source.
                return "FUNC_PANEL_RESCAN"
            return _toggle_current(self)
        elif action == "BACK":
            if self._state[1][1] is not None:
                self._state[1][1] = None
                return "FUNC_PANEL_CANCEL"
            if self._flags & 1:
                previous_save_error = self._state[1][0]
                if not self._queue_save():
                    return ("REDRAW" if self._state[1][0] != previous_save_error
                            else None)
                return "FUNC_PANEL_DONE"
            # A no-op visit must not reload plug-ins: on a fragmented
            # MicroPython heap, compiling every add-on again can fail even
            # though the user did not change the active function set.
            return "FUNC_PANEL_CANCEL"
        return None
