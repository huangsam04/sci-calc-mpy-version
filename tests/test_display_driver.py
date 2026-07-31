import display.ssd1322 as ssd1322_module
from display.ssd1322 import Display


class _Line:
    def __init__(self):
        self.values = []

    def __call__(self, value):
        self.values.append(value)


class _InitLine(_Line):
    OUT = 1

    def __init__(self):
        super().__init__()
        self.initialized = None

    def init(self, mode=None, value=None):
        self.initialized = (mode, value)


class _SPI:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append((data, bytes(data)))


def test_direct_xglcd_text_reads_packed_glyph_without_cache_objects():
    display = Display.__new__(Display)
    display.width = 16
    display.height = 16
    display.byte_width = 8
    display.gs4_buf = bytearray(display.byte_width * 16)
    font = type("Font", (), {
        "letters": bytes((2, 5, 2)),
        "height": 3,
        "bytes_per_letter": 3,
        "start_letter": 65,
        "letter_count": 1,
    })()

    display.draw_text_direct(4, 7, "A", font, gs=9)

    assert display.gs4_buf[7 * 8 + 2] == 0x90
    assert display.gs4_buf[8 * 8 + 2] == 0x09
    assert display.gs4_buf[9 * 8 + 2] == 0x90

    display.gs4_buf = bytearray(display.byte_width * 16)
    display.draw_text_direct(4, 7, b"A", font, gs=9)
    assert display.gs4_buf[7 * 8 + 2] == 0x90
    assert display.gs4_buf[8 * 8 + 2] == 0x09
    assert display.gs4_buf[9 * 8 + 2] == 0x90

    display.gs4_buf = bytearray(display.byte_width * 16)
    display.draw_text_direct(4, 7, bytearray(b"A"), font, gs=9)
    assert display.gs4_buf[7 * 8 + 2] == 0x90
    assert display.gs4_buf[8 * 8 + 2] == 0x09
    assert display.gs4_buf[9 * 8 + 2] == 0x90


def test_direct_xglcd_text_clips_every_edge_without_touching_framebuffer_canary():
    display = Display.__new__(Display)
    display.width = 8
    display.height = 8
    display.byte_width = 4
    guard = 8
    backing = bytearray(b"\xA5" * guard + b"\x00" * 32 + b"\x5A" * guard)
    display.gs4_buf = memoryview(backing)[guard:-guard]
    font = type("Font", (), {
        "letters": bytes((2, 7, 7)),
        "height": 3,
        "bytes_per_letter": 3,
        "start_letter": 65,
        "letter_count": 1,
    })()

    # Each partial glyph has visible pixels, but neither negative coordinates
    # nor the right/bottom tail may address outside the framebuffer.
    display.draw_text_direct(-1, -1, b"A", font, gs=9)
    display.draw_text_direct(7, 2, b"A", font, gs=9)
    display.draw_text_direct(2, 7, b"A", font, gs=9)
    display.draw_text_direct(0, 0, b"", font, gs=9)
    display.draw_text_direct(0, 0, b"\xff", font, gs=9)
    display.draw_text_direct(0, 0, "é", font, gs=9)

    assert backing[:guard] == b"\xA5" * guard
    assert backing[-guard:] == b"\x5A" * guard
    assert any(display.gs4_buf)


def test_direct_xglcd_text_passes_frame_bounds_to_the_viper_adapter(monkeypatch):
    display = Display.__new__(Display)
    display.width = 8
    display.height = 8
    display.byte_width = 4
    display.gs4_buf = bytearray(32)
    font = type("Font", (), {
        "letters": bytes((1, 1)),
        "height": 1,
        "bytes_per_letter": 2,
        "start_letter": 65,
        "letter_count": 1,
    })()
    calls = []

    def viper_adapter(*args):
        calls.append(args[-2:])

    monkeypatch.setattr(ssd1322_module, "_HAS_FAST_TEXT_DRAW", True)
    monkeypatch.setattr(ssd1322_module, "_draw_packed_text", viper_adapter,
                        raising=False)

    display.draw_text_direct(-1, 0, b"A", font, gs=9)

    assert calls == [(display.width, display.height)]


def test_write_cmd_reuses_command_buffers_for_common_payload_sizes():
    display = Display.__new__(Display)
    display.dc = _Line()
    display.cs = _Line()
    display.spi = _SPI()
    display._command_byte = bytearray(1)
    display._command_arg1 = bytearray(1)
    display._command_arg2 = bytearray(2)

    display.write_cmd(0x15, 28, 79)
    display.write_cmd(0xC7, 15)
    display.write_cmd(0xAF)

    writes = display.spi.writes
    assert [value for _, value in writes] == [
        b"\x15", b"\x1cO", b"\xc7", b"\x0f", b"\xaf"]
    assert writes[0][0] is display._command_byte
    assert writes[1][0] is display._command_arg2
    assert writes[2][0] is display._command_byte
    assert writes[3][0] is display._command_arg1
    assert writes[4][0] is display._command_byte


def test_brightness_and_addressing_use_fixed_command_helpers():
    display = Display.__new__(Display)
    display.width = 256
    calls = []
    display._write_cmd0 = lambda command: calls.append((command,))
    display._write_cmd1 = lambda command, value: calls.append(
        (command, value))
    display._write_cmd2 = lambda command, first, second: calls.append(
        (command, first, second))

    display.set_brightness(50)
    display.set_address(0, 1, 63, 62)

    assert calls == [
        (display.MASTER_CURRENT_CONTROL, 8),
        (display.SET_COLUMN_ADDRESS, 28, 91),
        (display.SET_ROW_ADDRESS, 1, 62),
        (display.WRITE_RAM,),
    ]


def test_transition_current_uses_fixed_command_without_changing_user_setting():
    display = Display.__new__(Display)
    display.brightness = 80
    calls = []
    display._write_cmd1 = lambda command, value: calls.append(
        (command, value))

    display.set_transition_current(0)
    display.set_transition_current(15)

    assert calls == [
        (display.MASTER_CURRENT_CONTROL, 0),
        (display.MASTER_CURRENT_CONTROL, 15),
    ]
    assert display.brightness == 80


def test_present_rows_merges_each_contiguous_full_width_range_into_one_write():
    display = Display.__new__(Display)
    display.width = 256
    display.height = 64
    display.byte_width = 128
    display.gs4_buf = bytearray(range(256)) * 32
    data = memoryview(display.gs4_buf)
    display._row_views = [
        [3 * 128, 5 * 128, data[3 * 128:5 * 128]],
        [54 * 128, 55 * 128, data[54 * 128:55 * 128]],
    ]
    events = []
    display.set_address = lambda *args: events.append(("address", args))
    display.write_data = lambda data: events.append(("data", bytes(data)))

    display.present_rows(((3, 2), (54, 1)))

    assert events == [
        ("address", (0, 3, 63, 4)),
        ("data", bytes(display.gs4_buf[3 * 128:5 * 128])),
        ("address", (0, 54, 63, 54)),
        ("data", bytes(display.gs4_buf[54 * 128:55 * 128])),
    ]


def test_display_initialization_preseeds_all_eleven_hot_partial_views(
        monkeypatch):
    # Exercise the real production constructor with inert hardware endpoints.
    # The retained partial bands share the sole framebuffer and cover the
    # Calculator/Plot editor, Stopwatch, footer, and three main-menu moves.
    monkeypatch.setattr(ssd1322_module, "sleep_ms", lambda _milliseconds: None)
    display = Display(_SPI(), _InitLine(), _InitLine(), _InitLine(),
                      width=8, height=64)

    assert isinstance(display._gs4_view, memoryview)
    assert display._gs4_view.obj is display.gs4_buf
    assert [(view[0], view[1]) for view in display._row_views] == [
        (0, display.byte_width * 12),
        (0, display.byte_width * 13),
        (0, display.byte_width * 14),
        (0, display.byte_width * 22),
        (display.byte_width * 54, display.byte_width * 64),
        (display.byte_width * 13, display.byte_width * 39),
        (display.byte_width * 25, display.byte_width * 51),
        (display.byte_width * 37, display.byte_width * 63),
        (display.byte_width * 13, display.byte_width * 51),
        (display.byte_width * 25, display.byte_width * 63),
        (display.byte_width * 13, display.byte_width * 63),
    ]
    assert all(isinstance(view[2], memoryview)
               and view[2].obj is display.gs4_buf
               for view in display._row_views)
    assert len(display._row_views) == 11


def test_present_rows_uses_preseeded_views_for_steady_one_and_two_band_damage():
    display = Display.__new__(Display)
    display.width = 256
    display.height = 64
    display.byte_width = 128
    display.gs4_buf = bytearray(range(256)) * 32
    display._gs4_view = memoryview(display.gs4_buf)
    data = display._gs4_view
    display._row_views = [
        [0, 12 * 128, data[0:12 * 128]],
        [0, 13 * 128, data[0:13 * 128]],
        [0, 14 * 128, data[0:14 * 128]],
        [0, 22 * 128, data[0:22 * 128]],
        [54 * 128, 64 * 128, data[54 * 128:64 * 128]],
    ]
    payloads = []
    display.set_address = lambda *args: None
    display.write_data = payloads.append

    display.present_rows(((0, 13),))
    first = payloads[-1]
    display.present_rows(((0, 13),))

    assert payloads[-1] is first
    assert isinstance(first, memoryview)

    bands = ((0, 14), (54, 10))
    display.present_rows(bands)
    first_two = payloads[-2:]
    display.present_rows(bands)

    assert payloads[-2] is first_two[0]
    assert payloads[-1] is first_two[1]
    assert len(display._row_views) == 5


def test_present_rows_cold_stopwatch_band_uses_the_preseed_without_slicing():
    display = Display.__new__(Display)
    display.width = 256
    display.height = 64
    display.byte_width = 128
    display.gs4_buf = bytearray(128 * 64)
    data = memoryview(display.gs4_buf)
    timer_view = data[0:13 * 128]
    display._row_views = [[0, 13 * 128, timer_view]]
    payloads = []
    display.set_address = lambda *args: None
    display.write_data = payloads.append

    class NoSliceMainView:
        def __getitem__(self, _index):
            raise AssertionError("cold Stopwatch partial frame sliced a view")

    # The steady implementation must find timer_view before it could touch a
    # main view.  This models the first measured 0-13 present after boot.
    display._gs4_view = NoSliceMainView()
    display.present_rows(((0, 13),))

    assert payloads == [timer_view]


def test_present_rows_unknown_bands_fall_back_to_one_full_present():
    display = Display.__new__(Display)
    display.width = 256
    display.height = 64
    display.byte_width = 128
    display.gs4_buf = bytearray(range(256)) * 32
    payloads = []
    display.set_address = lambda *args: None
    display.write_data = payloads.append
    bands = ((0, 1), (8, 1), (16, 1))

    display.present_rows(bands)
    cached_views = display._row_views
    display.present_rows(bands)

    assert display._row_views is cached_views
    assert cached_views == []
    # Each call has one safe full-buffer fallback, not a third retained or
    # transient range view.  The same framebuffer is passed through both.
    assert payloads == [display.gs4_buf, display.gs4_buf]
