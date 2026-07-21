"""Read-only serial diagnostic runner for device logic and navigation."""

from calc.functions import (DEFAULT_ENABLED_GROUPS, FUNCTION_GROUPS,
                            EvalContext, build_registry)
from calc.loader import load_function_files
from calc.parser import evaluate
from input.keyboard import get_key_label
from screens.function_panel import FunctionPanel
from screens.main_menu import MainMenu
from utils.storage import load_settings


class _Target:
    def __init__(self, name):
        self.name = name


class _Keyboard:
    def is_pressed(self, row, col):
        return False

    def get_hold_time(self, row, col):
        return 0

    def consume_long_press(self, row, col, threshold):
        return False


class DiagnosticSession:
    """Execute deterministic commands and emit one trace line per command."""

    def __init__(self, emit=print):
        self.emit = emit
        settings = load_settings()
        enabled = settings.get("enabled_functions", DEFAULT_ENABLED_GROUPS)
        groups = [name for name in enabled if name in FUNCTION_GROUPS]
        addons = [name[7:] for name in enabled if name.startswith("plugin:")]
        self.registry = build_registry(groups)
        report = load_function_files(self.registry, addons) if addons else None
        self.plugin_errors = report.errors if report else []
        self.context = EvalContext({}, self.registry)
        self.keyboard = _Keyboard()

        self.menu = MainMenu(None)
        for name in ("Calculator", "Plot", "Function Panel", "Stopwatch", "About"):
            self.menu.add_screen(name, _Target(name))
        self.menu.activate()
        self.current = self.menu
        self.last_result = None
        self.panel_items = []
        self.panel_labels = []

    def execute(self, command):
        parts = command.strip().split(" ", 3)
        action = parts[0].upper() if parts and parts[0] else ""
        if action == "KEY" and len(parts) >= 4:
            row = int(parts[1])
            col = int(parts[2])
            shift = parts[3] not in ("0", "false", "False")
            event = (row, col, shift)
            label = get_key_label(row, col, shift)
            before = self.page_name()
            result = None
            if self.current is self.menu:
                result = self.menu.update(self.keyboard, event)
                if result is not None:
                    self.current = result
            elif label == "ESC":
                self.current = self.menu
            self.emit("TRACE cmd=KEY key=" + label
                      + " from=" + before
                      + " page=" + self.page_name()
                      + " selection=" + str(self.menu.menu.cursor_pos)
                      + " result=" + (getattr(result, "name", "-") if result else "-"))
            return result

        if action == "BACK":
            self.current = self.menu
            self.menu.activate()
            self.emit("TRACE cmd=BACK page=" + self.page_name()
                      + " selection=" + str(self.menu.menu.cursor_pos))
            return self.current

        if action == "EVAL" and len(parts) >= 2:
            expression = command.strip()[5:]
            self.last_result = evaluate(expression, self.context)
            self.emit("TRACE cmd=EVAL expr=" + expression
                      + " result=" + str(self.last_result))
            return self.last_result

        if action == "PANEL":
            panel = FunctionPanel(None)
            panel.activate()
            self.panel_items = list(panel._items)
            self.panel_labels = [item[0] for item in panel.menu.items]
            for index, item in enumerate(panel._items):
                label = self.panel_labels[index]
                self.emit("TRACE cmd=PANEL index=" + str(index)
                          + " id=" + item[0] + " label=" + label)
            return self.panel_items

        if action == "STATUS":
            self.emit("TRACE cmd=STATUS page=" + self.page_name()
                      + " selection=" + str(self.menu.menu.cursor_pos)
                      + " functions=" + str(len(self.registry))
                      + " plugin_errors=" + str(len(self.plugin_errors)))
            return self.page_name()

        raise ValueError("Unknown diagnostic command: " + command)

    def page_name(self):
        return "MainMenu" if self.current is self.menu else self.current.name


DEFAULT_COMMANDS = (
    "STATUS",
    "PANEL",
    "KEY 3 1 0",
    "KEY 3 3 0",
    "BACK",
    "EVAL 2+3*4",
    "EVAL -2^2",
)


def run(commands=None):
    """Run commands on real device files and print a machine-readable verdict."""
    session = DiagnosticSession()
    failures = 0
    for command in commands or DEFAULT_COMMANDS:
        try:
            session.execute(command)
        except Exception as error:
            failures += 1
            print("TRACE cmd=ERROR input=" + command + " error=" + str(error))

    labels = [label for label, _ in session.menu.menu.items]
    if not any("Calculator" in label for label in labels):
        failures += 1
        print("CHECK FAIL main menu labels")
    if session.panel_items:
        ids = [item[0] for item in session.panel_items]
        if len(ids) != len(set(ids)):
            failures += 1
            print("CHECK FAIL duplicate panel ids")
        for index, item in enumerate(session.panel_items):
            label = session.panel_labels[index]
            if item[2] and item[0] == "basic" and "Arithmetic" not in label:
                failures += 1
                print("CHECK FAIL ambiguous built-in basic label")
            if not item[2] and "Add-on:" not in label:
                failures += 1
                print("CHECK FAIL ambiguous add-on label " + item[0])
    print("SELFTEST " + ("PASS" if failures == 0 else "FAIL")
          + " failures=" + str(failures))
    return failures
