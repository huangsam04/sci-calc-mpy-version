"""Native-code probe used by deploy.ps1 to prove a device accepts mpy assets."""
import micropython


@micropython.viper
def _identity(value: int) -> int:
    return value


PROBE_VALUE = _identity(41) + 1
