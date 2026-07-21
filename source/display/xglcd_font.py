"""XGLCD Font Utility."""
import gc
from math import floor
from framebuf import FrameBuffer, MONO_VLSB  # type: ignore


class XglcdFont(object):
    """Font data in X-GLCD format.

    Attributes:
        letters: A bytearray of letters (columns consist of bytes)
        width: Maximum pixel width of font
        height: Pixel height of font
        start_letter: ASCII number of first letter
        height_bytes: How many bytes comprises letter height
    """

    def __init__(self, path, width, height, start_letter=32, letter_count=96):
        self.width = width
        self.height = height
        self.start_letter = start_letter
        self.letter_count = letter_count
        self.bytes_per_letter = (floor(
            (self.height - 1) / 8) + 1) * self.width + 1
        # Cache rendered glyphs and strings. Hard cap prevents unbounded growth
        # from frequently-changing text like clock displays (time strings).
        self._cache = {}
        self._cache_max = 256
        self.__load_xglcd_font(path)

    def __load_xglcd_font(self, path):
        bytes_per_letter = self.bytes_per_letter
        self.letters = bytearray(bytes_per_letter * self.letter_count)
        mv = memoryview(self.letters)
        offset = 0
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if len(line) == 0 or line[0:2] != '0x':
                    continue
                comment = line.find('//')
                if comment != -1:
                    line = line[0:comment].strip()
                if line.endswith(','):
                    line = line[0:len(line) - 1]
                mv[offset: offset + bytes_per_letter] = bytearray(
                    int(b, 16) for b in line.split(','))
                offset += bytes_per_letter

    def get_letter(self, letter, rotate=0):
        """Convert letter byte data to pixels. Results are cached."""
        cache_key = (letter, rotate)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        letter_ord = ord(letter) - self.start_letter
        if letter_ord < 0 or letter_ord >= self.letter_count:
            return b'', 0, 0
        bytes_per_letter = self.bytes_per_letter
        offset = letter_ord * bytes_per_letter
        mv = memoryview(self.letters[offset:offset + bytes_per_letter])

        width = mv[0]
        height = self.height
        byte_height = (height - 1) // 8 + 1
        if byte_height > 6:
            return b'', 0, 0
        array_size = width * byte_height
        ba = bytearray(mv[1:array_size + 1])
        pos = 0
        ba2 = bytearray(array_size)

        for i in range(0, array_size, byte_height):
            ba2[pos] = ba[i]
            if byte_height > 1:
                ba2[pos + width] = ba[i + 1]
            if byte_height > 2:
                ba2[pos + width * 2] = ba[i + 2]
            if byte_height > 3:
                ba2[pos + width * 3] = ba[i + 3]
            if byte_height > 4:
                ba2[pos + width * 4] = ba[i + 4]
            if byte_height > 5:
                ba2[pos + width * 5] = ba[i + 5]
            pos += 1

        fb = FrameBuffer(ba2, width, height, MONO_VLSB)

        if rotate == 0:
            result = (fb, width, height)
        elif rotate == 90:
            byte_width = (width - 1) // 8 + 1
            adj_size = height * byte_width
            fb2 = FrameBuffer(bytearray(adj_size), height, width, MONO_VLSB)
            for y in range(height):
                for x in range(width):
                    fb2.pixel(y, x, fb.pixel(x, (height - 1) - y))
            result = (fb2, height, width)
        elif rotate == 180:
            fb2 = FrameBuffer(bytearray(array_size), width, height, MONO_VLSB)
            for y in range(height):
                for x in range(width):
                    fb2.pixel(x, y, fb.pixel((width - 1) - x, (height - 1) - y))
            result = (fb2, width, height)
        elif rotate == 270:
            byte_width = (width - 1) // 8 + 1
            adj_size = height * byte_width
            fb2 = FrameBuffer(bytearray(adj_size), height, width, MONO_VLSB)
            for y in range(height):
                for x in range(width):
                    fb2.pixel(y, x, fb.pixel((width - 1) - x, y))
            result = (fb2, height, width)
        else:
            result = (fb, width, height)

        if len(self._cache) < self._cache_max:
            self._cache[cache_key] = result
        return result

    def measure_text(self, text, spacing=1):
        """Measure length of text string in pixels."""
        length = 0
        for letter in text:
            letter_ord = ord(letter) - self.start_letter
            if letter_ord < 0 or letter_ord >= self.letter_count:
                continue  # skip glyphs outside the font's range
            offset = letter_ord * self.bytes_per_letter
            length += self.letters[offset] + spacing
        return length

    def get_text_fb(self, text, spacing=1):
        """Return a pre-rendered FrameBuffer for the entire string. Cached.
        Turns N letter blits into 1 string blit — huge perf win for static text."""
        cache_key = text
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        total_w = self.measure_text(text, spacing)
        if total_w == 0:
            result = (FrameBuffer(bytearray(1), 1, self.height, MONO_VLSB), 0, self.height)
            if len(self._cache) < self._cache_max:
                self._cache[cache_key] = result
            return result

        byte_height = (self.height - 1) // 8 + 1
        try:
            buf = bytearray(total_w * byte_height)
            fb = FrameBuffer(buf, total_w, self.height, MONO_VLSB)
        except MemoryError:
            # Emergency: clear cache + GC, then retry once
            self._cache.clear()
            gc.collect()
            try:
                buf = bytearray(total_w * byte_height)
                fb = FrameBuffer(buf, total_w, self.height, MONO_VLSB)
            except MemoryError:
                # Still OOM — return minimal placeholder, caller won't crash
                return (FrameBuffer(bytearray(1), 1, self.height, MONO_VLSB), 0, self.height)

        x = 0
        for letter in text:
            lfb, w, _ = self.get_letter(letter)
            fb.blit(lfb, x, 0)
            x += w + spacing

        result = (fb, total_w, self.height)
        if len(self._cache) < self._cache_max:
            self._cache[cache_key] = result
        return result
