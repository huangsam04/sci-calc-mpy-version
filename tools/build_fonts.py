"""Generate compact binary assets from the bundled X-GLCD C fonts."""
import argparse
from pathlib import Path


MAGIC = b"XGF1"
FONT_SPECS = (
    ("Bally7x9.c", 7, 9),
    ("Neato5x7.c", 5, 7),
    ("FixedFont5x8.c", 5, 8),
)


def bytes_per_letter(width, height):
    return ((height - 1) // 8 + 1) * width + 1


def _read_legacy_glyphs(source_path, width, height, letter_count):
    size = bytes_per_letter(width, height)
    glyphs = bytearray()
    expected = size * letter_count
    with open(source_path, "rb") as source:
        for raw_line in source:
            if len(glyphs) >= expected:
                break
            line = raw_line.strip()
            if not line.startswith(b"0x"):
                continue
            comment = line.find(b"//")
            if comment != -1:
                line = line[:comment].strip()
            if line.endswith(b","):
                line = line[:-1]
            values = bytearray(int(token, 16) for token in line.split(b","))
            if len(values) != size:
                raise ValueError(
                    "Unexpected glyph size in {}: expected {}, got {}".format(
                        source_path, size, len(values)))
            glyphs.extend(values)

    if len(glyphs) != expected:
        raise ValueError(
            "Unexpected glyph count in {}: expected {}, got {}".format(
                source_path, expected, len(glyphs)))
    return glyphs


def build_font(source_path, output_path, width, height,
               start_letter=32, letter_count=96):
    """Write an XGF1 asset and return its output path."""
    source_path = Path(source_path)
    output_path = Path(output_path)
    glyphs = _read_legacy_glyphs(source_path, width, height, letter_count)
    header = MAGIC + bytes((width, height, start_letter, letter_count))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(header + glyphs)
    return output_path


def build_all(source_dir, output_dir):
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)
    outputs = []
    for filename, width, height in FONT_SPECS:
        outputs.append(build_font(
            source_dir / filename,
            output_dir / (Path(filename).stem + ".xglcd"),
            width,
            height))
    return outputs


def main():
    parser = argparse.ArgumentParser(
        description="Generate compact binary X-GLCD font assets.")
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    for output in build_all(args.source_dir, args.output_dir):
        print(output)


if __name__ == "__main__":
    main()
