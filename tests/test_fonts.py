"""The font pool: file parsing, licence gating, discovery, and coverage.

`sfnt.py` has no third-party dependency, so its tests run anywhere a font file
can be found. They locate one rather than committing a binary fixture.
"""

from __future__ import annotations

import os
import pathlib
import struct
from pathlib import Path

import pytest

from docfix.fonts import sfnt

# Where a font might live, in preference order. reportlab bundles Bitstream
# Vera, so anywhere reportlab is installed there is a known font to test.
CANDIDATE_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
    "/System/Library/Fonts",
    "C:\\Windows\\Fonts",
]


def _reportlab_font_dir() -> str | None:
    try:
        import reportlab
    except Exception:  # noqa: BLE001
        return None
    path = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    return path if os.path.isdir(path) else None


def _find(name: str) -> str | None:
    """Locate a font file by basename, or None if this machine lacks it."""
    directory = _reportlab_font_dir()
    if directory:
        candidate = os.path.join(directory, name)
        if os.path.exists(candidate):
            return candidate
    for root_dir in CANDIDATE_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for root, _dirs, files in os.walk(root_dir):
            if name in files:
                return os.path.join(root, name)
    return None


def _any_truetype() -> str | None:
    directory = _reportlab_font_dir()
    if directory:
        for entry in sorted(os.listdir(directory)):
            if entry.lower().endswith(".ttf"):
                return os.path.join(directory, entry)
    for root_dir in CANDIDATE_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for root, _dirs, files in os.walk(root_dir):
            for entry in sorted(files):
                if entry.lower().endswith(".ttf"):
                    return os.path.join(root, entry)
    return None


@pytest.fixture
def a_font():
    path = _any_truetype()
    if not path:
        pytest.skip("no TrueType font available on this machine")
    return path


@pytest.fixture
def vera():
    path = _find("Vera.ttf")
    if not path:
        pytest.skip("reportlab's bundled Vera.ttf not available")
    return path


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_reads_family_and_style(a_font):
    info = sfnt.read(a_font)
    assert info.family
    assert info.style in ("regular", "bold", "italic", "bolditalic")
    assert info.outlines in (sfnt.TRUETYPE, sfnt.POSTSCRIPT)


def test_vera_matches_its_known_values(vera):
    """Bitstream Vera is a fixed, known quantity wherever reportlab is installed."""
    info = sfnt.read(vera)
    assert "Vera" in info.family
    assert info.fs_type == sfnt.FSTYPE_PREVIEW_PRINT
    assert info.embeddable, "preview & print still permits embedding"
    assert info.loadable


def test_vera_covers_ascii_but_not_cyrillic(vera):
    """Measured: Vera is Latin-only, which is why it cannot be the fallback."""
    info = sfnt.read(vera)
    assert info.covers("Hello world")
    assert not info.covers("Привет")
    assert not info.covers("你好")


def test_coverage_ignores_whitespace(vera):
    info = sfnt.read(vera)
    assert info.covers("a b\tc\nd")


def test_style_variants_are_distinguished():
    found = {}
    for name, expected in [
        ("Vera.ttf", "regular"),
        ("VeraBd.ttf", "bold"),
        ("VeraIt.ttf", "italic"),
        ("VeraBI.ttf", "bolditalic"),
    ]:
        path = _find(name)
        if path:
            found[expected] = sfnt.read(path, with_coverage=False).style
    if not found:
        pytest.skip("Vera family not available")
    for expected, actual in found.items():
        assert actual == expected


def test_skipping_coverage_avoids_the_cmap_work(a_font):
    info = sfnt.read(a_font, with_coverage=False)
    assert info.codepoints == set()
    assert info.family


def test_postscript_outlines_are_marked_unloadable():
    """reportlab's TTFont cannot load CFF outlines, so the pool must skip them."""
    path = None
    for root_dir in CANDIDATE_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for root, _dirs, files in os.walk(root_dir):
            for entry in files:
                if entry.lower().endswith(".otf"):
                    candidate = os.path.join(root, entry)
                    try:
                        if sfnt.read(candidate, with_coverage=False).outlines == sfnt.POSTSCRIPT:
                            path = candidate
                            break
                    except sfnt.FontFileError:
                        continue
            if path:
                break
        if path:
            break
    if not path:
        pytest.skip("no PostScript-outline font available")
    assert not sfnt.read(path, with_coverage=False).loadable


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "too short"),
        (b"not a font at all!!!", "unrecognised sfnt signature"),
    ],
)
def test_non_font_files_are_rejected(tmp_path, content, message):
    path = tmp_path / "bogus.ttf"
    path.write_bytes(content)
    with pytest.raises(sfnt.FontFileError, match=message):
        sfnt.read(str(path))


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(sfnt.FontFileError, match="could not read"):
        sfnt.read(str(tmp_path / "nope.ttf"))


# --------------------------------------------------------------------------
# Embedding permission
# --------------------------------------------------------------------------


def test_restricted_fonts_are_not_embeddable():
    """fsType bit 1 means the vendor forbids embedding outright."""
    info = sfnt.FontInfo("x.ttf", "X", "Regular", sfnt.TRUETYPE, sfnt.FSTYPE_RESTRICTED)
    assert not info.embeddable


@pytest.mark.parametrize(
    "value",
    [
        sfnt.FSTYPE_INSTALLABLE,
        sfnt.FSTYPE_PREVIEW_PRINT,
        sfnt.FSTYPE_EDITABLE,
        sfnt.FSTYPE_EDITABLE | sfnt.FSTYPE_NO_SUBSET,
        None,
    ],
)
def test_permitted_fs_types_are_embeddable(value):
    assert sfnt.FontInfo("x.ttf", "X", "Regular", sfnt.TRUETYPE, value).embeddable


def test_restriction_wins_over_other_bits():
    combined = sfnt.FSTYPE_RESTRICTED | sfnt.FSTYPE_PREVIEW_PRINT
    assert not sfnt.FontInfo("x.ttf", "X", "R", sfnt.TRUETYPE, combined).embeddable
    assert "not permitted" in sfnt.describe_fs_type(combined)


@pytest.mark.parametrize(
    "value, needle",
    [
        (None, "unspecified"),
        (sfnt.FSTYPE_INSTALLABLE, "installable"),
        (sfnt.FSTYPE_RESTRICTED, "not permitted"),
        (sfnt.FSTYPE_PREVIEW_PRINT, "preview"),
        (sfnt.FSTYPE_EDITABLE, "editable"),
        (sfnt.FSTYPE_NO_SUBSET, "no subsetting"),
    ],
)
def test_fs_type_descriptions(value, needle):
    assert needle in sfnt.describe_fs_type(value).lower()


def test_fs_type_is_read_from_the_real_table(vera, tmp_path):
    """Rewrite Vera's fsType to Restricted and confirm the parser sees it.

    Proves the gate reads the actual file rather than trusting a name list.
    """
    data = bytearray(Path(vera).read_bytes())
    (count,) = struct.unpack(">H", data[4:6])
    for index in range(count):
        start = 12 + index * 16
        tag, _c, offset, _length = struct.unpack(">4sIII", data[start : start + 16])
        if tag == b"OS/2":
            data[offset + 8 : offset + 10] = struct.pack(">H", sfnt.FSTYPE_RESTRICTED)
            break
    else:
        pytest.skip("Vera has no OS/2 table")

    path = tmp_path / "restricted.ttf"
    path.write_bytes(bytes(data))

    info = sfnt.read(str(path), with_coverage=False)
    assert info.fs_type == sfnt.FSTYPE_RESTRICTED
    assert not info.embeddable


def test_colour_bitmap_fonts_are_marked_unloadable():
    """A colour emoji font has a TrueType signature but no outline tables.

    reportlab fails on it with "missing location table", so the pool must not
    offer it. Detecting this needs `glyf`+`loca`, not just the signature.
    """
    path = _find("NotoColorEmoji.ttf")
    if not path:
        pytest.skip("NotoColorEmoji.ttf not available")
    info = sfnt.read(path, with_coverage=False)
    assert info.outlines == sfnt.BITMAP
    assert not info.loadable


def test_fonts_declare_their_own_licence(a_font):
    """Licence comes from the file (name IDs 13/14), never from a guess."""
    info = sfnt.read(a_font, with_coverage=False)
    assert isinstance(info.license_text, str)
    assert isinstance(info.license_url, str)


def test_liberation_declares_the_open_font_licence():
    path = _find("LiberationSans-Regular.ttf")
    if not path:
        pytest.skip("Liberation Sans not available")
    info = sfnt.read(path, with_coverage=False)
    assert "OFL" in info.license_url or "Open Font License" in info.license_text


# --------------------------------------------------------------------------
# The pool and the licence gate
# --------------------------------------------------------------------------


def test_pool_builds_without_error():
    from docfix.fonts import pool

    available = pool()
    assert isinstance(available.families, dict)


def test_only_open_licensed_fonts_are_auto_selectable():
    """The core promise: docfix never picks a font it cannot verify is open."""
    from docfix.fonts import pool

    for family in pool().usable_families():
        if family.auto_selectable:
            assert family.license.open, f"{family.name} auto-selected without an open licence"


def test_undeclared_licence_is_not_treated_as_open():
    from docfix.fonts.registry import UNKNOWN_LICENSE

    assert not UNKNOWN_LICENSE.open


def test_plain_gpl_is_not_auto_selectable():
    """Without the font embedding exception it is unclear whether embedding
    places the document under the GPL, so plain GPL is not auto-selected."""
    from docfix.fonts import load_catalog

    by_id = {entry.id: entry for entry in load_catalog().licenses}
    assert by_id["GPL"].open is False
    assert by_id["GPL-font-exception"].open is True


def test_licence_is_read_from_the_font_not_its_name():
    """A font claiming a familiar family name but declaring nothing stays unknown."""
    from docfix.fonts import load_catalog
    from docfix.fonts.registry import UNKNOWN_LICENSE

    catalog = load_catalog()
    impostor = sfnt.FontInfo("x.ttf", "Liberation Sans", "Regular", sfnt.TRUETYPE, 0)
    assert catalog.classify(impostor) is UNKNOWN_LICENSE


@pytest.mark.parametrize(
    "url, text, expected",
    [
        ("http://scripts.sil.org/OFL", "", "OFL-1.1"),
        ("", "Licensed under the SIL Open Font License, Version 1.1", "OFL-1.1"),
        ("http://dejavu.sourceforge.net/wiki/index.php/License", "", "Bitstream-Vera"),
        ("", "GPL2 with font embedding exception", "GPL-font-exception"),
        ("", "This computer font is part of GNU FreeFont.", "GPL-font-exception"),
        ("", "Licensed under the GNU General Public License", "GPL"),
        ("", "totally made up terms", "unknown"),
    ],
)
def test_licence_signatures_match_real_declarations(url, text, expected):
    from docfix.fonts import load_catalog

    info = sfnt.FontInfo("x.ttf", "X", "Regular", sfnt.TRUETYPE, 0,
                         license_text=text, license_url=url)
    assert load_catalog().classify(info).id == expected


def test_register_refuses_a_font_that_forbids_embedding():
    from docfix.fonts.registry import Family, FontError, License, register

    blocked = sfnt.FontInfo("x.ttf", "Blocked", "Regular", sfnt.TRUETYPE,
                            sfnt.FSTYPE_RESTRICTED)
    family = Family(
        name="Blocked",
        category="sans",
        license=License("OFL-1.1", "SIL OFL", True),
        faces={"regular": blocked},
    )
    assert not family.embeddable
    assert not family.auto_selectable, "an open licence cannot override fsType"
    with pytest.raises(FontError, match="Blocked"):
        register(family)


def test_unloadable_fonts_are_kept_out_of_the_pool():
    from docfix.fonts import pool

    for family in pool().usable_families():
        assert family.loadable


# --------------------------------------------------------------------------
# Span resolution
# --------------------------------------------------------------------------


def _option(name, codepoints):
    from docfix.fonts import FontOption

    return FontOption(name=name, family=name, covers=lambda c: c in codepoints)


def test_spans_use_the_first_font_that_covers_each_character():
    from docfix.fonts import resolve_spans

    latin = _option("Latin", set(range(0x00, 0x180)))
    greek = _option("Greek", set(range(0x370, 0x400)))
    spans, missing = resolve_spans("abcΓΔΕabc", [latin, greek])
    assert [(t, o.name) for t, o in spans] == [
        ("abc", "Latin"), ("ΓΔΕ", "Greek"), ("abc", "Latin"),
    ]
    assert not missing


def test_whitespace_does_not_fragment_spans():
    from docfix.fonts import resolve_spans

    latin = _option("Latin", set(range(0x00, 0x180)))
    spans, _ = resolve_spans("a b c", [latin])
    assert len(spans) == 1


def test_characters_nothing_covers_are_reported_and_kept():
    from docfix.fonts import resolve_spans

    latin = _option("Latin", set(range(0x00, 0x180)))
    spans, missing = resolve_spans("ab中cd", [latin])
    assert missing == {"中"}
    assert "".join(text for text, _ in spans) == "ab中cd", "text must not be dropped"


def test_empty_text_and_empty_chain_are_handled():
    from docfix.fonts import resolve_spans

    assert resolve_spans("", []) == ([], set())
    spans, missing = resolve_spans("abc", [])
    assert "".join(t for t, _ in spans) == "abc"
    assert not missing


def test_cid_ranges_are_split_by_script():
    """No single CID collection covers all of CJK, so the ranges are separate.

    Measured: HeiseiKakuGo-W5 renders Han and kana but not Hangul;
    HYSMyeongJo-Medium renders Hangul.
    """
    from docfix.fonts.coverage import in_ranges, ranges_for

    han = ranges_for(["han", "kana"])
    hangul = ranges_for(["hangul"])
    assert in_ranges(ord("日"), han) and in_ranges(ord("你"), han)
    assert not in_ranges(ord("한"), han)
    assert in_ranges(ord("한"), hangul)
    assert not in_ranges(ord("日"), hangul)


def test_base14_fallback_caps_coverage_at_latin1():
    """So that anything beyond gets reported rather than mis-rendered."""
    from docfix.fonts.coverage import base14_option

    option = base14_option("sans")
    assert option.can_render("A")
    assert not option.can_render("Ж")
    assert option.use_tags, "base-14 relies on reportlab's own <b>/<i> mapping"


# --------------------------------------------------------------------------
# Findings from the phase-9 checkpoint review
# --------------------------------------------------------------------------


def test_a_family_with_any_system_face_is_not_reported_pinned(tmp_path, monkeypatch):
    """`pinned` was set from the first face seen, so a family whose remaining
    faces came from the machine was still reported as fully pinned. pinned.yaml
    pins a single face of Noto Sans, so this was guaranteed on any machine that
    also has it installed: half the text would render in machine fonts while
    `docfix fonts --reproducible` reported success."""
    from docfix.fonts import registry
    from docfix.fonts.sfnt import TRUETYPE, FontInfo

    cache = tmp_path / "cache"
    system = tmp_path / "system"
    cache.mkdir()
    system.mkdir()

    faces = [
        FontInfo(str(cache / "F-Regular.ttf"), "Faux", "Regular", TRUETYPE, 0),
        FontInfo(str(system / "F-Bold.ttf"), "Faux", "Bold", TRUETYPE, 0),
    ]
    for face in faces:
        pathlib.Path(face.path).write_bytes(b"")

    monkeypatch.setattr(registry.discover, "scan", lambda *a, **k: faces)
    monkeypatch.setenv("DOCFIX_FONT_CACHE", str(cache))
    registry.reset_pool()

    pool = registry.build_pool()
    family = pool.families["faux"]
    assert set(family.faces) == {"regular", "bold"}
    assert family.pinned is False, "a family with a system face is not reproducible"


def test_a_sibling_directory_is_not_mistaken_for_the_cache(tmp_path, monkeypatch):
    """A bare startswith also matched `.../fonts-extra`, whose contents are
    unverified system fonts -- marking those pinned is the one claim the cache
    exists to make truthfully."""
    from docfix.fonts import registry
    from docfix.fonts.sfnt import TRUETYPE, FontInfo

    cache = tmp_path / "fonts"
    sibling = tmp_path / "fonts-extra"
    cache.mkdir()
    sibling.mkdir()
    face = FontInfo(str(sibling / "X-Regular.ttf"), "Sneaky", "Regular", TRUETYPE, 0)
    pathlib.Path(face.path).write_bytes(b"")

    monkeypatch.setattr(registry.discover, "scan", lambda *a, **k: [face])
    monkeypatch.setenv("DOCFIX_FONT_CACHE", str(cache))
    registry.reset_pool()

    assert registry.build_pool().families["sneaky"].pinned is False
