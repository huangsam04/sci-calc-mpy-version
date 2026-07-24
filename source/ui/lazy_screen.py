"""Allocation-bounded proxy that owns at most one active page instance."""
import sys

from ui.theme import (
    SHELL_ABOUT, SHELL_FUNCTION_PANEL, SHELL_FUNCTION_PICKER,
    SHELL_LETTERS, SHELL_PLOT, SHELL_SETTINGS, SHELL_STOPWATCH,
    SHELL_VARIABLE_PANEL, draw_page_shell)
from ui.residency import SETTLE_MORE, SETTLE_REDRAW


DEFAULT_LIST = SHELL_FUNCTION_PANEL
DEFAULT_PLOT = SHELL_PLOT
DEFAULT_STOPWATCH = SHELL_STOPWATCH
DEFAULT_SETTINGS = SHELL_SETTINGS
DEFAULT_LETTERS = SHELL_LETTERS
DEFAULT_ABOUT = SHELL_ABOUT
DEFAULT_FUNCTION_PANEL = SHELL_FUNCTION_PANEL
DEFAULT_FUNCTION_PICKER = SHELL_FUNCTION_PICKER
DEFAULT_VARIABLE_PANEL = SHELL_VARIABLE_PANEL

BUILD_ABOUT = 0
BUILD_SETTINGS = 1
BUILD_FUNCTION_PANEL = 2
BUILD_STOPWATCH = 3
BUILD_LETTERS = 4
BUILD_FUNCTION_PICKER = 5
BUILD_VARIABLE_PANEL = 6
BUILD_PLOT = 7

_SCREEN_MODULES = (
    "screens.about",
    "screens.settings",
    "screens.function_panel",
    "screens.stopwatch",
    "screens.letter_panel",
    "screens.function_picker",
    "screens.variable_panel",
    "screens.plot",
)


class ScreenFactory:
    """Build disposable page instances from one shared dependency record."""

    __slots__ = ("_deps", "_about", "_letter_input")

    def __init__(self, font, small_font, display, settings, persistence,
                 calculator, registry, memory):
        self._deps = (
            font, small_font, display, settings, persistence,
            calculator, registry, memory)
        self._about = None
        self._letter_input = calculator.input_box

    def set_about(self, screen):
        self._about = screen

    def set_letter_input(self, input_box):
        self._letter_input = input_box

    def release_screen(self, kind):
        name = _SCREEN_MODULES[kind]
        module = sys.modules.get("screens")
        leaf = name[8:]
        if module is not None and hasattr(module, leaf):
            delattr(module, leaf)
        try:
            del sys.modules[name]
        except KeyError:
            pass

    def __call__(self, kind):
        (font, small_font, display, settings, persistence,
         calculator, registry, memory) = self._deps
        if kind == BUILD_ABOUT:
            from screens.about import AboutScreen
            from version import VERSION
            return AboutScreen(font, VERSION)
        if kind == BUILD_SETTINGS:
            from screens.settings import SettingsScreen
            return SettingsScreen(
                font, display, settings, self._about,
                request_save=persistence.request_settings,
                on_display_digits_change=calculator.set_display_digits)
        if kind == BUILD_FUNCTION_PANEL:
            from screens.function_panel import FunctionPanel
            panel = FunctionPanel(
                None, request_settings=persistence.request_settings,
                settings=settings,
                plugin_functions=registry.plugin_functions,
                plugin_dependencies=registry.plugin_dependencies)
            panel.set_load_errors(registry.plugin_errors)
            return panel
        if kind == BUILD_STOPWATCH:
            from screens.stopwatch import StopwatchScreen
            return StopwatchScreen(font)
        if kind == BUILD_LETTERS:
            from screens.letter_panel import LetterPanel
            return LetterPanel(font, self._letter_input)
        if kind == BUILD_FUNCTION_PICKER:
            from screens.function_picker import FunctionPicker
            return FunctionPicker(font, calculator)
        if kind == BUILD_VARIABLE_PANEL:
            from screens.variable_panel import VariablePanel
            return VariablePanel(font, calculator)
        from screens.plot import PlotScreen
        return PlotScreen(font, small_font, registry, memory=memory)


class LazyScreen:
    """Keep a page description resident while its full instance is disposable."""

    __slots__ = (
        "_spec", "_instance", "_state", "_residency_error", "_font")

    def __init__(self, key, title, default_kind, factory,
                 factory_key=None, requires_plot_workspace=False,
                 font=None):
        self._spec = (
            key, title, default_kind, factory, factory_key,
            requires_plot_workspace)
        self._instance = None
        self._state = None
        self._residency_error = ""
        self._font = font

    @property
    def swap_key(self):
        return self._spec[0]

    @property
    def transition_title(self):
        return self._spec[1]

    @property
    def requires_plot_workspace(self):
        return self._spec[5]

    def loaded(self):
        return self._instance

    def get_loaded_attr(self, name, default=None):
        screen = self._instance
        return getattr(screen, name, default) if screen is not None else default

    def _load(self):
        if self._instance is None:
            factory = self._spec[3]
            factory_key = self._spec[4]
            try:
                self._instance = (factory() if factory_key is None
                                  else factory(factory_key))
            except Exception:
                release_screen = getattr(factory, "release_screen", None)
                if factory_key is not None and release_screen is not None:
                    release_screen(factory_key)
                raise
        return self._instance

    def activate(self):
        self._load().activate()

    def activate_default(self):
        # The proxy itself owns the allocation-bounded default shell.
        pass

    def animation_children(self):
        screen = self._instance
        if screen is None:
            return ()
        children = getattr(screen, "animation_children", None)
        return children() if children is not None else ()

    def snapshot_state(self):
        screen = self._instance
        if screen is not None:
            snapshotter = getattr(screen, "snapshot_state", None)
            if snapshotter is not None:
                return snapshotter()
        return self._state or {}

    def restore_state(self, state):
        self._state = state

    def settle_step(self):
        screen = self._instance
        if screen is None:
            if self._residency_error:
                return 0
            screen = self._load()
            activator = getattr(screen, "activate_default", None)
            if activator is None:
                activator = getattr(screen, "activate", None)
            if activator is not None:
                activator()
            state = self._state
            self._state = None
            if state is not None:
                restorer = getattr(screen, "restore_state", None)
                if restorer is not None:
                    restorer(state)
            return SETTLE_REDRAW | SETTLE_MORE
        stepper = getattr(screen, "settle_step", None)
        return int(stepper() or 0) if stepper is not None else 0

    def deactivate(self):
        # release_memory() deactivates before dropping the owned instance.
        pass

    def release_memory(self):
        screen = self._instance
        if screen is None:
            return False
        deactivator = getattr(screen, "deactivate", None)
        if deactivator is not None:
            deactivator()
        releaser = getattr(screen, "release_memory", None)
        if releaser is not None:
            releaser()
        self._instance = None
        self._state = None
        factory = self._spec[3]
        factory_key = self._spec[4]
        release_screen = getattr(factory, "release_screen", None)
        if factory_key is not None and release_screen is not None:
            release_screen(factory_key)
        return True

    def reset_state(self):
        self.release_memory()
        self._state = None

    def show_residency_error(self, message):
        self._residency_error = str(message)
        screen = self._instance
        reporter = getattr(screen, "show_residency_error", None)
        if reporter is not None:
            reporter(message)

    def clear_residency_error(self):
        self._residency_error = ""
        screen = self._instance
        clearer = getattr(screen, "clear_residency_error", None)
        if clearer is not None:
            clearer()

    def draw_transition_default(self, display):
        draw_page_shell(display, self._spec[2], self._font)

    def draw(self, display):
        screen = self._instance
        if screen is None:
            self.draw_transition_default(display)
        else:
            screen.draw(display)
        if self._residency_error:
            display.fill_rectangle(0, 45, 210, 19, 0)
            label = ("PAGE error - reset"
                     if self._residency_error.startswith("Page ")
                     else "SWAP error - page reset")
            display.draw_text8x8(3, 47, label, gs=15)

    def update(self, keyboard, event=None):
        screen = self._instance
        if screen is None:
            return None
        return screen.update(keyboard, event)

    def __getattr__(self, name):
        screen = self._instance
        if screen is None:
            raise AttributeError(name)
        return getattr(screen, name)
