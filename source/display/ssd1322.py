"""MicroPython SSD1322 OLED monochrom display driver."""
import sys
import micropython  # type: ignore
from micropython import const  # type: ignore
from framebuf import FrameBuffer, GS4_HMSB  # type: ignore
from utime import sleep_ms  # type: ignore
from display.mono_palette import MonoPalette


_HAS_FAST_TEXT_DRAW = sys.implementation.name == "micropython"


def _cached_row_view(cached_views, start, end):
    """Return one of the display's finite preseeded row views.

    A missing range is deliberately not cached at present time.  The caller
    promotes it to a full transfer, preserving correct pixels without a new
    memoryview object in the frame path.
    """
    index = 0
    cache_count = len(cached_views)
    while index < cache_count:
        cached = cached_views[index]
        if cached[0] == start and cached[1] == end:
            return cached[2]
        index += 1
    return None


if _HAS_FAST_TEXT_DRAW:
    @micropython.viper
    def _draw_packed_text(target: ptr8, data: ptr8, text: ptr8,
                           text_length: int, x: int, y: int, stride: int,
                           shade: int, font_height: int, byte_height: int,
                           bytes_per_letter: int, start_letter: int,
                           end_letter: int, spacing: int,
                           screen_width: int, screen_height: int) -> int:
        char_index: int = 0
        while char_index < text_length:
            code: int = int(text[char_index])
            if code < start_letter or code >= end_letter:
                return 0
            glyph_offset: int = (code - start_letter) * bytes_per_letter
            width: int = int(data[glyph_offset])
            column: int = 0
            while column < width:
                chunk: int = 0
                while chunk < byte_height:
                    bits: int = int(
                        data[glyph_offset + 1 + column * byte_height + chunk])
                    row_base: int = chunk * 8
                    row_limit: int = font_height - row_base
                    if row_limit > 8:
                        row_limit = 8
                    row: int = 0
                    while row < row_limit:
                        if bits & (1 << row):
                            pixel_y: int = y + row_base + row
                            pixel_x: int = x + column
                            # Viper pointer writes do not bounds-check.  Keep
                            # all four edge tests immediately before deriving
                            # the buffer offset so clipped text is safe too.
                            if (pixel_x >= 0 and pixel_x < screen_width
                                    and pixel_y >= 0
                                    and pixel_y < screen_height):
                                output_offset: int = (
                                    pixel_y * stride + (pixel_x >> 1))
                                value: int = int(target[output_offset])
                                if pixel_x & 1:
                                    target[output_offset] = (
                                        value & 0xF0) | shade
                                else:
                                    target[output_offset] = (
                                        value & 0x0F) | (shade << 4)
                        row += 1
                    chunk += 1
                column += 1
            x += width + spacing
            char_index += 1
        return 1


class Display(object):
    """Serial interface for monochrome OLED display.

    Note:  All coordinates are zero based.
    """

    # Command constants from display datasheet
    ENABLE_GRAY_SCALE_TABLE = const(0x00)
    SET_COLUMN_ADDRESS = const(0x15)
    WRITE_RAM = const(0x5C)
    READ_RAM = const(0x5D)
    SET_ROW_ADDRESS = const(0x75)
    SET_REMAP_DUAL_COM_LINE_MODE = const(0xA0)  # Re-map & Dual COM Line Mode
    SET_DISPLAY_START_LINE = const(0xA1)
    SET_DISPLAY_OFFSET = const(0xA2)
    SET_DISPLAY_MODE_ALL_OFF = const(0xA4)
    SET_DISPLAY_MODE_ALL_ON = const(0xA5)
    SET_DISPLAY_MODE_NORMAL = const(0xA6)
    SET_DISPLAY_MODE_INVERSE = const(0xA7)
    PARTIAL_DISPLAY_ENABLE = const(0xA8)
    PARTIAL_DISPLAY_DISABLE = const(0xA9)
    SET_FUNCTION_SELECTION = const(0xAB)
    DISPLAY_SLEEP_ON = const(0xAE)
    DISPLAY_SLEEP_OFF = const(0xAF)
    SET_PHASE_LENGTH = const(0xB1)
    SET_FRONT_CLOCK_DIVIDER = const(0xB3)
    DISPLAY_ENHANCEMENT_A = const(0xB4)
    SET_GPIO = const(0xB5)
    SET_SECOND_PRECHARGE_PERIOD = const(0xB6)
    SET_GRAY_SCALE_TABLE = const(0xB8)
    SELECT_DEFAULT_LINEAR_GRAY_SCALE_TABLE = const(0xB9)
    SET_PRECHARGE_VOLTAGE = const(0xBB)
    SET_VCOMH_VOLTAGE = const(0xBE)
    SET_CONTRAST_CURRENT = const(0xC1)
    MASTER_CURRENT_CONTROL = const(0xC7)
    SET_MULTIPLEX_RATIO = const(0xCA)
    DISPLAY_ENHANCEMENT_B = const(0xD1)
    SET_COMMAND_LOCK = const(0xFD)

    # Options for controlling VSL selection
    ENABLE_EXTERNAL_VSL = const(0x00)
    ENABLE_INTERNAL_VSL = const(0x02)

    # Options for grayscale quality
    NORMAL_GRAYSCALE_QUALITY = const(0xB0)
    ENHANCED_LOW_GRAY_SCALE_QUALITY = const(0XF8)

    # Options for display enhancement b
    RESERVED_ENHANCEMENT = const(0x00)
    NORMAL_ENHANCEMENT = const(0x02)

    # Options for command lock
    COMMANDS_LOCK = const(0x16)
    COMMANDS_UNLOCK = const(0x12)

    # Column and row maximums
    # NOTE: Unsure if addresses vary among displays
    COLUMN_ADDRESS = const(0x77)
    ROW_ADDRESS = const(0x7F)

    def __init__(self, spi, cs, dc, rst, width=256, height=64):
        """Constructor for Display.

        Args:
            spi (Class Spi):  SPI interface for display
            cs (Class Pin):  Chip select pin
            dc (Class Pin):  Data/Command pin
            rst (Class Pin):  Reset pin
            width (Optional int): Screen width (default 256)
            height (Optional int): Screen height (default 64)
        """
        self.spi = spi
        self.cs = cs
        self.dc = dc
        self.rst = rst
        self.width = width
        self.height = height
        self.byte_width = -(-width // 2)  # Ceiling division
        self.buffer_length = self.byte_width * height
        # Buffer
        self.gs4_buf = bytearray(self.buffer_length)
        # ``present_rows()`` keeps one main view and preseeded fixed hot-band
        # views.  The normal 64-row display covers Calculator/Plot editor
        # bands, the Stopwatch's 13-row timer band, the shared footer, and all
        # six finite root-menu spans before the first measured transfer.
        self._gs4_view = memoryview(self.gs4_buf)
        if height >= 64:
            top_12_end = self.byte_width * 12
            top_13_end = self.byte_width * 13
            top_14_end = self.byte_width * 14
            top_22_end = self.byte_width * 22
            footer_start = self.byte_width * 54
            footer_end = self.byte_width * 64
            menu_01_start = self.byte_width * 13
            menu_01_end = self.byte_width * 39
            menu_12_start = self.byte_width * 25
            menu_12_end = self.byte_width * 51
            menu_23_start = self.byte_width * 37
            menu_23_end = self.byte_width * 63
            menu_012_end = self.byte_width * 51
            menu_123_start = self.byte_width * 25
            menu_123_end = self.byte_width * 63
            menu_0123_end = self.byte_width * 63
            self._row_views = [
                [0, top_12_end, self._gs4_view[0:top_12_end]],
                [0, top_13_end, self._gs4_view[0:top_13_end]],
                [0, top_14_end, self._gs4_view[0:top_14_end]],
                [0, top_22_end, self._gs4_view[0:top_22_end]],
                [footer_start, footer_end,
                 self._gs4_view[footer_start:footer_end]],
                [menu_01_start, menu_01_end,
                 self._gs4_view[menu_01_start:menu_01_end]],
                [menu_12_start, menu_12_end,
                 self._gs4_view[menu_12_start:menu_12_end]],
                [menu_23_start, menu_23_end,
                 self._gs4_view[menu_23_start:menu_23_end]],
                [menu_01_start, menu_012_end,
                 self._gs4_view[menu_01_start:menu_012_end]],
                [menu_123_start, menu_123_end,
                 self._gs4_view[menu_123_start:menu_123_end]],
                [menu_01_start, menu_0123_end,
                 self._gs4_view[menu_01_start:menu_0123_end]],
            ]
        else:
            # Generic/test displays have no known hot bands.  Their valid
            # compatibility path is a bounded full transfer.
            self._row_views = []
        # Frame Buffer
        self.gs4_fb = FrameBuffer(self.gs4_buf, width, height, GS4_HMSB)
        # Reused by write_cmd() so brightness and present paths do not allocate
        # tiny command buffers before transfers.
        self._command_byte = bytearray(1)
        self._command_arg1 = bytearray(1)
        self._command_arg2 = bytearray(2)
        # Init palette for mono to GS4 blit
        self.palette = MonoPalette()
        self.clear_buffers()
        # Initialize GPIO pins
        self.cs.init(self.cs.OUT, value=1)
        self.dc.init(self.dc.OUT, value=0)
        self.rst.init(self.rst.OUT, value=1)

        self.reset()
        # Send initialization commands
        self.write_cmd(self.SET_COMMAND_LOCK, self.COMMANDS_UNLOCK)
        self.write_cmd(self.DISPLAY_SLEEP_ON)
        # Set clock at 80 frames per second
        self.write_cmd(self.SET_FRONT_CLOCK_DIVIDER, 0x91)
        # Set multiplex ratio to 1/64
        self.write_cmd(self.SET_MULTIPLEX_RATIO, 0x3F)
        self.write_cmd(self.SET_DISPLAY_OFFSET, 0x00)
        self.write_cmd(self.SET_DISPLAY_START_LINE, 0x00)
        # Column address 0 mapped to SEG0
        # Disable nibble remap
        # Scan from COM[N-1] to C0M0
        # Disable COM split between odd and even
        # Enable dual COM line mode
        self.write_cmd(self.SET_REMAP_DUAL_COM_LINE_MODE, 0x06, 0x11)  # sci-calc: nibble remap + no COM remap
        # Disable GPIO pins input
        self.write_cmd(self.SET_GPIO, 0x00)
        # Enable internal VDD regulator
        self.write_cmd(self.SET_FUNCTION_SELECTION, 0x01)
        # Enable external VSL
        self.write_cmd(self.DISPLAY_ENHANCEMENT_A,
                       self.ENABLE_EXTERNAL_VSL | 0xA0,
                       self.ENHANCED_LOW_GRAY_SCALE_QUALITY | 0x05)
        # Set segment output current
        self.write_cmd(self.SET_CONTRAST_CURRENT, 0x9F)
        # Set scale factor of segment output current control
        self.set_brightness(100)
        # Set default linear gray scale table
        self.write_cmd(self.SELECT_DEFAULT_LINEAR_GRAY_SCALE_TABLE)
        # Set phase 1 as 5 clocks and phase 2 as 14 clocks
        self.write_cmd(self.SET_PHASE_LENGTH, 0xE2)
        # Enhance driving scheme capability
        self.write_cmd(self.DISPLAY_ENHANCEMENT_B,
                       self.RESERVED_ENHANCEMENT | 0xA2, 0x20)
        # Set pre-charge voltage level as 0.60 * VCC
        self.write_cmd(self.SET_PRECHARGE_VOLTAGE, 0x1F)
        # Set second pre-charge period as 8 clocks
        self.write_cmd(self.SET_SECOND_PRECHARGE_PERIOD, 0x08)
        # Set common pin deselect voltage as 0.86 * VCC
        self.write_cmd(self.SET_VCOMH_VOLTAGE, 0x07)
        # Normal display mode
        self.write_cmd(self.SET_DISPLAY_MODE_NORMAL)
        self.write_cmd(self.PARTIAL_DISPLAY_DISABLE)
        self.write_cmd(self.DISPLAY_SLEEP_OFF)

        self.clear_buffers()
        self.present()

    def cleanup(self):
        """Clean up resources."""
        self.clear()
        self.sleep()
        self.spi.deinit()

    def clear(self):
        """Clear display."""
        self.clear_buffers()
        self.present()

    def clear_buffers(self, gs=0):
        """Clear buffer.

        Args:
            gs (int): Grayscale 0=Black to 15=White (default grayscale table)
        """
        self.gs4_fb.fill(gs)

    def draw_hline(self, x, y, w, gs=15):
        """Draw a horizontal line.

        Args:
            x (int): Starting X position.
            y (int): Starting Y position.
            w (int): Width of line.
            gs (int): Grayscale 0=Black to 15=White (default grayscale table)
        """
        if self.is_off_grid(x, y, x + w - 1, y):
            return
        self.gs4_fb.hline(x, y, w, gs)

    def draw_pixel(self, x, y, gs=15):
        """Draw a single pixel.

        Args:
            x (int): X position.
            y (int): Y position.
            gs (int): Grayscale 0=Black to 15=White (default grayscale table)
        """
        if self.is_off_grid(x, y, x, y):
            return
        self.gs4_fb.pixel(x, y, gs)

    def draw_rectangle(self, x, y, w, h, gs=15):
        """Draw a rectangle.

        Args:
            x (int): Starting X position.
            y (int): Starting Y position.
            w (int): Width of rectangle.
            h (int): Height of rectangle.
            gs (int): Grayscale 0=Black to 15=White (default grayscale table)
        """
        self.gs4_fb.rect(x, y, w, h, gs)

    def draw_text_direct(self, x, y, text, font, gs=15, spacing=1):
        """Draw an XGLCD string directly from its packed source bytes.

        This path creates neither glyph FrameBuffers nor string-cache entries.
        It clips each packed pixel before writing because the MicroPython Viper
        implementation operates on a raw framebuffer pointer.
        """
        if not text:
            return
        data = font.letters
        target = self.gs4_buf
        stride = self.byte_width
        shade = gs & 0x0F
        height = font.height
        byte_height = (height + 7) // 8
        bytes_per_letter = font.bytes_per_letter
        start_letter = font.start_letter
        end_letter = start_letter + font.letter_count
        screen_width = self.width
        screen_height = self.height
        # Text advances only rightward and glyphs only downward.  Rejecting
        # wholly off-screen runs avoids unnecessary glyph traversal while the
        # inner loops still clip partial left/top/right/bottom glyphs.
        if (x >= screen_width or y >= screen_height
                or y + height <= 0):
            return
        encoded = not isinstance(text, str)
        if encoded and _HAS_FAST_TEXT_DRAW:
            _draw_packed_text(
                target, data, text, len(text), x, y, stride, shade, height,
                byte_height, bytes_per_letter, start_letter, end_letter,
                spacing, screen_width, screen_height)
            return
        char_index = 0
        text_length = len(text)
        while char_index < text_length:
            code = text[char_index] if encoded else ord(text[char_index])
            if code < start_letter or code >= end_letter:
                return
            offset = (code - start_letter) * bytes_per_letter
            width = data[offset]
            column = 0
            while column < width:
                chunk = 0
                while chunk < byte_height:
                    bits = data[
                        offset + 1 + column * byte_height + chunk]
                    row = 0
                    row_base = chunk * 8
                    row_limit = min(8, height - row_base)
                    while row < row_limit:
                        if bits & (1 << row):
                            pixel_y = y + row_base + row
                            pixel_x = x + column
                            if (pixel_x >= 0 and pixel_x < screen_width
                                    and pixel_y >= 0
                                    and pixel_y < screen_height):
                                offset_out = (
                                    pixel_y * stride + (pixel_x >> 1))
                                value = target[offset_out]
                                if pixel_x & 1:
                                    target[offset_out] = (
                                        value & 0xF0) | shade
                                else:
                                    target[offset_out] = (
                                        value & 0x0F) | (shade << 4)
                        row += 1
                    chunk += 1
                column += 1
            x += width + spacing
            char_index += 1

    def draw_text8x8(self, x, y, text, gs=15):
        """Draw text using built-in MicroPython 8x8 bit font.

        Args:
            x (int): Starting X position.
            y (int): Starting Y position.
            text (string): Text to draw.
            gs (int): Grayscale 0=Black to 15=White (default grayscale table)
        """
        # Confirm coordinates in boundary.  An 8x8 glyph ends at x+7, y+7, so
        # the far corner is inclusive (matching fill_rectangle / draw_vline).
        if self.is_off_grid(x, y, x + 8 - 1, y + 8 - 1):
            return
        self.gs4_fb.text(text, x, y, gs)

    def draw_vline(self, x, y, h, gs=15):
        """Draw a vertical line.

        Args:
            x (int): Starting X position.
            y (int): Starting Y position.
            h (int): Height of line.
            gs (int): Grayscale 0=Black to 15=White (default grayscale table)
        """
        # Confirm coordinates in boundary.  A line of height h ends at row
        # y+h-1, so the far corner is y+h-1 (matching fill_rectangle).
        if self.is_off_grid(x, y, x, y + h - 1):
            return
        self.gs4_fb.vline(x, y, h, gs)

    def fill_rectangle(self, x, y, w, h, gs=15):
        """Draw a filled rectangle.

        Args:
            x (int): Starting X position.
            y (int): Starting Y position.
            w (int): Width of rectangle.
            h (int): Height of rectangle.
            gs (int): Grayscale 0=Black to 15=White (default grayscale table)
        """
        if self.is_off_grid(x, y, x + w - 1, y + h - 1):
            return
        self.gs4_fb.fill_rect(x, y, w, h, gs)

    def is_off_grid(self, xmin, ymin, xmax, ymax):
        """Check if coordinates extend past display boundaries.

        Args:
            xmin (int): Minimum horizontal pixel.
            ymin (int): Minimum vertical pixel.
            xmax (int): Maximum horizontal pixel.
            ymax (int): Maximum vertical pixel.
        Returns:
            boolean: False = Coordinates OK, True = Error.
        """
        # Avoid UART logging here; this path runs for every presented frame.
        if xmin < 0 or ymin < 0 or xmax >= self.width or ymax >= self.height:
            return True
        return False

    def present(self):
        """Present image to display.
        """
        x0 = 0
        x1 = self.width // 4 - 1  # 2 bytes per address, 2 pixels per byte
        y0 = 0
        y1 = self.height - 1
        self.set_address(x0, y0, x1, y1)
        self.write_data(self.gs4_buf)

    def present_rows(self, row_ranges):
        """Present complete framebuffer rows with one SPI write per range.

        Full-width rows are contiguous in the GS4 buffer, so this keeps the
        OLED's unchanged rows in controller RAM without a second framebuffer.
        Callers may use this only when they know every omitted row is stable.
        """
        row_bytes = self.byte_width
        last_column = self.width // 4 - 1
        try:
            cached_views = self._row_views
        except AttributeError:
            # Host tests sometimes construct a minimal Display with __new__.
            # Production preseeded all supported partial views at boot.
            cached_views = []
            self._row_views = cached_views
        try:
            range_total = len(row_ranges)
        except TypeError:
            # The resident renderer passes DamageMap's fixed indexable
            # backing.  For a one-off external iterable, preserve visible
            # correctness with a full transfer instead of allocating one.
            self.present()
            return
        range_index = 0
        while range_index < range_total:
            row_range = row_ranges[range_index]
            row_start = max(0, int(row_range[0]))
            row_end = min(
                self.height, row_start + max(0, int(row_range[1])))
            if row_end <= row_start:
                range_index += 1
                continue
            start = row_start * row_bytes
            end = row_end * row_bytes
            row_view = _cached_row_view(cached_views, start, end)
            if row_view is None:
                # Do not make any arbitrary view in a partial frame.  A full
                # transfer is slower but remains correct and bounded.
                self.present()
                return
            self.set_address(0, row_start, last_column, row_end - 1)
            self.write_data(row_view)
            range_index += 1

    def _write_cmd0(self, command):
        self.dc(0)
        self.cs(0)
        self._command_byte[0] = command
        self.spi.write(self._command_byte)
        self.cs(1)

    def _write_cmd1(self, command, value):
        self._write_cmd0(command)
        self._command_arg1[0] = value
        self.write_data(self._command_arg1)

    def _write_cmd2(self, command, first, second):
        self._write_cmd0(command)
        self._command_arg2[0] = first
        self._command_arg2[1] = second
        self.write_data(self._command_arg2)

    def reset(self):
        """Perform reset."""
        self.rst(0)
        sleep_ms(50)
        self.rst(1)
        sleep_ms(100)

    def set_address(self, x0, y0, x1, y1, offset=28):
        """Set column and row addresses.

        Args:
            x0 (byte): Starting X address
            y0 (byte): Starting Y address
            x1 (byte): Ending X address
            y1 (byte): Ending Y address
            offset (byte): Horizontal offset (Default 28)
        Note:
            There is a horizontal offset of 28 (pixels start from segment 112)
        """
        self._write_cmd2(
            self.SET_COLUMN_ADDRESS, x0 + offset, x1 + offset)
        self._write_cmd2(self.SET_ROW_ADDRESS, y0, y1)
        self._write_cmd0(self.WRITE_RAM)

    def sleep(self):
        """Put display to sleep."""
        self.write_cmd(self.DISPLAY_SLEEP_ON)

    def wake(self):
        """Wake display from sleep."""
        self.write_cmd(self.DISPLAY_SLEEP_OFF)

    def set_brightness(self, percent):
        """Set OLED master current from a user-facing percentage.

        Ten percent is kept as the lower bound so a mistaken setting cannot
        make the display appear broken.  The SSD1322 C7 register accepts a
        four-bit scale factor, so percentages are rounded to the nearest step.
        """
        percent = max(10, min(100, int(percent)))
        current = max(1, min(15, (percent * 15 + 50) // 100))
        self._write_cmd1(self.MASTER_CURRENT_CONTROL, current)
        self.brightness = percent

    def write_cmd(self, command, *args):
        """Write command to display.

        Args:
            command (byte): Display command code.
            *args (optional bytes): Data to transmit.
        """
        self.dc(0)
        self.cs(0)
        self._command_byte[0] = command
        self.spi.write(self._command_byte)
        self.cs(1)
        # Handle any passed data
        if len(args) == 1:
            self._command_arg1[0] = args[0]
            self.write_data(self._command_arg1)
        elif len(args) == 2:
            self._command_arg2[0] = args[0]
            self._command_arg2[1] = args[1]
            self.write_data(self._command_arg2)
        elif len(args) > 2:
            self.write_data(bytearray(args))

    def write_data(self, data):
        """Write data to display.

        Args:
            data (bytes): Data to transmit.
        """
        self.dc(1)
        self.cs(0)
        self.spi.write(data)
        self.cs(1)
