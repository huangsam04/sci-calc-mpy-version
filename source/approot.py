# Application resource root resolution.
# Frozen production modules precede the selected A/B slot on sys.path.
import sys


def app_root():
    for path in sys.path:
        if path.startswith("/sd/.slots/"):
            return path
    return sys.path[0]
