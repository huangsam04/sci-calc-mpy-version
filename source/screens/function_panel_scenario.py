"""Function Panel acceptance transaction, imported only on demand."""

from ui.menu import Menu


class FunctionPanelScenarioTransaction:
    """Incrementally rebuild the resident Function Panel without SD work.

    The transaction deliberately reuses the live Menu when it exists.  A
    complete second label window would be an unnecessary peak on the target,
    so each physical step replaces at most one row.  If a step fails, menu
    labels are treated as derived state and released; settings and live plugin
    metadata are restored by reference without copying their contents.
    """

    __slots__ = (
        "_panel", "_closed", "_complete", "_failed", "_settings",
        "_settings_had_enabled", "_enabled", "_toggled", "_flags",
        "_pending_enabled", "_save_error", "_load_error",
        "_dependency_notice",
        "_plugin_dependencies", "_plugin_files",
        "_plugin_count", "_groups", "_group_labels", "_default_groups",
        "_max_function_name_length", "_menu",
        "_items", "_saved_menu_present",
        "_saved_cursor_pos", "_saved_view_offset", "_saved_cursor_x",
        "_saved_cursor_y", "_saved_cursor_width", "_saved_cursor_height",
        "_saved_cursor_mode", "_saved_cursor_visible", "_saved_cursor_gs",
        "_saved_repeat_state",
        "_group_index", "_plugin_index", "_row_index", "_focus_index")

    def __init__(self, panel):
        if panel._state[1][3] is not None:
            raise RuntimeError("Function panel scenario transaction is already active")

        # Importing resident constants is safe; the transaction must never ask
        # the loader for an SD directory scan or a source reload.
        from calc.functions import (FUNCTION_GROUPS, FUNCTION_GROUP_LABELS,
                                    DEFAULT_ENABLED_GROUPS)
        from calc.limits import (MAX_DISCOVERED_PLUGIN_FILES,
                                 MAX_ENABLED_FUNCTIONS,
                                 MAX_FUNCTION_NAME_LENGTH)

        settings = panel._state[0][1]
        if settings is not None and not isinstance(settings, dict):
            raise RuntimeError("Function panel scenario settings are unavailable")
        if settings is None:
            settings_had_enabled = False
            enabled = DEFAULT_ENABLED_GROUPS
        else:
            settings_had_enabled = "enabled_functions" in settings
            enabled = settings.get("enabled_functions", DEFAULT_ENABLED_GROUPS)
        if not isinstance(enabled, (list, tuple)):
            raise RuntimeError("Function panel enabled selection is invalid")
        if len(enabled) > MAX_ENABLED_FUNCTIONS:
            raise RuntimeError("Function panel enabled selection exceeds its limit")

        toggled = panel._state[0][2]
        if not isinstance(toggled, dict):
            raise RuntimeError("Function panel toggle state is invalid")
        if len(toggled) > len(DEFAULT_ENABLED_GROUPS) + MAX_DISCOVERED_PLUGIN_FILES:
            raise RuntimeError("Function panel toggle state exceeds its limit")

        plugin_files = panel._state[2][1]
        if not isinstance(plugin_files, (list, tuple)):
            raise RuntimeError("Function panel plugin catalog is invalid")
        plugin_count = len(plugin_files)
        if plugin_count > MAX_DISCOVERED_PLUGIN_FILES:
            raise RuntimeError("Function panel plugin catalog exceeds its limit")
        plugin_dependencies = panel._state[2][0]
        if not isinstance(plugin_dependencies, dict):
            raise RuntimeError("Function panel plugin dependencies are invalid")

        menu = panel._menu
        items = panel._items
        if menu is None:
            if items:
                raise RuntimeError("Function panel menu state is inconsistent")
            # Allocate the only new Menu before claiming the panel.  An OOM
            # here leaves every visible and semantic field untouched.
            menu = Menu(0, 13, 210, 4, 10)
            items = []
        else:
            if (not isinstance(items, list)
                    or not isinstance(menu._state[5], list)
                    or len(menu._state[5]) != len(items)):
                raise RuntimeError("Function panel menu state is inconsistent")

        self._panel = panel
        self._closed = False
        self._complete = False
        self._failed = False
        self._settings = settings
        self._settings_had_enabled = settings_had_enabled
        self._enabled = enabled
        self._toggled = toggled
        self._flags = panel._flags
        self._pending_enabled = panel._state[0][3]
        self._save_error = panel._state[1][0]
        self._load_error = panel._state[1][1]
        self._dependency_notice = panel._state[1][2]
        self._plugin_dependencies = plugin_dependencies
        self._plugin_files = plugin_files
        self._plugin_count = plugin_count
        self._groups = FUNCTION_GROUPS
        self._group_labels = FUNCTION_GROUP_LABELS
        self._default_groups = DEFAULT_ENABLED_GROUPS
        self._max_function_name_length = MAX_FUNCTION_NAME_LENGTH
        self._menu = menu
        self._items = items
        self._saved_menu_present = panel._menu is not None
        self._saved_cursor_pos = menu.cursor_pos
        self._saved_view_offset = menu.view_offset
        cursor = menu.cursor
        self._saved_cursor_x = cursor.x
        self._saved_cursor_y = cursor.y
        self._saved_cursor_width = cursor.width
        self._saved_cursor_height = cursor.height
        self._saved_cursor_mode = cursor.mode
        self._saved_cursor_visible = cursor.is_visible
        self._saved_cursor_gs = cursor.gs
        self._saved_repeat_state = menu._state[6]
        self._group_index = 0
        self._plugin_index = 0
        self._row_index = 0
        self._focus_index = -1

        # Claim the panel before altering derived labels.  Existing rows remain
        # available until their bounded replacement step, avoiding a second
        # complete menu allocation while still leaving an OOM-safe rollback.
        panel._state[1][3] = self
        panel._menu = menu
        panel._items = items
        panel._flags &= ~4

    @property
    def complete(self):
        return self._complete

    def _require_open(self):
        panel = self._panel
        if self._closed or panel is None:
            raise RuntimeError("Function panel scenario transaction is closed")
        if panel._state[1][3] is not self:
            raise RuntimeError("Function panel scenario transaction is not active")
        return panel

    def _require_unchanged_source(self, panel):
        if (panel._state[0][1] is not self._settings
                or panel._state[0][2] is not self._toggled
                or panel._state[2][0] is not self._plugin_dependencies
                or panel._state[2][1] is not self._plugin_files
                or panel._menu is not self._menu
                or panel._items is not self._items
                or (panel._flags & ~4) != (self._flags & ~4)
                or panel._state[0][3] is not self._pending_enabled
                or panel._state[1][0] != self._save_error
                or panel._state[1][1] != self._load_error
                or panel._state[1][2] != self._dependency_notice
                or len(self._plugin_files) != self._plugin_count):
            raise RuntimeError("Function panel scenario source changed")
        settings = self._settings
        if settings is not None:
            had_enabled = "enabled_functions" in settings
            if had_enabled != self._settings_had_enabled:
                raise RuntimeError("Function panel scenario settings changed")
            if (had_enabled
                    and settings.get("enabled_functions") is not self._enabled):
                raise RuntimeError("Function panel scenario settings changed")

    def _is_enabled(self, setting_name):
        toggled = self._toggled
        if setting_name in toggled:
            return toggled[setting_name]
        return setting_name in self._enabled

    def _publish_row(self, panel, setting_name, is_on, is_group, label):
        """Replace or append exactly one row without a full scratch menu."""
        menu = self._menu
        items = self._items
        row = self._row_index
        if row < len(items):
            if row >= len(menu._state[5]):
                raise RuntimeError("Function panel menu state is inconsistent")
            items[row] = (setting_name, is_on, is_group)
            menu.replace_item(row, label, None)
        elif row == len(items):
            if row != len(menu._state[5]):
                raise RuntimeError("Function panel menu state is inconsistent")
            menu.add_item(label, None)
            items.append((setting_name, is_on, is_group))
        else:
            raise RuntimeError("Function panel row state is inconsistent")
        if (self._load_error is not None
                and setting_name == "plugin:" + self._load_error[0]):
            self._focus_index = row
        self._row_index = row + 1

    def _step_group(self, panel):
        group_name = self._default_groups[self._group_index]
        self._group_index += 1
        if group_name not in self._groups:
            return False
        is_on = self._is_enabled(group_name)
        prefix = "[x]" if is_on else "[ ]"
        display_name = self._group_labels.get(group_name, group_name)
        label = prefix + " " + display_name
        self._publish_row(panel, group_name, is_on, True, label)
        return False

    def _step_plugin(self, panel):
        entry = self._plugin_files[self._plugin_index]
        self._plugin_index += 1
        if (not isinstance(entry, (list, tuple)) or len(entry) != 2
                or not isinstance(entry[0], str)
                or len(entry[0]) > self._max_function_name_length
                or not isinstance(entry[1], str)):
            raise RuntimeError("Function panel plugin catalog entry is invalid")
        name = entry[0]
        setting_name = "plugin:" + name
        is_on = self._is_enabled(setting_name)
        prefix = "[x]" if is_on else "[ ]"
        label = prefix + " Add-on: " + name
        self._publish_row(panel, setting_name, is_on, False, label)
        return False

    def _trim_one_row(self):
        """Discard one stale row after an add-on disappeared from the catalog."""
        menu = self._menu
        items = self._items
        if len(menu._state[5]) != len(items):
            raise RuntimeError("Function panel menu state is inconsistent")
        if len(items) <= self._row_index:
            return False
        items.pop()
        menu._state[5].pop()
        menu.invalidate_presented()
        return True

    def _complete_menu(self, panel):
        menu = self._menu
        items = self._items
        if (len(items) != self._row_index
                or len(menu._state[5]) != self._row_index):
            raise RuntimeError("Function panel menu state is inconsistent")
        menu.cursor_pos = self._focus_index if self._focus_index >= 0 else 0
        menu.view_offset = 0
        menu._clamp_view()
        menu.activate()
        panel._flags |= 4
        self._complete = True
        return True

    def _restore_semantic_state(self, panel):
        """Restore references/scalars only; menu rows are handled separately."""
        panel._state[0][1] = self._settings
        panel._state[0][2] = self._toggled
        panel._flags = (panel._flags & 4) | (self._flags & ~4)
        panel._state[0][3] = self._pending_enabled
        panel._state[1][0] = self._save_error
        panel._state[1][1] = self._load_error
        panel._state[1][2] = self._dependency_notice
        panel._state[2][0] = self._plugin_dependencies
        panel._state[2][1] = self._plugin_files

        settings = self._settings
        if settings is not None:
            if self._settings_had_enabled:
                settings["enabled_functions"] = self._enabled
            elif "enabled_functions" in settings:
                del settings["enabled_functions"]

    def _restore_menu_scalars(self, panel):
        menu = self._menu
        if not self._saved_menu_present or panel._menu is not menu:
            return
        cursor = menu.cursor
        menu.cursor_pos = self._saved_cursor_pos
        menu.view_offset = self._saved_view_offset
        menu._state[6] = self._saved_repeat_state
        cursor.x = self._saved_cursor_x
        cursor.y = self._saved_cursor_y
        cursor.width = self._saved_cursor_width
        cursor.height = self._saved_cursor_height
        cursor.mode = self._saved_cursor_mode
        cursor.is_visible = self._saved_cursor_visible
        cursor.gs = self._saved_cursor_gs
        # Labels were rebuilt in place, so stale present stamps must not claim
        # that the replacement pixels have already reached the framebuffer.
        menu._state[4] = 0

    def _discard_derived_menu(self, panel):
        """Drop labels with tuple sentinels so OOM cleanup cannot allocate."""
        menu = self._menu
        if menu is not None:
            menu._state[5] = ()
            menu._state[4] = 0
            menu._state[6] = 0
        panel._items = ()
        panel._menu = None
        panel._flags &= ~4
        self._items = None
        self._menu = None

    def _finish(self, panel):
        if panel._state[1][3] is self:
            panel._state[1][3] = None
        # This method is reached only after either semantic restoration or a
        # deliberate derived-menu discard completed.  Do not release these
        # snapshots earlier: a cleanup failure must leave enough state for a
        # later close() retry to restore the resident panel.
        self._panel = None
        self._settings = None
        self._enabled = None
        self._toggled = None
        self._pending_enabled = None
        self._save_error = None
        self._load_error = None
        self._dependency_notice = None
        self._plugin_dependencies = None
        self._plugin_files = None
        self._groups = None
        self._group_labels = None
        self._default_groups = None
        self._items = None
        self._menu = None
        self._closed = True

    def _recover_after_step_failure(self, panel):
        """Best-effort rollback that never masks the original step failure."""
        self._failed = True
        restored = True
        try:
            self._restore_semantic_state(panel)
        except Exception:
            restored = False
        try:
            self._discard_derived_menu(panel)
        except Exception:
            restored = False
        if restored:
            self._finish(panel)

    def step(self):
        """Build one group/add-on row, or trim one stale row, per call."""
        panel = self._require_open()
        if self._complete:
            return True
        try:
            self._require_unchanged_source(panel)
            if self._group_index < len(self._default_groups):
                return self._step_group(panel)
            if self._plugin_index < self._plugin_count:
                return self._step_plugin(panel)
            if self._trim_one_row():
                return False
            return self._complete_menu(panel)
        except Exception:
            # In particular, preserve a primary MemoryError even if restoring
            # a hostile settings mapping itself fails under low memory.
            self._recover_after_step_failure(panel)
            raise

    def close(self):
        """Restore semantic state; incomplete/failing rebuilds release labels."""
        if self._closed:
            return True
        panel = self._require_open()
        usable_menu = False
        if self._complete and not self._failed:
            try:
                self._require_unchanged_source(panel)
                usable_menu = True
            except Exception:
                self._failed = True
        try:
            self._restore_semantic_state(panel)
            if usable_menu:
                self._restore_menu_scalars(panel)
                panel._flags |= 4
            else:
                self._discard_derived_menu(panel)
        except Exception:
            # The transaction guard remains installed, so the caller can retry
            # close after a secondary cleanup fault.
            self._failed = True
            try:
                self._discard_derived_menu(panel)
            except Exception:
                pass
            raise
        self._finish(panel)
        return True
