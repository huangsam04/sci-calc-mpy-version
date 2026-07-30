import builtins
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


def test_internal_main_discards_cached_sd_boot_chain_modules(monkeypatch):
    imported_paths = []
    executed = []

    class Environment:
        def exec_file(self, target):
            raise AssertionError(
                "slot handoff must not retain BootEnvironment")

        def recover(self, error):
            raise AssertionError("unexpected recovery: " + repr(error))

    environment = Environment()
    trusted_bootenv = types.ModuleType("bootenv")
    trusted_bootenv.environment = lambda: environment
    trusted_supervisor = types.ModuleType("bootsupervisor")
    trusted_supervisor.prepare = lambda _environment: (
        object(), "/sd/.slots/A/launch.py")
    untrusted = types.ModuleType("untrusted_sd_module")
    original_import = builtins.__import__

    for name in (
            "bootenv", "bootlog", "bootsel", "bootsupervisor", "recovery"):
        monkeypatch.setitem(sys.modules, name, untrusted)
    monkeypatch.setattr(sys, "path", ["/sd", "/", "/lib"])

    def trusted_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "bootenv":
            assert sys.modules.get(name) is not untrusted
            imported_paths.append(tuple(sys.path))
            return trusted_bootenv
        if name == "bootsupervisor":
            assert sys.modules.get(name) is not untrusted
            imported_paths.append(tuple(sys.path))
            return trusted_supervisor
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", trusted_import)
    def execfile_stub(target):
        assert "bootenv" not in sys.modules
        assert "bootsupervisor" not in sys.modules
        executed.append(target)

    monkeypatch.setattr(builtins, "execfile", execfile_stub, raising=False)
    source = (SOURCE / "internal_main.py").read_text(encoding="utf-8")
    namespace = {"__name__": "__main__"}
    exec(compile(source, "internal_main.py", "exec"), namespace)

    assert imported_paths == [("/lib", "/"), ("/lib", "/")]
    assert executed == ["/sd/.slots/A/launch.py"]
    assert namespace["environment"] is None
    assert namespace["bootenv"] is None
