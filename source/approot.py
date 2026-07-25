# Application resource root resolution.
# The boot supervisor puts the active slot root first on sys.path, so
# slot-managed resources (fonts, built-in function packs) resolve relative
# to it. Flat /sd deployments keep working unchanged.
import sys

_FALLBACK_ROOT = "/sd"
_RESERVED_PREFIXES = (".frozen", "/lib")


def app_root():
    if sys.path:
        candidate = sys.path[0]
        if candidate and candidate not in _RESERVED_PREFIXES:
            return candidate
    return _FALLBACK_ROOT
