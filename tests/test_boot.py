import pathlib
import runpy
import sys
import types


SOURCE = pathlib.Path(__file__).parents[1] / "source"


def test_boot_mounts_sd_on_same_spi2_bus_as_display(monkeypatch):
    calls = {}

    class PinStub:
        def __init__(self, number):
            self.number = number

    def spi_stub(bus, **kwargs):
        calls["spi"] = (bus, kwargs)
        return "SPI2"

    class SDCardStub:
        def __init__(self, spi, cs, baudrate=1320000):
            calls["sd"] = (spi, cs.number, baudrate)

    machine = types.ModuleType("machine")
    machine.Pin = PinStub
    machine.SPI = spi_stub
    sdcard = types.ModuleType("sdcard")
    sdcard.SDCard = SDCardStub
    vfs = types.ModuleType("vfs")
    vfs.mount = lambda block_device, path: calls.update(mount=(block_device, path))
    monkeypatch.setitem(sys.modules, "machine", machine)
    monkeypatch.setitem(sys.modules, "sdcard", sdcard)
    monkeypatch.setitem(sys.modules, "vfs", vfs)
    monkeypatch.setattr(sys, "path", list(sys.path))

    runpy.run_path(str(SOURCE / "boot.py"))

    assert calls["spi"][0] == 2
    assert calls["spi"][1]["sck"].number == 18
    assert calls["spi"][1]["mosi"].number == 23
    assert calls["spi"][1]["miso"].number == 19
    assert calls["sd"] == ("SPI2", 4, 10_000_000)
    assert calls["mount"][1] == "/sd"
