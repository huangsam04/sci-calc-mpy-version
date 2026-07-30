# SCI-CALC MicroPython Firmware
# Main entry point — display init FIRST, then lazy-load everything else
"""SCI-CALC: Multifunctional Scientific Calculator (MicroPython Edition)."""
import time
import gc
from machine import Pin, SPI

# --- Minimal imports for splash screen ---
from display.ssd1322 import Display as SSD1322
from ui.motion import (
    IDLE_FRAME_MS, IDLE_LOOP_SLEEP_MS, SLEEP_SCAN_MS, FrameScheduler)
from version import VERSION
from performance import metrics

# SPI pins for display
SPI_CLK = 18
SPI_DATA = 23
SPI_CS = 5
SPI_DC = 16
SPI_RESET = 17
INPUT_BATCH_LIMIT = 5
BACKGROUND_IDLE_MS = 750
SIDEBAR_REFRESH_MS = 5000


def _init_display():
    """Initialize SSD1322 display — called FIRST for fast splash."""
    spi = SPI(2,
              baudrate=10_000_000,
              polarity=0, phase=0, bits=8,
              sck=Pin(SPI_CLK), mosi=Pin(SPI_DATA))
    cs = Pin(SPI_CS, Pin.OUT)
    dc = Pin(SPI_DC, Pin.OUT)
    rst = Pin(SPI_RESET, Pin.OUT)
    return SSD1322(spi, cs, dc, rst)


def _boot_progress(display, step, total, label="", operation=""):
    """Draw one truthful, allocation-light boot checkpoint."""
    bar_x, bar_y, bar_w, bar_h = 20, 34, 216, 5
    fill_w = (bar_w - 2) * step // max(1, total)
    display.clear_buffers(0)
    display.draw_text8x8(96, 10, "SCI-CALC", gs=15)
    display.draw_hline(60, 20, 136, 6)
    display.draw_rectangle(bar_x, bar_y, bar_w, bar_h, 6)
    if fill_w:
        display.fill_rectangle(
            bar_x + 1, bar_y + 1, fill_w, bar_h - 2, 15)
    if label:
        display.draw_text8x8(20, 44, label, gs=10)
    progress = str(step) + "/" + str(total)
    display.draw_text8x8(
        210 - len(progress) * 8, 44, progress, gs=8)
    if operation:
        detail = "(" + operation + ")"
        if len(detail) > 29:
            detail = detail[:28] + "~"
        display.draw_text8x8(12, 54, detail, gs=7)
    display.present()


def _boot_fail(display, step, total, label, error):
    """Show boot error briefly, then continue with fallback."""
    bar_x, bar_y, bar_w, bar_h = 20, 34, 216, 5
    fill_w = int((bar_w - 2) * step / total)

    err_str = str(error)
    if len(err_str) > 28:
        err_str = err_str[:27] + "~"

    display.clear_buffers(0)
    display.draw_text8x8(96, 10, "SCI-CALC", gs=15)
    display.draw_hline(60, 20, 136, 6)

    # Progress bar at current step
    display.draw_rectangle(bar_x, bar_y, bar_w, bar_h, 6)
    if fill_w > 0:
        display.fill_rectangle(bar_x + 1, bar_y + 1, fill_w, bar_h - 2, 15)

    # Error
    display.draw_text8x8(16, 44, f"FAIL: {label}", gs=15)
    display.draw_text8x8(16, 52, err_str, gs=10)
    display.present()
    time.sleep(2)


def _needs_render(now, last_render, dirty, stopwatch_running, input_changed):
    """Decide whether the current loop should submit a full display frame."""
    if input_changed:
        return True
    elapsed = time.ticks_diff(now, last_render)
    return (elapsed >= IDLE_FRAME_MS
            and (dirty or stopwatch_running
                 or elapsed >= SIDEBAR_REFRESH_MS))


def _drain_input_batch(nav, keyboard, handler):
    """Dispatch at most five queued edges before the next display write."""
    count = 0
    while count < INPUT_BATCH_LIMIT:
        event = nav.poll_event(keyboard)
        if event is None:
            break
        handler(event)
        count += 1
    return count


def _navigate_registered_page(nav, current, result, event):
    """Enter a resident page returned by the current screen.

    Calculator and Function Panel deliberately use compact standalone screen
    classes, so their navigation contract is registration identity rather than
    inheritance from UIElement.
    """
    if result is current:
        return False
    for screen in nav._managed:
        if result is screen:
            nav.go_to(screen, event)
            return True
    return False


def _page_update_requested(keyboard, event):
    """Keep menu direction hold-repeat alive without repeating calculator input."""
    return (event is not None
            or keyboard.is_pressed(0, 0)
            or keyboard.is_pressed(4, 3)
            or keyboard.is_pressed(1, 1)
            or keyboard.is_pressed(3, 1))


def _refresh_sidebar_if_due(renderer, now, last_refresh):
    """Invalidate slow status pixels independently from page rendering."""
    if time.ticks_diff(now, last_refresh) < SIDEBAR_REFRESH_MS:
        return last_refresh
    renderer.invalidate_sidebar()
    return now


def _toggle_angle_mode(registry, settings, persistence, renderer,
                       plot_screen=None):
    """Update calculation state and its sidebar pixels in one input step."""
    registry.angle_mode = 1 - registry.angle_mode
    settings["angle_mode"] = registry.angle_mode
    if plot_screen is not None:
        plot_screen.on_angle_mode_changed()
    renderer.invalidate_sidebar()
    persistence.request_settings(settings)


def _blocks_global_shortcuts(screen):
    """Ask a modal page whether it owns every queued edge this frame."""
    checker = getattr(screen, "blocks_global_shortcuts", None)
    if callable(checker):
        return bool(checker())
    return bool(checker)


def _open_context_letter_panel(screen, event, letter_panel, nav):
    """Open Letters only from a page's currently visible input context."""
    if event is None or _blocks_global_shortcuts(screen):
        return False
    row, col, shift = event
    if (row, col) != (3, 5) or not shift:
        return False
    target_getter = getattr(screen, "letter_input_target", None)
    target = target_getter() if callable(target_getter) else None
    if target is None:
        return False
    letter_panel._state[0] = target
    nav.go_to(letter_panel, event)
    return True


def _global_angle_allowed(event, page_was_modal, page_result):
    """Leave ANG to ordinary non-modal pages after their own event handler."""
    return (event is not None and not page_was_modal and page_result is None
            and event[0] == 4 and event[1] == 4)


def _drop_function_loader_module():
    """Remove the rebuildable plug-in loader from the resident graph."""
    if not hasattr(gc, "mem_free"):
        return
    import sys
    loader_module = sys.modules.pop("calc.loader", None)
    sys.modules.pop("approot", None)
    calc_package = sys.modules.get("calc")
    if (loader_module is not None and calc_package is not None
            and getattr(calc_package, "loader", None) is loader_module):
        delattr(calc_package, "loader")


def _release_function_loader():
    """Drop the rebuildable plug-in loader after its boot-time use."""
    _drop_function_loader_module()
    if hasattr(gc, "mem_free"):
        # These splash-only functions have completed before resident page
        # imports begin.  Remove their raw code from the constrained device
        # heap; host tests keep them available for direct verification.
        module_globals = globals()
        module_globals.pop("_init_display", None)
        module_globals.pop("_boot_progress", None)
        module_globals.pop("_boot_fail", None)
        module_globals.pop("_release_function_loader", None)


def _reload_functions(settings, registry=None):
    from calc.functions import (
        DEFAULT_ENABLED_GROUPS, FUNCTION_GROUPS, build_registry,
        register_builtins)
    from calc.limits import MAX_ENABLED_PLUGINS
    enabled = settings.get("enabled_functions", DEFAULT_ENABLED_GROUPS)
    if not isinstance(enabled, (list, tuple)):
        raise ValueError("enabled_functions must be a list")
    groups = []
    sd_names = []
    for selected in enabled:
        if not isinstance(selected, str):
            raise ValueError("Function selection names must be strings")
        if selected in FUNCTION_GROUPS:
            if selected not in groups:
                groups.append(selected)
        elif selected.startswith("plugin:"):
            if selected[7:] not in sd_names:
                if len(sd_names) >= MAX_ENABLED_PLUGINS:
                    raise ValueError("Enabled add-on limit reached")
                sd_names.append(selected[7:])
    bundled_only = registry is not None
    for name in sd_names:
        if name != "basic" and name != "solve" and name != "trig":
            bundled_only = False
            break
    if registry is None:
        target = build_registry(groups)
        angle_mode = 0
        in_place = False
        known_files = ()
    else:
        # The main loop is synchronous while this cold operation runs.  Keep
        # the shared registry identity and its already allocated hash table,
        # but release the rebuildable callback graph before importing the
        # loader.  Reallocating that table after user state fragmented the heap
        # was the measured 640-byte maximum-state reload failure.
        angle_mode = registry.angle_mode
        known_files = registry.plugin_files
        registry.clear_for_reload()
        registry.plugin_errors.clear()
        gc.collect()
        target = registry
        in_place = True
    target.angle_mode = angle_mode
    report = None
    try:
        if bundled_only:
            # The canonical add-ons are already compiled and resident.  Do not
            # import the general SD source loader merely to rebuild them: its
            # measured 868-byte raw-code allocation failed on the fragmented
            # heap after supported maximum user state.
            from calc.bundled_plugins import register_bundled
            for name in sd_names:
                if not register_bundled(name, target):
                    raise ValueError("Bundled add-on is unavailable")
                target._plugin_exports[name] = {}
            target.plugin_files = known_files
            register_builtins(target, groups)
            return target
        from calc.loader import load_function_files, list_function_files
        if sd_names:
            report = load_function_files(
                target, sd_names, in_place=in_place)
            target.plugin_errors = report.errors
            target.plugin_dependencies = report.dependencies
            target.plugin_files = report.files
        else:
            target.plugin_files = list_function_files()
        if registry is not None and (report is None or not report.errors):
            # Allocate built-in definition tuples only after the largest
            # plug-in sources have compiled.  Their callbacks share this same
            # identity-stable registry, so no second table is required.
            register_builtins(registry, groups)
    except MemoryError:
        if registry is not None:
            # Leave the calculator with its complete non-optional grammar even
            # when an unsupported add-on exhausts the cold operation.
            registry.clear_for_reload()
            gc.collect()
            register_builtins(registry, groups)
            registry.angle_mode = angle_mode
        raise

    if registry is None:
        return target

    if report is not None and report.errors:
        # Do not expose a partially loaded set.  Built-ins are always resident
        # after an ordinary add-on failure, while the bounded report remains
        # available to the panel and its pending selection rolls back.
        errors = report.errors
        files = report.files
        registry.clear_for_reload()
        gc.collect()
        register_builtins(registry, groups)
        registry.angle_mode = angle_mode
        registry.plugin_errors = errors
        registry.plugin_files = files
        return None
    return registry


def _reload_functions_after_reclaim(nav, active_screen, settings, registry):
    """Reload plug-ins only after one unified optional-memory reclaim."""
    enabled = settings.get("enabled_functions")
    bundled_only = (enabled is None or isinstance(enabled, list)
                    or isinstance(enabled, tuple))
    if bundled_only and enabled is not None:
        for selected in enabled:
            if (not isinstance(selected, str)
                    or (selected.startswith("plugin:")
                        and selected != "plugin:basic"
                        and selected != "plugin:solve"
                        and selected != "plugin:trig")):
                bundled_only = False
                break
    if bundled_only:
        return _reload_bundled_functions(settings, registry)
    return _reload_external_functions_after_reclaim(
        nav, active_screen, settings, registry)


def _reload_bundled_functions(settings, registry):
    """Rebuild fixed add-ons without source-loader or selection-list churn."""
    from calc.bundled_plugins import register_bundled
    from calc.functions import DEFAULT_ENABLED_GROUPS, register_builtins

    enabled = settings.get("enabled_functions", DEFAULT_ENABLED_GROUPS)
    angle_mode = registry.angle_mode
    known_files = registry.plugin_files
    registry.clear_for_reload()
    registry.plugin_errors.clear()
    gc.collect()
    try:
        if "plugin:basic" in enabled:
            register_bundled("basic", registry)
            registry._plugin_exports["basic"] = {}
        if "plugin:solve" in enabled:
            register_bundled("solve", registry)
            registry._plugin_exports["solve"] = {}
        if "plugin:trig" in enabled:
            register_bundled("trig", registry)
            registry._plugin_exports["trig"] = {}
        register_builtins(registry, enabled)
        registry.plugin_files = known_files
        registry.angle_mode = angle_mode
        return registry
    except MemoryError:
        registry.clear_for_reload()
        gc.collect()
        register_builtins(registry, enabled)
        registry.angle_mode = angle_mode
        raise


def _reload_external_functions_after_reclaim(
        nav, active_screen, settings, registry):
    """Give source add-ons the full cold-operation recovery envelope."""
    nav.prepare_memory_intensive_operation(active_screen)
    gc.collect()
    try:
        return _reload_functions(settings, registry)
    finally:
        _drop_function_loader_module()
        gc.collect()


def _scan_function_files_after_reclaim(nav, active_screen):
    """Refresh the bounded filename catalog without executing add-ons."""
    nav.prepare_memory_intensive_operation(active_screen)
    gc.collect()
    try:
        from calc.loader import list_function_files
        return list_function_files()
    finally:
        _drop_function_loader_module()
        gc.collect()


def _present_first_ui_frame(nav, root):
    """Replace the 8/8 splash before diagnostics may return."""
    nav.boot(root)
    nav.present_current()


def _draw_crash(display, error):
    """Render crash info directly to display. Uses only 8x8 text — no font dependency."""
    import sys
    try:
        sys.print_exception(error)
    except Exception:
        pass

    display.clear_buffers(0)
    display.fill_rectangle(0, 0, 210, 64, 3)
    display.draw_rectangle(4, 2, 202, 60, 15)

    err_type = type(error).__name__
    if len(err_type) > 26:
        err_type = err_type[:25] + "~"
    display.draw_text8x8(8, 5, "CRASH: " + err_type, gs=15)

    # Word-wrap message across 4 lines, 25 chars each
    msg = str(error) or "(no message)"
    y = 18
    for _ in range(4):
        if not msg:
            break
        chunk = msg[:25]
        if len(msg) > 25:
            space = msg.rfind(' ', 0, 25)
            if space > 10:
                chunk = msg[:space]
                msg = msg[space:].lstrip()
            else:
                msg = msg[25:]
        else:
            msg = ""
        display.draw_text8x8(8, y, chunk, gs=15)
        y += 10

    display.draw_text8x8(8, 54, "Press any key to restart", gs=10)
    display.present()


# ── Screen navigation ───────────────────────────────────────────

# The resident acceptance controller imports these only after acquiring the
# already-constructed pages through ApplicationBinding.  Values follow that
# binding's fixed order, excluding its root Main Menu.
PAGE_SCENARIO_CALCULATOR = 1
PAGE_SCENARIO_PLOT = 2
PAGE_SCENARIO_ADDONS = 3
PAGE_SCENARIO_STOPWATCH = 4
PAGE_SCENARIO_SETTINGS = 5
PAGE_SCENARIO_ABOUT = 6
PAGE_SCENARIO_LETTERS = 7
PAGE_SCENARIO_FUNCTION_PICKER = 8
PAGE_SCENARIO_VARIABLE_PANEL = 9

# Keep the screen vocabulary available without adding alternate action values.
PAGE_SCENARIO_FUNCTION_PANEL = PAGE_SCENARIO_ADDONS
PAGE_SCENARIO_CATALOG = PAGE_SCENARIO_FUNCTION_PICKER
PAGE_SCENARIO_VARIABLES = PAGE_SCENARIO_VARIABLE_PANEL

_PAGE_SCENARIO_READY = 0
_PAGE_SCENARIO_LEASE_OPEN = 1
_PAGE_SCENARIO_CHILD_ACTIVE = 2
_PAGE_SCENARIO_LEASE_DONE = 3
_PAGE_SCENARIO_LEASE_CLOSED = 4


if hasattr(gc, "mem_free"):
    _NavPageScenarioTransaction = None
else:
    from nav_scenario import _NavPageScenarioTransaction


class Nav:
    """Resident-page navigation and rebuildable-resource ownership."""

    __slots__ = (
        "memory", "renderer", "stack", "_managed", "_input_locked",
        "_collect_pending", "last_present_us", "_page_scenario_transaction",
        "_active_screen")

    def __init__(self, display, font_small, registry, memory=None):
        from ui.memory import MemoryManager
        from ui.renderer import Renderer
        from ui.sidebar import Sidebar
        self.memory = memory or MemoryManager()
        self.renderer = Renderer(
            display, Sidebar(font_small, registry), memory=self.memory)
        self.stack = []
        self._managed = ()
        self._input_locked = False
        self._collect_pending = False
        self.last_present_us = 0
        self._page_scenario_transaction = None
        self._active_screen = None

    def register_screens(self, screens):
        self._managed = tuple(screens)

    @property
    def current(self):
        return self.stack[-1]

    def boot(self, screen):
        self.stack[:] = [screen]
        self._active_screen = None
        self._activate_screen(screen)

    def _activate_screen(self, screen):
        screen.activate()
        self._active_screen = screen

    def _restore_active_screen(self, screen, primary_error=None):
        """Best-effort rollback with memory-pressure precedence."""
        # A failed deactivate leaves the old page's prior active marker stale.
        # Clear it before a best-effort reactivation so the next navigation
        # retries restoration instead of assuming the page is usable.
        self._active_screen = None
        try:
            self._activate_screen(screen)
        except MemoryError:
            # Memory pressure must remain visible.  An already-primary OOM
            # wins by identity; a rollback OOM upgrades an ordinary failure.
            if (primary_error is not None
                    and not isinstance(primary_error, MemoryError)):
                raise
            return False
        except BaseException:
            return False
        return True

    def _ensure_current_active(self):
        current = self.current
        if self._active_screen is current:
            return True
        self._activate_screen(current)
        return True

    def _release_screen(self, screen):
        """Release one page's rebuildable state without knowing its internals."""
        releaser = getattr(screen, "release_memory", None)
        return bool(releaser()) if releaser is not None else False

    def _release_departing_screen(self, screen):
        """Free a leaving page and its optional Plot workspace as one action."""
        released = self._release_screen(screen)
        if getattr(screen, "requires_plot_workspace", False):
            released = self.memory.release_plot_workspace() or released
        return released

    def _release_registered_screens(self, active_screen=None):
        """Release all inactive resident-page caches through the common seam."""
        released = False
        for screen in self._managed:
            if screen is active_screen:
                continue
            released = self._release_screen(screen) or released
        return released

    def _deactivate_stack(self):
        """Deactivate the visible navigation path from leaf to root."""
        for screen in reversed(self.stack):
            screen.deactivate()

    def _has_prepared_page_scenario(self, screen, transaction):
        """Check Nav-owned identity before skipping a child activation."""
        return (isinstance(transaction, _NavPageScenarioTransaction)
                and transaction is self._page_scenario_transaction
                and transaction._nav is self
                and transaction._prepared_screen is screen
                and transaction._child_lease is not None
                and transaction._phase == _PAGE_SCENARIO_LEASE_OPEN)

    def _go_to(self, screen, trigger_event=None, prepared_transaction=None):
        if screen is self.current:
            self._ensure_current_active()
            return
        if prepared_transaction is not None:
            if not self._has_prepared_page_scenario(
                    screen, prepared_transaction):
                raise RuntimeError("Prepared page scenario lease is not active")
        old = self.current
        self._ensure_current_active()
        self._active_screen = None
        try:
            old.deactivate()
        except BaseException as primary_error:
            self._restore_active_screen(old, primary_error)
            raise
        try:
            released = self._release_departing_screen(old)
        except BaseException as primary_error:
            self._restore_active_screen(old, primary_error)
            raise
        if released:
            self._collect_pending = True
        self.stack.append(screen)
        try:
            if prepared_transaction is None:
                self._activate_screen(screen)
            else:
                if not self._has_prepared_page_scenario(
                        screen, prepared_transaction):
                    raise RuntimeError("Prepared page scenario lease is not active")
                self._active_screen = screen
        except BaseException as primary_error:
            self.stack.pop()
            self._restore_active_screen(old, primary_error)
            raise

    def go_to(self, screen, trigger_event=None):
        self._require_ordinary_navigation()
        self._go_to(screen, trigger_event)

    def _go_to_prepared(self, screen, transaction, trigger_event=None):
        """Navigate through the active transaction's exact prepared child."""
        if (not self._has_prepared_page_scenario(screen, transaction)
                or len(self.stack) != 1
                or self.stack[0] is not transaction._root
                or self.current is not transaction._root):
            raise RuntimeError("Prepared page scenario lease is not active")
        self._go_to(
            screen, trigger_event, prepared_transaction=transaction)

    def _go_back_prepared(self, transaction, trigger_event=None):
        """Return only through the active transaction's known page path."""
        if (not isinstance(transaction, _NavPageScenarioTransaction)
                or transaction is not self._page_scenario_transaction
                or transaction._nav is not self):
            raise RuntimeError("Page scenario transaction is not active")
        if len(self.stack) == 1:
            if (self.current is not transaction._root
                    or transaction._phase not in (
                        _PAGE_SCENARIO_READY,
                        _PAGE_SCENARIO_LEASE_CLOSED)):
                raise RuntimeError("Page scenario navigation state is unexpected")
            self._ensure_current_active()
            return
        if (transaction._phase != _PAGE_SCENARIO_LEASE_CLOSED
                or len(self.stack) != 2
                or self.stack[0] is not transaction._root
                or self.current is not transaction._prepared_screen):
            raise RuntimeError("Page scenario navigation state is unexpected")
        self._go_back(trigger_event)

    def _require_ordinary_navigation(self):
        """Keep public navigation from escaping an active page lease."""
        if self._page_scenario_transaction is not None:
            raise RuntimeError("Page scenario transaction owns navigation")

    def open_page_scenario_transaction(self, canonical_screens):
        """Open the controller-only bounded auxiliary-page primitive."""
        if self._page_scenario_transaction is not None:
            raise RuntimeError("Page scenario transaction is already active")
        global _NavPageScenarioTransaction
        transaction_type = _NavPageScenarioTransaction
        if transaction_type is None:
            from nav_scenario import (
                _NavPageScenarioTransaction as transaction_type)
            _NavPageScenarioTransaction = transaction_type
        transaction = transaction_type(self, canonical_screens)
        self._page_scenario_transaction = transaction
        return transaction

    def _go_back(self, trigger_event=None):
        if len(self.stack) <= 1:
            self._ensure_current_active()
            return
        old = self.stack[-1]
        parent = self.stack[-2]
        self._ensure_current_active()
        self._active_screen = None
        try:
            old.deactivate()
        except BaseException as primary_error:
            self._restore_active_screen(old, primary_error)
            raise
        try:
            released = self._release_departing_screen(old)
        except BaseException as primary_error:
            self._restore_active_screen(old, primary_error)
            raise
        if released:
            self._collect_pending = True
        self.stack.pop()
        try:
            self._activate_screen(parent)
        except BaseException as primary_error:
            # A retry of the destination may restore the now-current root.
            # If it cannot, put the departed child back so a later return can
            # retry from one coherent visible stack.
            parent_restore_error = None
            try:
                parent_restored = self._restore_active_screen(
                    parent, primary_error)
            except MemoryError as error:
                parent_restore_error = error
                parent_restored = False
            if parent_restored:
                raise
            self.stack.append(old)
            if parent_restore_error is not None:
                self._restore_active_screen(old, parent_restore_error)
                raise parent_restore_error
            self._restore_active_screen(old, primary_error)
            raise

    def go_back(self, trigger_event=None):
        self._require_ordinary_navigation()
        self._go_back(trigger_event)

    def poll_event(self, keyboard):
        # The bounded page scenario owns its own execution steps.  Do not
        # consume queued keypad edges while its lease is open.
        if self._page_scenario_transaction is not None:
            return None
        if self._input_locked:
            if keyboard.any_pressed():
                return None
            self._input_locked = False
        return keyboard.pop_key_event()

    def present_current(self, now=None):
        presented = self.renderer.present(self.current)
        self.last_present_us = self.renderer.last_present_us
        return presented

    def settle_current(self):
        return self.current.settle_step() or 0

    def prepare_memory_intensive_operation(self, active_screen):
        self._release_registered_screens(active_screen)
        self.memory.release_plot_workspace()
        self.memory.collect()
        self._collect_pending = False

    def collect_pending(self):
        if not self._collect_pending:
            return False
        self.memory.collect()
        self._collect_pending = False
        return True

    def reset(self, root):
        # Recovery must quiesce the live navigation path before it invalidates
        # its derived state.  This fixed sequence leaves no page holding a
        # stale cache or input focus when root becomes visible again.
        self._deactivate_stack()
        self._release_registered_screens()
        self.memory.release_plot_workspace()
        self._collect_pending = False
        self.renderer.invalidate()
        self._input_locked = True
        self.memory.collect()
        self.stack[:] = [root]
        self._active_screen = None
        self._activate_screen(root)


def main(run_loop=True, runtime_mode="resident", publish_runtime=True):
    from runtime_handle import set_resident_runtime, ApplicationBinding
    if not run_loop:
        from runtime_materialize import RuntimeHandle
    if publish_runtime:
        set_resident_runtime(None)

    # ============================================================
    # Phase 1: Display FIRST — show splash immediately
    # ============================================================
    metrics.start_boot()
    display = _init_display()
    metrics.mark_boot("display")
    _boot_progress(
        display, 1, 8, "Loading keyboard...", "Keyboard()")

    # ============================================================
    # Phase 2: Build the resident interface while showing progress.
    # Each step is wrapped — ordinary failures can show an error and use a
    # fallback, while MemoryError always reaches the boot recovery seam.
    # ============================================================

    # Keyboard (critical — halt on failure)
    try:
        from input.keyboard import Keyboard, get_key_label
        kb = Keyboard()
        _boot_progress(
            display, 2, 8, "Loading interface...", "built-in 8x8 font")
    except MemoryError:
        raise
    except Exception as e:
        _boot_fail(display, 2, 8, "Keyboard", e)
        raise  # can't run without keyboard
    metrics.mark_boot("keyboard")

    # Every page has a fixed 8x8 path.  Keep that path canonical on the
    # constrained target instead of retaining two packed font payloads.
    font_main = None
    font_small = None
    metrics.mark_boot("fonts")
    _boot_progress(
        display, 3, 8, "Loading settings...", "load_settings()")

    # Settings (fallback: defaults)
    try:
        from utils.storage import load_settings
        settings = load_settings()
        _boot_progress(
            display, 4, 8, "Loading variables...", "load_vars()")
    except MemoryError:
        raise
    except Exception as e:
        _boot_fail(display, 4, 8, "Settings", e)
        settings = {"angle_mode": 0, "enabled_functions": ["basic", "trig", "math", "list"], "diagnostics": False, "brightness": 100, "display_digits": 4}
    display.set_brightness(settings.get("brightness", 100))
    from utils.storage import DeferredStorage
    persistence = DeferredStorage()
    metrics.mark_boot("settings")
    # Variables (fallback: empty dict)
    try:
        from utils.storage import load_vars
        vars_dict = load_vars()
        _boot_progress(
            display, 5, 8, "Loading functions...", "_reload_functions()")
    except MemoryError:
        raise
    except Exception as e:
        _boot_fail(display, 5, 8, "Vars", e)
        vars_dict = {}
    metrics.mark_boot("variables")

    # Functions (fallback: built-in groups only)
    try:
        registry = _reload_functions(settings)
        registry.angle_mode = settings.get("angle_mode", 0)
        _boot_progress(
            display, 6, 8, "Loading screens...", "import screens.*")
    except MemoryError:
        raise
    except Exception as e:
        _boot_fail(display, 6, 8, "Functions", e)
        from calc.functions import build_registry
        registry = build_registry(["basic", "trig", "math", "list"])
        registry.angle_mode = settings.get("angle_mode", 0)
    # Drop the rebuildable loader and its temporary import graph before the
    # first resident page module is loaded.  This is a boot-only collection;
    # input and frame paths remain collection-free.
    _release_function_loader()
    metrics.mark_boot("functions")
    if hasattr(metrics, "release_boot_samples"):
        metrics.release_boot_samples()
    gc.collect()

    from utils.power import AWAKE, WOKE, DisplayPower

    nav = Nav(display, font_small, registry)

    # Screens (import + build — skip broken ones)
    try:
        from screens.main_menu import MainMenu
        # About, Calculator, FunctionPanel and Plot need their fixed class
        # tables before later imports split the remaining runs.  This remains
        # a synchronous resident-page startup; no import reaches input paths.
        from screens.about import AboutScreen
        gc.collect()
        from screens.calculator import CalculatorScreen
        gc.collect()
        from screens.function_panel import FunctionPanel
        gc.collect()
        from screens.plot import PlotScreen
        gc.collect()
        # LetterPanel must precede FunctionPicker and the remaining class
        # tables: otherwise no 136-byte run remains for its class table.
        from screens.letter_panel import LetterPanel
        gc.collect()
        from screens.function_picker import FunctionPicker
        gc.collect()
        from screens.stopwatch import StopwatchScreen, STOPWATCH_FRAME_MS
        gc.collect()
        from screens.settings import SettingsScreen
        gc.collect()
        from screens.variable_panel import VariablePanel
        gc.collect()
        # Plot owns the largest remaining child-object construction.  Reserve
        # it only after every resident module import frame has been reclaimed.
        plot_screen = PlotScreen(
            None, None, registry, memory=nav.memory)
        gc.collect()
        # Calculator gets coalesced import-frame space while the already
        # reserved Plot object remains resident.
        calc_screen = CalculatorScreen(
            font_main, font_small, registry, vars_dict,
            display_digits=settings.get("display_digits", 4))
        # Reserve the shared Cursor and both fixed Menu blocks before compact
        # overlays and root rows split the remaining runs.
        from ui.cursor import Cursor
        shared_menu_cursor = Cursor(2, 15, mode=0)
        from ui.menu import Menu
        settings_menu = Menu(
            0, 13, 210, 4, 10, shared_menu_cursor, ())
        function_panel_menu = Menu(
            0, 13, 210, 4, 10, shared_menu_cursor, ())
        # These three compact overlays depend only on Calculator.  Reserve
        # their fixed state lists before root rows and menu objects fragment
        # the remaining runs.
        letter_panel = LetterPanel(None, calc_screen.input_box)
        func_picker = FunctionPicker(None, calc_screen)
        var_panel = VariablePanel(None, calc_screen)
        main_menu = MainMenu()
        stopwatch = StopwatchScreen(font_small)
        gc.collect()
        func_panel = FunctionPanel(
            persistence.request_settings, settings,
            registry.plugin_dependencies, registry.plugin_files)
        settings_screen = SettingsScreen(
            None, display, settings, None,
            request_save=persistence.request_settings,
            on_display_digits_change=calc_screen.set_display_digits,
            build_rows=False, menu=settings_menu)
        func_panel._menu = function_panel_menu
        # Settings rows are still unbuilt, so bind About after all three fixed
        # Menu/Settings blocks have secured their contiguous heap runs.
        # Auxiliary pages use the display's built-in 8x8 font to avoid SD
        # glyph reads and cache growth on the input path.
        about = AboutScreen(None, VERSION)
        settings_screen._state[1] = about
    except MemoryError:
        # A boot-time page OOM can leave too little contiguous heap for the
        # recovery UI's framebuffer.  Quiesce the already-initialized panel
        # before the original exception reaches the boot supervisor.
        try:
            display.sleep()
        except Exception:
            pass
        raise
    except Exception as e:
        _draw_crash(display, e)
        raise
    metrics.mark_boot("screen_imports")
    # Imports leave reclaimable loader objects interleaved with resident code.
    # Coalesce them before constructing the remaining screens.
    gc.collect()

    # Bind the fixed resident topology before root labels split the remaining
    # runs into small tuples. Acceptance retains these exact existing pages;
    # it never constructs a parallel screen graph.
    resident_screens = (
        main_menu, calc_screen, plot_screen, func_panel, stopwatch,
        settings_screen, about, letter_panel, func_picker, var_panel)
    nav.register_screens(resident_screens)
    gc.collect()
    application_binding = ApplicationBinding(
        resident_screens, registry, settings, persistence, nav=nav)
    from ui.memory import plot_curve_buffer_size
    runtime = application_binding if run_loop else RuntimeHandle(
        nav,
        main_menu,
        resident_screens,
        mode=runtime_mode,
        version=VERSION,
        optional_buffer_size=plot_curve_buffer_size(display.height),
        scenario_adapter=None,
        application_binding=application_binding,
    )

    try:
        # All resident page objects now exist. Keep inactive, rebuildable rows
        # empty until their page is activated after this construction frame has
        # returned; only the visible root menu needs labels during boot.
        func_panel.set_load_errors(registry.plugin_errors)
        main_menu.add_screen("Calculator", calc_screen)
        main_menu.add_screen("Plot", plot_screen)
        main_menu.add_screen("Function Panel", func_panel)
        main_menu.add_screen("Stopwatch", stopwatch)
        main_menu.add_screen("Settings", settings_screen)
        gc.collect()
    except MemoryError:
        try:
            display.sleep()
        except Exception:
            pass
        raise
    except Exception as e:
        _draw_crash(display, e)
        raise
    metrics.mark_boot("ui_ready")
    # ============================================================
    # Phase 3: Main loop
    # ============================================================
    from ui.element import SETTLE_COLLECT, SETTLE_MORE, SETTLE_REDRAW

    try:
        _present_first_ui_frame(nav, main_menu)
    except MemoryError:
        try:
            display.sleep()
        except Exception:
            pass
        raise
    except Exception as e:
        _draw_crash(display, e)
        raise
    if not run_loop:
        if publish_runtime:
            set_resident_runtime(runtime)
        return runtime
    _frame = 0
    _loop_started = time.ticks_ms()
    scheduler = FrameScheduler(
        _loop_started, background_idle_ms=BACKGROUND_IDLE_MS,
        sidebar_refresh_ms=SIDEBAR_REFRESH_MS)
    # The first visible frame uses Sidebar's fixed placeholder.  Let the
    # first quiet loop acquire ADC data instead of adding hardware allocation
    # and latency to boot or to an input-driven present.
    scheduler.force_sidebar_poll(_loop_started)
    diagnostics = bool(settings.get("diagnostics", False))
    _diag_last = time.ticks_ms()
    _diag_render_us = 0
    _diag_present_us = 0
    _diag_frames = 0
    _function_reload_pending = False
    _function_scan_pending = False
    _input_visual_changed = False
    power = DisplayPower(
        display, int(settings.get("sleep_timeout_s", 180)) * 1000)

    def _handle_event(event):
        nonlocal _function_reload_pending, _function_scan_pending
        nonlocal _input_visual_changed
        cur = nav.current
        page_was_modal = _blocks_global_shortcuts(cur)
        if event is not None:
            if diagnostics:
                metrics.record_input()
                print("INPUT page=" + cur.__class__.__name__
                      + " row=" + str(event[0])
                      + " col=" + str(event[1])
                      + " shift=" + str(int(event[2]))
                      + " key=" + get_key_label(
                          event[0], event[1], event[2]))
            if _open_context_letter_panel(cur, event, letter_panel, nav):
                _input_visual_changed = True
                return True
        elif not _page_update_requested(kb, None):
            return False

        result = cur.update(kb, event)
        if diagnostics and result is not None:
            print("ACTION page=" + cur.__class__.__name__
                  + " result=" + str(result))

        if _global_angle_allowed(event, page_was_modal, result):
            _toggle_angle_mode(
                registry, settings, persistence, nav.renderer, plot_screen)
            _input_visual_changed = True
            return True

        if result == "BACK":
            nav.go_back(event)
        elif result == "FUNC_PANEL_DONE":
            _function_scan_pending = False
            func_panel.set_plugin_scan_active(False)
            nav.go_back(event)
            _function_reload_pending = True
        elif result == "FUNC_PANEL_RESCAN":
            # Actual bounded directory enumeration begins only in quiet work.
            # Source executes once, later, if the selection is committed.
            _function_scan_pending = True
            # A repeated request is real input work (and must reset quiet
            # grace), but once the fixed "Scanning..." footer is already
            # visible it changes no pixels.  Do not turn it into a phantom
            # input frame merely because the semantic request is non-null.
            if func_panel.set_plugin_scan_active(True):
                _input_visual_changed = True
            return _input_visual_changed
        elif result in (
                "FUNC_PICKER_DONE", "LETTER_DONE", "VAR_PANEL_DONE",
                "FUNC_PANEL_CANCEL"):
            if result == "FUNC_PANEL_CANCEL":
                _function_scan_pending = False
                func_panel.set_plugin_scan_active(False)
            nav.go_back(event)
        elif result == "FUNC_PICKER":
            nav.go_to(func_picker, event)
        elif result == "VARIABLE_PANEL":
            nav.go_to(var_panel, event)
        elif _navigate_registered_page(nav, cur, result, event):
            pass
        changed = result is not None
        if changed:
            _input_visual_changed = True
        return changed

    if publish_runtime:
        __import__("runtime_handle")._resident_runtime = runtime
    while True:
        try:
            kb.scan()
            now = time.ticks_ms()
            power_state = power.update(now, kb.any_pressed())
            if power_state != AWAKE:
                kb.discard_pending_events()
                if power_state == WOKE:
                    scheduler.force_render(now)
                    nav.renderer.invalidate_sidebar()
                # Matrix keys cannot wake ESP32 deep sleep reliably, so keep a
                # low-cost scan loop while the OLED controller is asleep.
                time.sleep_ms(SLEEP_SCAN_MS)
                continue

            _frame += 1
            _input_visual_changed = False
            batch_count = _drain_input_batch(nav, kb, _handle_event)
            hold_changed = False
            hold_active = False
            if batch_count == 0:
                hold_active = _page_update_requested(kb, None)
                if hold_active:
                    hold_changed = _handle_event(None)
            # Physical holds reset the quiet grace even when they reach a
            # boundary and produce no pixels.  That prevents a release from
            # immediately starting SD/GC work after a long ignored hold.
            input_activity = bool(batch_count or hold_active)
            input_changed = _input_visual_changed
            now = time.ticks_ms()
            if input_activity:
                scheduler.note_input(now)
            if input_changed:
                scheduler.request_render()

            cur = nav.current
            quiet = (batch_count == 0
                     and not hold_changed
                     and not kb.has_pending_events()
                     and not kb.any_pressed())
            if (scheduler.sidebar_poll_due(now, quiet)
                    and nav.renderer.poll_sidebar()):
                scheduler.request_render()
            stopwatch_running = cur is stopwatch and stopwatch._clock[1]
            continuous = stopwatch_running
            continuous_frame_ms = (
                STOPWATCH_FRAME_MS if stopwatch_running else 0)
            needs_render = scheduler.should_present(
                now, continuous, input_changed, continuous_frame_ms)

            if needs_render:
                render_started = time.ticks_us()
                if nav.present_current(now):
                    scheduler.mark_presented(now)
                    _diag_present_us += nav.last_present_us
                    render_elapsed = time.ticks_diff(
                        time.ticks_us(), render_started)
                    _diag_render_us += render_elapsed
                    if diagnostics:
                        metrics.record_frame(render_elapsed)
                    _diag_frames += 1
                    # Capture edges that occurred during the OLED transfer
                    # before any GC, SD write or lazy rebuild may start.
                    kb.scan()
                    now = time.ticks_ms()
                else:
                    # A page can explicitly report no pixel damage.  That is
                    # not a physical frame and must not skew render metrics.
                    scheduler.clear_render_request()

            if (diagnostics and quiet
                    and time.ticks_diff(now, _diag_last) >= 5000):
                heap_free = gc.mem_free() if hasattr(gc, "mem_free") else -1
                divisor = max(1, _diag_frames)
                print("PERF frames=" + str(_diag_frames)
                      + " render_us=" + str(_diag_render_us // divisor)
                      + " present_us=" + str(_diag_present_us // divisor)
                      + " heap_free=" + str(heap_free))
                _diag_last = now
                _diag_render_us = 0
                _diag_present_us = 0
                _diag_frames = 0

            if calc_screen.context.dirty and calc_screen.context.consume_dirty():
                persistence.request_vars(calc_screen.vars)

            settle_flags = nav.settle_current() if quiet else 0
            if settle_flags & SETTLE_COLLECT:
                gc_started = time.ticks_us()
                gc.collect()
                if diagnostics:
                    metrics.record_gc(
                        time.ticks_diff(time.ticks_us(), gc_started))
            if settle_flags & SETTLE_REDRAW:
                scheduler.request_render()

            # Potentially blocking work gets a grace period after input.
            if (quiet
                    and not (settle_flags & SETTLE_MORE)
                    and scheduler.background_due(now)):
                if _function_reload_pending:
                    # Consume the request before beginning the allocation-heavy
                    # transaction.  A MemoryError must not retry the same SD
                    # reload every quiet interval after the root recovery.
                    _function_reload_pending = False
                    reloaded = _reload_functions_after_reclaim(
                        nav, nav.current, settings, registry)
                    if reloaded is None:
                        func_panel.rollback_plugin_reload()
                    else:
                        func_panel.confirm_plugin_reload()
                    func_panel.set_plugin_catalog(
                        registry.plugin_dependencies)
                    func_panel.set_load_errors(registry.plugin_errors)
                    scheduler.request_render()
                elif _function_scan_pending:
                    _function_scan_pending = False
                    files = _scan_function_files_after_reclaim(
                        nav, func_panel)
                    func_panel.adopt_plugin_files(files)
                    func_panel.set_plugin_scan_active(False)
                    scheduler.request_render()
                elif nav.collect_pending():
                    pass
                elif _frame % 256 == 0:
                    heap_reporter = getattr(gc, "mem_free", None)
                    heap_free = (
                        heap_reporter() if heap_reporter is not None else -1)
                    if (heap_reporter is None
                            or heap_free < 12 * 1024):
                        gc_started = time.ticks_us()
                        gc.collect()
                        if diagnostics:
                            metrics.record_gc(
                                time.ticks_diff(
                                    time.ticks_us(), gc_started))
                else:
                    persisted = persistence.flush(now)
                    if persisted is not None:
                        consume_visual = getattr(
                            nav.current, "consume_persist_visual_change", None)
                        if consume_visual is not None and consume_visual():
                            scheduler.request_render()
                    if persisted is not None and not persisted[1]:
                        calc_screen.set_storage_error("Not saved - check SD")
                        scheduler.request_render()

            time.sleep_ms(IDLE_LOOP_SLEEP_MS)

        except MemoryError:
            # Memory pressure returns to a usable root and forgets snapshots.
            _function_reload_pending = False
            _function_scan_pending = False
            func_panel.set_plugin_scan_active(False)
            func_panel.rollback_plugin_reload()
            if diagnostics:
                # Never stringify an exception while the heap is exhausted.
                print("MEMORY_RECOVER")
            try:
                power.reset(time.ticks_ms())
            except Exception:
                pass
            gc.collect()
            nav.reset(main_menu)
            scheduler.reset(time.ticks_ms(), force_render=True)

        except Exception as e:
            # Crash landing: reclaim optional rasters before allocating even
            # the small diagnostic screen, then wait for acknowledgement.
            try:
                power.reset(time.ticks_ms())
            except Exception:
                pass
            gc.collect()
            _draw_crash(display, e)

            time.sleep_ms(300)
            while True:
                kb.scan()
                if kb.pop_key_event() is not None:
                    break
                time.sleep_ms(20)

            # The acknowledgement key and any simultaneous keys must be fully
            # released before the root page can receive input again.
            while kb.any_pressed():
                kb.scan()
                kb.discard_pending_events()
                time.sleep_ms(20)

            nav.reset(main_menu)
            scheduler.reset(time.ticks_ms(), force_render=True)


if __name__ == "__main__":
    main()
