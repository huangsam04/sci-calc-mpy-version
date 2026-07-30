import pytest

from sdcard import SDCard


class SPIStub:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.writes = []
        self.readinto_calls = []
        self.readinto_buffers = []

    def read(self, length, fill):
        raise AssertionError("1 B SD transfers must reuse tokenbuf via readinto")

    def readinto(self, buf, fill):
        self.readinto_calls.append((len(buf), fill))
        self.readinto_buffers.append(buf)
        buf[0] = next(self.responses)

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
    card.tokenbuf = bytearray(1)
    return card


def test_rejected_sd_write_raises_visible_io_error():
    card = card_with_responses((0xFF, 0x0B))

    with pytest.raises(OSError, match="rejected"):
        card.write(0xFE, bytearray(512))

    assert card.spi.readinto_calls == [(1, 0xFE), (1, 0xFF)]
    assert all(buf is card.tokenbuf for buf in card.spi.readinto_buffers)
    assert card.cs.values == [0, 1]


def test_accepted_sd_write_reuses_tokenbuf_through_busy_release():
    card = card_with_responses((0xFF, 0x05, 0xFF))

    card.write(0xFE, bytearray(512))

    assert card.spi.readinto_calls == [(1, 0xFE), (1, 0xFF), (1, 0xFF)]
    assert all(buf is card.tokenbuf for buf in card.spi.readinto_buffers)
    assert card.cs.values == [0, 1]


def test_busy_sd_write_times_out_and_releases_bus(monkeypatch):
    now = [0]
    sleeps = []

    def ticks_ms():
        now[0] += 100
        return now[0]

    monkeypatch.setattr("sdcard.time.ticks_ms", ticks_ms)
    monkeypatch.setattr("sdcard.time.ticks_add", lambda value, delta: value + delta)
    monkeypatch.setattr("sdcard.time.ticks_diff", lambda left, right: left - right)
    monkeypatch.setattr("sdcard.time.sleep_ms", sleeps.append)
    card = card_with_responses((0xFF, 0x05) + (0,) * 20)

    with pytest.raises(OSError, match="busy timeout"):
        card.write(0xFE, bytearray(512))

    assert card.spi.readinto_calls[:2] == [(1, 0xFE), (1, 0xFF)]
    assert all(fill == 0xFF for _length, fill in card.spi.readinto_calls[1:])
    assert all(buf is card.tokenbuf for buf in card.spi.readinto_buffers)
    assert sleeps == [1, 1, 1, 1]
    assert card.cs.values == [0, 1]


def test_stop_token_write_reuses_the_same_single_byte_buffer():
    card = card_with_responses((0xFF, 0xFF))

    card.write_token(0xFD)

    assert card.spi.readinto_calls == [(1, 0xFD), (1, 0xFF)]
    assert all(buf is card.tokenbuf for buf in card.spi.readinto_buffers)
    assert card.cs.values == [0, 1]
