"""Self-contained SCI-CALC product firmware.

Acceptance fixtures and scenario transactions are not frozen or shipped in an
ordinary release; the existing acceptance script uploads them only while a
device check is running.
"""

freeze("$(PORT_DIR)/modules")
include("$(MPY_DIR)/extmod/asyncio")

_SOURCE = "../source"
_FIRMWARE = "."

freeze(_FIRMWARE, "main.py")

freeze(_SOURCE, (
    "application.py",
    "boot.py",
    "calc/__init__.py",
    "calc/bundled_plugins.py",
    "calc/functions.py",
    "calc/limits.py",
    "calc/loader.py",
    "calc/number.py",
    "calc/parser.py",
    "calc/plugin_reload.py",
    "functions/__init__.py",
    "functions/basic.py",
    "functions/solve.py",
    "functions/trig.py",
    "performance.py",
    "recovery.py",
    "runtime_handle.py",
    "sdcard.py",
    "screens/__init__.py",
    "screens/about.py",
    "screens/calculator.py",
    "screens/function_panel.py",
    "screens/function_picker.py",
    "screens/letter_panel.py",
    "screens/main_menu.py",
    "screens/plot.py",
    "screens/settings.py",
    "screens/stopwatch.py",
    "screens/variable_panel.py",
    "version.py",
))

freeze(_SOURCE, "display")
freeze(_SOURCE, "input")
freeze(_SOURCE, "ui")
freeze(_SOURCE, "utils")
