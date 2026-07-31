from pathlib import Path

import pytest

import main
from calc import loader
from screens.function_panel import FunctionPanel
from calc.limits import MAX_ENABLED_PLUGINS
from ui.menu import Menu
from ui.motion import DAMAGE_PARTIAL, DamageMap


class DisplayStub:
    def __init__(self):
        self.text = []
        self.rectangles = []
        self.fills = []

    def draw_text8x8(self, x, y, text, **kwargs):
        self.text.append(text)

    def draw_rectangle(self, *args):
        self.rectangles.append(args)

    def fill_rectangle(self, *args):
        self.fills.append(args)

    def draw_hline(self, *args):
        pass


def test_function_panel_supports_the_nav_quiet_settle_contract():
    panel = FunctionPanel(None, {"enabled_functions": ["basic"]})

    assert panel.settle_step() == 0


def test_plugin_load_error_is_visible_in_function_panel():
    panel = FunctionPanel(
        None, {"enabled_functions": ["basic"]})
    display = DisplayStub()

    panel.set_load_errors([("broken", "boom")])
    panel.draw(display)

    assert any("broken" in text for text in display.text)
    assert any("boom" in text for text in display.text)
    assert any("ENT off" in text for text in display.text)


def test_function_panel_default_footer_is_prefitted_for_the_hot_draw_path():
    panel = FunctionPanel(None, {"enabled_functions": ["basic"]})
    display = DisplayStub()

    panel.draw(display)

    assert "ENT toggle Sh+E" in display.text


def test_function_panel_loading_bar_is_visible_and_blocks_more_input():
    panel = FunctionPanel(None, {"enabled_functions": ["basic"]})
    panel.activate()
    display = DisplayStub()
    cursor_before = panel.menu.cursor_pos

    assert panel.set_plugin_reload_active(True) is True
    panel.draw(display)

    assert "Loading add-ons" in display.text
    assert (130, 57, 76, 5, 9) in display.rectangles
    assert (132, 59, 24, 1, 15) in display.fills
    assert panel.update(None, (3, 1, False)) is None
    assert panel.menu.cursor_pos == cursor_before


def test_function_panel_reuses_loaded_catalog_without_reexecuting_plugins():
    panel = FunctionPanel(
        None, {"enabled_functions": ["basic", "plugin:solve"]},
        {"solve": ()}, [("solve", "solve.py")])
    panel.activate()

    assert not hasattr(panel, "_plugin_functions")
    assert "[x] Add-on: solve" in [
        label for label, _ in panel.menu._state[5]]


def test_function_panel_adopts_boot_file_snapshot_without_relisting_sd():
    files = [("solve", "solve.py")]

    panel = FunctionPanel(
        None, {"enabled_functions": ["plugin:solve"]},
        {"solve": ()}, files)
    panel.activate()

    assert panel._state[2][1] is files
    assert "[x] Add-on: solve" in [
        label for label, _ in panel.menu._state[5]]


def test_empty_plugin_dependency_catalog_skips_closure_construction(
        monkeypatch):
    panel = FunctionPanel(
        None,
        {"enabled_functions": ["plugin:solve"]},
        {"solve": ()},
        [("solve", "solve.py")],
    )
    monkeypatch.setattr(
        FunctionPanel,
        "_enable_dependencies",
        lambda *_args: pytest.fail("empty dependency closure was traversed"),
    )

    assert panel._ensure_all_enabled_dependencies() == ((), ())


def test_function_panel_reuses_a_preallocated_menu():
    menu = Menu(0, 13, 210, 4, 10)

    panel = FunctionPanel(None, {"enabled_functions": ["basic"]})
    panel._menu = menu

    assert panel.menu is menu


def test_builtin_groups_and_addons_have_unambiguous_user_labels():
    panel = FunctionPanel(
        None,
        {"enabled_functions": [
            "basic", "trig", "math", "list", "plugin:basic", "plugin:trig",
        ]},
        {"basic": (), "trig": ()},
        [("basic", "basic.py"), ("trig", "trig.py")])

    panel.activate()

    labels = [label for label, _ in panel.menu._state[5]]
    assert any("Arithmetic" in label for label in labels)
    assert any("Trigonometry" in label for label in labels)
    assert "[x] Add-on: basic" in labels
    assert "[x] Add-on: trig" in labels


def test_function_panel_uses_preloaded_plugin_catalog_during_activation():
    files = [("basic", "basic.py")]
    panel = FunctionPanel(
        None, {"enabled_functions": ["basic"]}, {}, files)

    assert panel._state[2][1] is files
    panel.activate()
    assert panel._items[-1] == ("plugin:basic", False, False)


def test_function_panel_builds_deferred_menu_on_direct_activation(
        monkeypatch):
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    refreshes = []
    monkeypatch.setattr(
        FunctionPanel, "_refresh", lambda _panel: refreshes.append(True))

    panel.activate()

    assert refreshes == [True]


def test_function_panel_cancels_unchanged_exit(monkeypatch):
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    panel.activate()
    saves = []
    monkeypatch.setattr(
        FunctionPanel, "_queue_save", lambda _panel: saves.append(True))

    # (0, 0) is the physical ESC key, so this follows the production menu
    # key-to-action path rather than faking a menu return value.
    assert panel.update(None, (0, 0, False)) == "FUNC_PANEL_CANCEL"
    assert saves == []


def test_function_panel_shift_enter_only_submits_a_plugin_scan_action(
        monkeypatch):
    catalog = [("basic", "basic.py")]
    panel = FunctionPanel(
        None, {"enabled_functions": ["basic"]}, {}, catalog)
    panel.activate()
    catalog[:] = [("solve", "solve.py")]
    monkeypatch.setattr(Menu, "update", lambda _menu, kb, event: "ENTER")

    assert panel.update(None, (3, 3, True)) == "FUNC_PANEL_RESCAN"

    assert panel._items[-1] == ("plugin:basic", False, False)


def test_function_panel_adopts_completed_catalog_and_uses_fixed_scan_status(
        monkeypatch):
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    panel.activate()

    class Report:
        files = [("solve", "solve.py")]
        functions = {"solve": ["solve"]}
        dependencies = {"solve": ()}
        errors = []

    panel.adopt_plugin_catalog(Report())

    assert panel._items[-1] == ("plugin:solve", False, False)
    assert "[ ] Add-on: solve" in [
        label for label, _ in panel.menu._state[5]]

    panel.set_plugin_scan_active(True)
    display = DisplayStub()
    panel.draw(display)
    assert "Scanning..." in display.text
    panel.set_plugin_scan_active(False)


def test_background_catalog_adoption_does_not_rebuild_a_released_panel(
        monkeypatch):
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    panel.activate()
    assert panel.release_memory() is True
    assert panel._menu is None

    class Report:
        files = [("solve", "solve.py")]
        functions = {"solve": ["solve"]}
        dependencies = {"solve": ()}
        errors = []

    panel.adopt_plugin_catalog(Report())

    assert panel._menu is None
    assert panel._items == ()
    assert not panel._flags & 4
    assert panel._state[2][1] == [("solve", "solve.py")]

    panel.activate()
    assert panel._items[-1] == ("plugin:solve", False, False)


def test_function_panel_invalidates_only_for_visible_scan_load_and_save_changes(
        monkeypatch):
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    panel.activate()
    invalidations = []
    original_invalidate = Menu.invalidate_presented

    def record_invalidation(menu):
        if menu is panel.menu:
            invalidations.append(True)
        original_invalidate(menu)

    monkeypatch.setattr(Menu, "invalidate_presented", record_invalidation)

    assert panel.set_plugin_scan_active(True) is True
    assert panel.set_plugin_scan_active(True) is False
    assert panel.set_plugin_scan_active(False) is True
    assert invalidations == [True, True]
    invalidations[:] = []
    assert panel.set_load_errors([("broken", "details")]) is True
    assert panel.set_load_errors([("broken", "details")]) is False
    assert panel.set_load_errors(()) is True
    assert invalidations == [True, True]

    invalidations[:] = []
    panel._on_save_result(False)
    panel._on_save_result(False)
    assert invalidations == [True]
    assert panel.consume_persist_visual_change() is True
    assert panel.consume_persist_visual_change() is False

    panel._on_save_result(True)
    panel._on_save_result(True)
    assert invalidations == [True, True]
    assert panel.consume_persist_visual_change() is True
    assert panel.consume_persist_visual_change() is False


def test_function_panel_menu_move_does_not_damage_static_footer(monkeypatch):
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    panel.activate()
    panel.mark_presented()
    panel.menu.move_cursor_down()
    damage = DamageMap()

    assert panel.collect_present_damage(damage) == DAMAGE_PARTIAL
    assert damage.count == 1
    start, count = damage.ranges[0]
    assert start + count <= 54


def test_function_panel_queues_shared_settings_when_leaving(monkeypatch):
    settings = {"enabled_functions": ["basic", "trig", "math", "list"]}
    queued = []

    panel = FunctionPanel(
        lambda value, callback=None, owner=None: queued.append(dict(value)),
        settings)
    panel._items = [("basic", True, True), ("trig", False, True)]
    panel._flags |= 1

    assert panel._queue_save() is True
    assert settings == {"enabled_functions": ["basic"]}
    assert queued == [{"enabled_functions": ["basic"]}]


def test_function_panel_refuses_an_addon_selection_past_the_capacity():
    settings = {"enabled_functions": ["basic"]}
    queued = []
    panel = FunctionPanel(
        lambda value, callback=None, owner=None: queued.append(value),
        settings)
    panel._items = [
        ("plugin:p" + str(index), True, False)
        for index in range(MAX_ENABLED_PLUGINS + 1)]

    assert panel._queue_save() is False
    assert panel._state[1][0] == "Add-on limit reached"
    assert queued == []


def test_function_panel_rolls_back_a_queued_selection_after_reload_failure():
    settings = {"enabled_functions": ["basic"]}
    queued = []
    panel = FunctionPanel(
        lambda value, callback=None, owner=None: queued.append(value),
        settings)
    panel._items = [("basic", True, True), ("plugin:new", True, False)]

    assert panel._queue_save() is True
    assert settings["enabled_functions"] == ["basic", "plugin:new"]
    assert queued == [settings]

    assert panel.rollback_plugin_reload() is True
    assert settings["enabled_functions"] == ["basic"]
    assert panel.rollback_plugin_reload() is False


def test_function_panel_keeps_deferred_save_failure_visible_after_reopening(
        monkeypatch):
    settings = {"enabled_functions": ["basic", "trig", "math", "list"]}
    panel = FunctionPanel(None, settings=settings)

    panel._on_save_result(False)
    panel.activate()

    assert panel._state[1][0] == "Not saved - check SD"


def test_function_panel_releases_menu_labels_but_keeps_pending_selection(
        monkeypatch):
    panel = FunctionPanel(None, settings={"enabled_functions": ["basic"]})
    panel.activate()
    old_menu = panel._menu
    panel._state[0][2] = {"basic": False}
    panel._state[0][3] = ["basic", "trig"]
    panel._state[1][2] = "Derived status"
    panel._state[1][0] = "Not saved - check SD"
    panel._state[1][1] = ("broken", "details")

    assert panel.release_memory() is True
    assert panel._items == ()
    assert panel._menu is None
    assert not panel._flags & 4
    assert panel._state[0][2] == {"basic": False}
    assert panel._state[0][3] == ["basic", "trig"]
    assert panel._state[1][0] == "Not saved - check SD"
    assert panel._state[1][1] == ("broken", "details")

    panel.activate()
    assert panel._menu is not old_menu
    assert panel._items


def test_main_defers_work_after_showing_loading_and_reloads_plugins_once():
    source = (Path(__file__).parents[1] / "source" / "main.py").read_text(
        encoding="utf-8")
    start = source.index('elif result == "FUNC_PANEL_DONE":')
    end = source.index("elif result in (", start)
    handler = source[start:end]
    helper_start = source.index("def _reload_functions_after_reclaim(")
    helper_end = source.index("def _draw_crash", helper_start)
    helper = source[helper_start:helper_end]
    panel_start = source.index("screen = FunctionPanel(")
    panel_end = source.index("screen.set_load_errors", panel_start)
    panel_init = source[panel_start:panel_end]

    assert "cur.set_plugin_reload_active(True)" in handler
    assert "nav.defer_back(event)" not in handler
    assert "_function_reload_pending = True" in handler
    assert 'result == "FUNC_PANEL_RESCAN"' in handler
    assert "FunctionEnvironment" not in handler
    assert "_cancel_function_environment(" not in handler
    background = source[source.index("# Potentially blocking work"):
                        source.index("time.sleep_ms(IDLE_LOOP_SLEEP_MS)")]
    assert "_reload_functions_after_reclaim(" in background
    assert "nav, nav.current, settings, registry" in background
    scheduler_start = source.index("scheduler = FrameScheduler(")
    scheduler_end = source.index("diagnostics =", scheduler_start)
    scheduler_init = source[scheduler_start:scheduler_end]
    assert "background_idle_ms=BACKGROUND_IDLE_MS" in scheduler_init
    assert "scheduler.background_due(now)" in background
    assert "FunctionEnvironment(" not in background
    assert "_scan_function_files_after_reclaim(" in background
    assert "func_panel.adopt_plugin_files(files)" in background
    assert "return _reload_functions(settings, registry)" in helper
    assert "registry.plugin_dependencies, registry.plugin_files" in panel_init
    assert "context[5].request_settings, context[4]" in panel_init
    assert "registry.plugin_functions" not in panel_init
    assert "func_panel.set_plugin_catalog(" in background
    assert "load_settings()" not in handler
    assert (background.index("_function_reload_pending = False")
            < background.index("_reload_functions_after_reclaim("))
    main_loop_start = source.index("    while True:")
    main_loop_end = source.index("\n\nif __name__ == \"__main__\":", main_loop_start)
    main_loop = source[main_loop_start:main_loop_end]
    recovery_start = main_loop.index(
        "        except MemoryError:\n"
        "            # Memory pressure returns to a usable root and forgets snapshots.")
    recovery_end = main_loop.index("        except Exception as e:", recovery_start)
    recovery = main_loop[recovery_start:recovery_end]
    assert recovery.startswith("        except MemoryError:")
    assert "str(" not in recovery
    assert "_draw_crash(" not in recovery
    assert (recovery.index("_function_reload_pending = False")
            < recovery.index("_function_scan_pending = False")
            < recovery.index("func_panel.rollback_plugin_reload()")
            < recovery.index("nav.reset(main_menu)"))

    ordinary_start = main_loop.index("        except Exception as e:")
    ordinary = main_loop[ordinary_start:]
    assert (ordinary.index("_function_reload_pending = False")
            < ordinary.index("_function_scan_pending = False")
            < ordinary.index("func_panel.rollback_plugin_reload()")
            < ordinary.index("nav.cancel_motion()")
            < ordinary.index("nav.reset(main_menu)"))


def test_main_rescan_marks_visual_change_only_when_scan_status_changes():
    source = (Path(__file__).parents[1] / "source" / "main.py").read_text(
        encoding="utf-8")
    handler_start = source.index("    def _handle_event(event):")
    handler_end = source.index("    if publish_runtime:", handler_start)
    handler = source[handler_start:handler_end]
    rescan_start = handler.index('elif result == "FUNC_PANEL_RESCAN":')
    rescan_end = handler.index("elif result in (", rescan_start)
    rescan = handler[rescan_start:rescan_end]

    # A new request schedules a bounded filename refresh, but an already-
    # visible scanning footer is not a new frame.
    assert "_function_environment" not in rescan
    assert "_function_scan_pending = True" in rescan
    assert "if cur.set_plugin_scan_active(True):" in rescan
    assert rescan.count("_input_visual_changed = True") == 1
    assert (rescan.index("if cur.set_plugin_scan_active(True):")
            < rescan.index("_input_visual_changed = True")
            < rescan.index("return _input_visual_changed"))

    # The early return prevents the generic non-null-result fallback below
    # from scheduling a phantom render for repeated Shift+ENT.
    assert "changed = result is not None" not in rescan
    assert (handler.index("return _input_visual_changed", rescan_start)
            < handler.index("changed = result is not None", rescan_end))


def test_main_keeps_the_loading_panel_visible_until_reload_finishes():
    source = (Path(__file__).parents[1] / "source" / "main.py").read_text(
        encoding="utf-8")
    handler_start = source.index("    def _handle_event(event):")
    handler_end = source.index("    if publish_runtime:", handler_start)
    handler = source[handler_start:handler_end]
    done_start = handler.index('elif result == "FUNC_PANEL_DONE":')
    done_end = handler.index('elif result == "FUNC_PANEL_RESCAN":', done_start)
    done = handler[done_start:done_end]
    reload_start = source.index("                if _function_reload_pending:")
    reload_end = source.index("                elif _function_scan_pending:", reload_start)
    reload_block = source[reload_start:reload_end]

    assert "cur.set_plugin_reload_active(True)" in done
    assert "nav.defer_back" not in done
    assert "func_panel.set_plugin_reload_active(False)" in reload_block
    assert "nav.back()" in reload_block
    assert "nav.release_pending" not in reload_block


def test_main_filename_scan_reclaims_and_releases_loader(monkeypatch):
    events = []

    class Nav:
        def prepare_memory_intensive_operation(self, screen):
            events.append(("prepare", screen))

    monkeypatch.setattr(main.gc, "collect", lambda: events.append("collect"))
    monkeypatch.setattr(
        main, "_drop_function_loader_module", lambda: events.append("drop"))
    monkeypatch.setattr(
        loader, "list_function_files", lambda: [("basic", "basic.py")])

    panel = object()
    assert main._scan_function_files_after_reclaim(Nav(), panel) == [
        ("basic", "basic.py")]
    assert events == [
        ("prepare", panel), "collect", "drop", "collect"]
