"""`sfnt.py` against malformed fonts.

SECURITY.md claims this parser "fails closed" -- that a table which does not
parse makes a font unusable rather than trusted. That is a claim about
attacker-influenced bytes, so it needs testing with actual malformed input
rather than being asserted.

Every font here is built byte by byte. A corrupt font committed as a fixture
would be opaque to review, and the interesting part is precisely *which* byte
is wrong.
"""

from __future__ import annotations

import struct

import pytest

from docfix.fonts import sfnt

# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def build_font(tables: dict[bytes, bytes], signature: bytes = b"\x00\x01\x00\x00") -> bytes:
    """A minimal but structurally valid sfnt wrapper around given tables."""
    count = len(tables)
    directory_size = 12 + 16 * count
    body = b""
    entries = []
    for tag, payload in tables.items():
        entries.append((tag, directory_size + len(body), len(payload)))
        body += payload

    head = struct.pack(">4sHHHH", signature, count, 0, 0, 0)
    directory = b"".join(
        struct.pack(">4sIII", tag, offset, 0, length) if False
        # tag, checksum, offset, length -- checksum is not verified by the reader
        else struct.pack(">4sIII", tag, 0, offset, length)
        for tag, offset, length in entries
    )
    return head + directory + body


def name_table(names: dict[int, str], platform: int = 3) -> bytes:
    """A `name` table carrying the given name IDs, UTF-16BE as Windows does."""
    records = []
    strings = b""
    for name_id, value in sorted(names.items()):
        encoded = value.encode("utf-16-be" if platform in (0, 3) else "latin-1")
        records.append(
            struct.pack(">HHHHHH", platform, 1, 0, name_id, len(encoded), len(strings))
        )
        strings += encoded
    header = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records))
    return header + b"".join(records) + strings


def os2_table(fs_type: int) -> bytes:
    return b"\x00" * 8 + struct.pack(">H", fs_type) + b"\x00" * 70


def write(tmp_path, data: bytes, name: str = "font.ttf"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.fixture
def good_font(tmp_path):
    """A font the reader should accept, so the negatives mean something."""
    return write(
        tmp_path,
        build_font(
            {
                b"OS/2": os2_table(0),
                b"name": name_table({sfnt.NAME_FAMILY: "Testface", sfnt.NAME_SUBFAMILY: "Regular"}),
                b"glyf": b"\x00" * 16,
                b"loca": b"\x00" * 16,
            }
        ),
    )


# --------------------------------------------------------------------------
# The control: a font that is fine must still read
# --------------------------------------------------------------------------


def test_a_well_formed_font_reads(good_font):
    info = sfnt.read(good_font)
    assert info.family == "Testface"
    assert info.fs_type == 0
    assert info.outlines == sfnt.TRUETYPE


# --------------------------------------------------------------------------
# Structural corruption -- must raise FontFileError, never anything else
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "empty file"),
        (b"\x00\x01\x00", "shorter than a header"),
        (b"%PDF-1.7\n%truncated", "not a font at all"),
        (b"OTTO"[:2] + b"\x00" * 10, "truncated signature"),
        (b"\xff\xff\xff\xff" + b"\x00" * 32, "unrecognised signature"),
    ],
)
def test_a_file_that_is_not_a_font_is_refused_cleanly(tmp_path, data, reason):
    """Refused as FontFileError, so callers can catch one thing -- not a
    struct.error or an IndexError leaking out of the parser."""
    with pytest.raises(sfnt.FontFileError):
        sfnt.read(write(tmp_path, data), with_coverage=False)


def test_a_truncated_table_directory_is_refused(tmp_path):
    """The header promises more tables than the file contains."""
    head = struct.pack(">4sHHHH", b"\x00\x01\x00\x00", 40, 0, 0, 0)
    with pytest.raises(sfnt.FontFileError):
        sfnt.read(write(tmp_path, head + b"\x00" * 16), with_coverage=False)


def test_a_table_offset_past_the_end_of_the_file_is_refused(tmp_path):
    head = struct.pack(">4sHHHH", b"\x00\x01\x00\x00", 1, 0, 0, 0)
    directory = struct.pack(">4sIII", b"name", 0, 0xFFFFFF, 100)
    with pytest.raises(sfnt.FontFileError):
        sfnt.read(write(tmp_path, head + directory), with_coverage=False)


def test_a_font_with_no_usable_family_name_is_refused(tmp_path):
    """Without a family there is nothing to select the font by, so accepting it
    would put an unaddressable entry in the pool."""
    data = build_font({b"OS/2": os2_table(0), b"name": name_table({})})
    with pytest.raises(sfnt.FontFileError, match="no usable family name"):
        sfnt.read(write(tmp_path, data), with_coverage=False)


# --------------------------------------------------------------------------
# Table-level corruption -- degrade, do not crash, and never invent a value
# --------------------------------------------------------------------------


def test_a_truncated_os2_table_yields_no_fs_type_rather_than_zero(tmp_path):
    """This is the one that matters most. fs_type 0 means 'embedding allowed',
    so guessing 0 from an unreadable table would hand out embedding rights the
    font never granted. It must come back None."""
    data = build_font(
        {
            b"OS/2": b"\x00" * 4,  # far too short to hold fsType
            b"name": name_table({sfnt.NAME_FAMILY: "Testface"}),
        }
    )
    info = sfnt.read(write(tmp_path, data), with_coverage=False)
    assert info.fs_type is None, "an unreadable OS/2 must not read as permissive"


def test_a_missing_os2_table_yields_no_fs_type(tmp_path):
    data = build_font({b"name": name_table({sfnt.NAME_FAMILY: "Testface"})})
    info = sfnt.read(write(tmp_path, data), with_coverage=False)
    assert info.fs_type is None


def test_a_name_table_with_a_bad_encoding_does_not_crash(tmp_path):
    """An odd-length UTF-16 string is not decodable; the record is dropped
    rather than taking the whole font down."""
    records = struct.pack(">HHHHHH", 3, 1, 0, sfnt.NAME_SUBFAMILY, 3, 0)
    strings = b"\x00A\xff"  # odd length for utf-16-be
    bad_name = struct.pack(">HHH", 0, 1, 6 + 12) + records + strings

    data = build_font({b"name": bad_name})
    with pytest.raises(sfnt.FontFileError, match="no usable family name"):
        sfnt.read(write(tmp_path, data), with_coverage=False)


def test_a_truncated_name_table_is_survivable(tmp_path):
    data = build_font({b"name": b"\x00\x00\x00\x05"})
    with pytest.raises(sfnt.FontFileError, match="no usable family name"):
        sfnt.read(write(tmp_path, data), with_coverage=False)


def test_a_font_with_an_unreadable_cmap_is_never_usable(tmp_path):
    """Either degradation is safe -- refused outright, or read with no coverage
    so nothing ever selects it. What must not happen is a font claiming
    coverage it does not have, which is how \\x00 reaches the page."""
    data = build_font(
        {
            b"name": name_table({sfnt.NAME_FAMILY: "Testface"}),
            b"cmap": b"\xde\xad\xbe\xef",
        }
    )
    path = write(tmp_path, data)
    try:
        info = sfnt.read(path, with_coverage=True)
    except sfnt.FontFileError:
        return  # refused outright, which is the stronger of the two
    assert info.codepoints == set()
    assert not info.covers("A")


def test_a_font_with_no_cmap_at_all_reports_no_coverage(tmp_path):
    data = build_font({b"name": name_table({sfnt.NAME_FAMILY: "Testface"})})
    info = sfnt.read(write(tmp_path, data), with_coverage=True)
    assert info.codepoints == set()


def test_a_cmap_subtable_pointing_past_the_end_is_ignored(tmp_path):
    cmap = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 0xFFFF)
    data = build_font(
        {b"name": name_table({sfnt.NAME_FAMILY: "Testface"}), b"cmap": cmap}
    )
    info = sfnt.read(write(tmp_path, data), with_coverage=True)
    assert info.codepoints == set()


# --------------------------------------------------------------------------
# Outline kind -- what reportlab can actually load
# --------------------------------------------------------------------------


def test_glyf_without_loca_is_not_loadable(tmp_path):
    """reportlab fails with 'missing location table'. Colour emoji fonts have
    the TrueType signature but store bitmaps, which is where this bit."""
    data = build_font(
        {b"name": name_table({sfnt.NAME_FAMILY: "Testface"}), b"glyf": b"\x00" * 8}
    )
    info = sfnt.read(write(tmp_path, data), with_coverage=False)
    assert not info.loadable


def test_a_bitmap_only_font_is_not_loadable(tmp_path):
    data = build_font(
        {
            b"name": name_table({sfnt.NAME_FAMILY: "Testface"}),
            b"CBDT": b"\x00" * 8,
            b"CBLC": b"\x00" * 8,
        }
    )
    info = sfnt.read(write(tmp_path, data), with_coverage=False)
    assert not info.loadable
    assert info.outlines == sfnt.BITMAP


def test_a_cff_font_is_recognised_but_not_loadable_by_reportlab(tmp_path):
    """reportlab's TTFont handles TrueType outlines only."""
    data = build_font(
        {b"name": name_table({sfnt.NAME_FAMILY: "Testface"}), b"CFF ": b"\x00" * 8},
        signature=b"OTTO",
    )
    info = sfnt.read(write(tmp_path, data), with_coverage=False)
    assert info.outlines == sfnt.POSTSCRIPT
    assert not info.loadable


# --------------------------------------------------------------------------
# fsType: the gate that decides whether a font may be embedded at all
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fs_type", "embeddable"),
    [
        (0x0000, True),   # installable
        (0x0004, True),   # printing and preview
        (0x0008, True),   # editable
        (0x0002, False),  # restricted licence embedding
        (0x0006, False),  # restricted bit set alongside another
    ],
)
def test_fs_type_decides_embedding(tmp_path, fs_type, embeddable):
    """Restricted License Embedding is refused for everyone, including a font
    the user names explicitly. An open licence does not override it."""
    data = build_font(
        {
            b"OS/2": os2_table(fs_type),
            b"name": name_table({sfnt.NAME_FAMILY: "Testface"}),
            b"glyf": b"\x00" * 8,
            b"loca": b"\x00" * 8,
        }
    )
    info = sfnt.read(write(tmp_path, data), with_coverage=False)
    assert info.embeddable is embeddable, sfnt.describe_fs_type(info.fs_type)


def test_an_unknown_fs_type_is_described_rather_than_guessed(tmp_path):
    assert sfnt.describe_fs_type(None)


# --------------------------------------------------------------------------
# The parser is reached from a directory walk, so it must not raise there
# --------------------------------------------------------------------------


def test_a_directory_of_junk_does_not_break_discovery(tmp_path):
    """Discovery walks directories a user does not control. One bad file must
    not stop the pool being built."""
    from docfix.fonts import discover

    for index, junk in enumerate([b"", b"not a font", b"\x00\x01\x00\x00"]):
        write(tmp_path, junk, f"junk{index}.ttf")

    found = discover.scan(extra=[str(tmp_path)], use_cache=False)
    assert isinstance(found, list)
    assert not any(info.path.endswith("junk0.ttf") for info in found)
