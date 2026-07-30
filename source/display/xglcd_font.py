"""Packed X-GLCD font data for allocation-free direct rendering."""
import gc


_COMPACT_MAGIC = b"XGF1"
_COMPACT_HEADER_SIZE = 8


class XglcdFont(object):
    """Load one bounded ASCII font without retaining raster caches."""

    __slots__ = (
        "width", "height", "start_letter", "letter_count",
        "bytes_per_letter", "letters")

    def __init__(self, path, width, height, start_letter=32, letter_count=96,
                 cache_bytes=0):
        # ``cache_bytes`` remains an accepted compatibility argument, but the
        # resident renderer writes packed glyphs straight into its sole GS4
        # framebuffer and therefore owns no optional raster cache.
        self.width = width
        self.height = height
        self.start_letter = start_letter
        self.letter_count = letter_count
        self.bytes_per_letter = (((height - 1) // 8 + 1) * width + 1)
        self._load(path)

    def _load(self, path):
        bytes_per_letter = self.bytes_per_letter
        letters = bytearray(bytes_per_letter * self.letter_count)
        view = memoryview(letters)
        with open(path, "rb") as font_file:
            header = font_file.read(_COMPACT_HEADER_SIZE)
            if header[0:4] == _COMPACT_MAGIC:
                if len(header) != _COMPACT_HEADER_SIZE:
                    raise ValueError("Incomplete compact X-GLCD font header")
                metadata = (header[4], header[5], header[6], header[7])
                expected = (self.width, self.height, self.start_letter,
                            self.letter_count)
                if metadata != expected:
                    raise ValueError(
                        "Compact X-GLCD font metadata does not match requested font")
                data = font_file.read(len(letters))
                if len(data) != len(letters):
                    raise ValueError("Incomplete compact X-GLCD font data")
                view[:] = data
                self.letters = letters
                return

            font_file.seek(0)
            offset = 0
            # Legacy C assets may contain non-UTF-8 glyph names in comments;
            # parse only their ASCII byte tokens.
            for line in font_file:
                if offset >= len(letters):
                    break
                line = line.strip()
                if len(line) == 0 or line[0:2] != b"0x":
                    continue
                comment = line.find(b"//")
                if comment != -1:
                    line = line[0:comment].strip()
                if line.endswith(b","):
                    line = line[:-1]
                view[offset:offset + bytes_per_letter] = bytearray(
                    int(value, 16) for value in line.split(b","))
                offset += bytes_per_letter
        self.letters = letters

    def measure_text(self, text, spacing=1):
        """Measure packed text without creating glyph or substring objects."""
        width = 0
        index = 0
        start = self.start_letter
        end = start + self.letter_count
        bytes_per_letter = self.bytes_per_letter
        letters = self.letters
        while index < len(text):
            code = ord(text[index])
            if start <= code < end:
                width += letters[(code - start) * bytes_per_letter]
            width += spacing
            index += 1
        return width


def trim_caches(primary=None, secondary=None, target_bytes=0):
    """Trim compatible optional font caches; packed fonts simply return 0."""
    released = 0
    if primary is not None:
        trimmer = getattr(primary, "trim_cache", None)
        if trimmer is not None:
            released += trimmer(target_bytes)
    if secondary is not None and secondary is not primary:
        trimmer = getattr(secondary, "trim_cache", None)
        if trimmer is not None:
            released += trimmer(target_bytes)
    return released


def emergency_reclaim(primary=None, secondary=None):
    """Release compatible optional caches before the recovery collection."""
    released = trim_caches(primary, secondary, 0)
    gc.collect()
    return released
