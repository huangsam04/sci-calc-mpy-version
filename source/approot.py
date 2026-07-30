# Application resource root resolution.
# The boot supervisor puts the selected A/B slot first on sys.path.
import sys


def app_root():
    return sys.path[0]
