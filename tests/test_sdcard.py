import pytest

from sdcard import SDCard


class SPIStub:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.writes = []

    def read(self, length, fill):
        return bytes((next(self.responses),))

    def write(self, data):
        self.writes.append(bytes(data))


class PinStub:
    def __init__(self):
        self.values = []

    def __call__(self, value):
        self.values.append(value)


def card_with_responses(responses):
    card = SDCard.__new__(SDCard)
    card.spi = SPIStub(responses)
    card.cs = PinStub()
    return card


def test_rejected_sd_write_raises_visible_io_error():
    card = card_with_responses((0xFF, 0x0B))

    with pytest.raises(OSError, match="rejected"):
        card.write(0xFE, bytearray(512))


def test_busy_sd_write_times_out_and_releases_bus(monkeypatch):
    now = [0]

    def ticks_ms():
        now[0] += 100
        return now[0]

    monkeypatch.setattr("sdcard.time.ticks_ms", ticks_ms)
    monkeypatch.setattr("sdcard.time.ticks_add", lambda value, delta: value + delta)
    monkeypatch.setattr("sdcard.time.ticks_diff", lambda left, right: left - right)
    monkeypatch.setattr("sdcard.time.sleep_ms", lambda value: None)
    card = card_with_responses((0xFF, 0x05) + (0,) * 20)

    with pytest.raises(OSError, match="busy timeout"):
        card.write(0xFE, bytearray(512))

    assert card.cs.values[-1] == 1
