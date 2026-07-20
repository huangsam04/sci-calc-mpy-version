import pathlib
import sys
import types
import time


SOURCE = pathlib.Path(__file__).parents[1] / "source"
sys.path.insert(0, str(SOURCE))

micropython = types.ModuleType("micropython")
micropython.const = lambda value: value
sys.modules.setdefault("micropython", micropython)

if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_diff = lambda left, right: left - right
    time.ticks_add = lambda value, delta: value + delta
    time.sleep_ms = lambda milliseconds: time.sleep(milliseconds / 1000)
    time.sleep_us = lambda microseconds: time.sleep(microseconds / 1_000_000)


class _Pin:
    IN = 0
    OUT = 1

    def __init__(self, number, mode=None):
        self.number = number
        self.mode = mode
        self._value = 0

    def value(self, new_value=None):
        if new_value is not None:
            self._value = new_value
        return self._value

    def init(self, mode=None, value=None):
        self.mode = mode
        if value is not None:
            self._value = value


class _SPI:
    def __init__(self, *args, **kwargs):
        pass

    def write(self, data):
        pass


class _ADC:
    ATTN_11DB = 0

    def __init__(self, pin):
        pass

    def atten(self, value):
        pass

    def read(self):
        return 2048


machine = types.ModuleType("machine")
machine.Pin = _Pin
machine.SPI = _SPI
machine.ADC = _ADC
machine.SDCard = object
sys.modules.setdefault("machine", machine)

utime = types.ModuleType("utime")
utime.sleep_ms = time.sleep_ms
sys.modules.setdefault("utime", utime)


class _FrameBuffer:
    def __init__(self, buffer, width, height, format, stride=None):
        self.buffer = buffer
        self.width = width
        self.height = height
        self.lines = []

    def pixel(self, x, y, color=None):
        return 0

    def line(self, x1, y1, x2, y2, color):
        self.lines.append((x1, y1, x2, y2, color))

    def fill(self, color):
        pass

    def fill_rect(self, x, y, width, height, color):
        pass

    def rect(self, x, y, width, height, color):
        pass

    def hline(self, x, y, width, color):
        pass

    def vline(self, x, y, height, color):
        pass

    def blit(self, source, x, y, key=-1, palette=None):
        pass


framebuf = types.ModuleType("framebuf")
framebuf.FrameBuffer = _FrameBuffer
framebuf.MONO_HMSB = 1
framebuf.MONO_VLSB = 0
framebuf.GS4_HMSB = 2
framebuf.GS8 = 3
sys.modules.setdefault("framebuf", framebuf)
