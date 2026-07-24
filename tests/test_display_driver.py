from display.ssd1322 import Display


class _Line:
    def __init__(self):
        self.values = []

    def __call__(self, value):
        self.values.append(value)


class _SPI:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append((data, bytes(data)))


def test_direct_xglcd_text_reads_packed_glyph_without_cache_objects():
    display = Display.__new__(Display)
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


def test_present_region_programs_only_the_requested_controller_window():
    display = Display.__new__(Display)
    display.height = 64
    events = []
    display._write_cmd2 = lambda *args: events.append(("cmd2", args))
    display._write_cmd0 = lambda command: events.append(("cmd0", command))
    display.write_data = lambda data: events.append(("data", bytes(data)))
    payload = memoryview(bytearray(range(16)))

    display.present_region(40, 8, payload)

    assert events == [
        ("cmd2", (display.SET_COLUMN_ADDRESS, 68, 75)),
        ("cmd2", (display.SET_ROW_ADDRESS, 0, 63)),
        ("cmd0", display.WRITE_RAM),
        ("data", bytes(range(16))),
    ]
