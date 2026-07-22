import importlib.util
from pathlib import Path

from display.xglcd_font import XglcdFont


PROJECT = Path(__file__).parents[1]
SOURCE = PROJECT / "source"
TOOL = PROJECT / "tools" / "build_fonts.py"


def _font_builder():
    spec = importlib.util.spec_from_file_location("build_fonts", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_host_generated_font_asset_matches_legacy_xglcd_data(tmp_path):
    builder = _font_builder()
    source_path = SOURCE / "fonts" / "Bally7x9.c"
    output_path = tmp_path / "Bally7x9.xglcd"

    builder.build_font(source_path, output_path, 7, 9)

    legacy = XglcdFont(str(source_path), 7, 9)
    compact = XglcdFont(str(output_path), 7, 9)
    assert output_path.read_bytes().startswith(b"XGF1")
    assert compact.letters == legacy.letters
    _, width, height = compact.get_letter("A")
    assert (width, height) == (6, 9)


def test_compact_font_rejects_metadata_for_the_wrong_font_shape(tmp_path):
    builder = _font_builder()
    output_path = tmp_path / "Bally7x9.xglcd"
    builder.build_font(SOURCE / "fonts" / "Bally7x9.c", output_path, 7, 9)

    try:
        XglcdFont(str(output_path), 5, 7)
    except ValueError as error:
        assert "metadata" in str(error)
    else:
        raise AssertionError("wrong font metadata was accepted")


def test_host_check_generates_all_compact_font_assets():
    script = (PROJECT / "check.ps1").read_text(encoding="utf-8")

    assert "tools\\build_fonts.py" in script
    assert "--source-dir" in script
    assert "--output-dir" in script


def test_font_builder_limits_legacy_sources_to_the_device_glyph_range(tmp_path):
    builder = _font_builder()
    output_path = tmp_path / "Neato5x7.xglcd"

    builder.build_font(SOURCE / "fonts" / "Neato5x7.c", output_path, 5, 7)

    compact = XglcdFont(str(output_path), 5, 7)
    assert len(compact.letters) == compact.bytes_per_letter * 96
