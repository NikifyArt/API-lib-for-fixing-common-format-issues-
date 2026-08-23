"""A minimal reader for TrueType/OpenType font files.

Only what the font pool needs: which family and style a file holds, whether its
licence permits embedding, which characters it can actually render, and whether
reportlab can load it at all.

Deliberately standalone rather than built on reportlab's parser: reportlab does
not expose `fsType`, its internals are not a stable API, and the licence gate
must work on files reportlab would refuse to load.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# fsType lives in the OS/2 table and states what embedding the vendor permits.
FSTYPE_INSTALLABLE = 0x0000
FSTYPE_RESTRICTED = 0x0002
FSTYPE_PREVIEW_PRINT = 0x0004
FSTYPE_EDITABLE = 0x0008
FSTYPE_NO_SUBSET = 0x0100
FSTYPE_BITMAP_ONLY = 0x0200

TRUETYPE = "truetype"
POSTSCRIPT = "postscript"
# Colour emoji fonts carry a TrueType signature but store glyphs as bitmaps
# (CBDT/sbix/EBDT) with no `glyf`/`loca`. reportlab cannot render them.
BITMAP = "bitmap"

NAME_FAMILY = 1
NAME_SUBFAMILY = 2
NAME_TYPO_FAMILY = 16
NAME_TYPO_SUBFAMILY = 17
# Fonts declare their own licence terms; read them rather than assuming.
NAME_LICENSE = 13
NAME_LICENSE_URL = 14


class FontFileError(ValueError):
    """Raised for a file that is not a usable font."""


@dataclass
class FontInfo:
    path: str
    family: str
    subfamily: str
    outlines: str
    fs_type: int | None
    codepoints: set[int] = field(default_factory=set, repr=False)
    # What the font file itself declares about its licence (name IDs 13 and 14).
    license_text: str = ""
    license_url: str = ""

    @property
    def embeddable(self) -> bool:
        """False only when the vendor explicitly forbids embedding."""
        if self.fs_type is None:
            return True  # No OS/2 table states no restriction.
        return not (self.fs_type & FSTYPE_RESTRICTED)

    @property
    def loadable(self) -> bool:
        """reportlab's TTFont handles TrueType *outlines* only.

        Not merely a TrueType signature: a colour bitmap font has one but no
        outline tables, and reportlab fails on it with "missing location table".
        """
        return self.outlines == TRUETYPE

    @property
    def style(self) -> str:
        """Normalised style key: regular, bold, italic, or bolditalic."""
        lowered = self.subfamily.lower()
        bold = "bold" in lowered
        italic = "italic" in lowered or "oblique" in lowered
        if bold and italic:
            return "bolditalic"
        if bold:
            return "bold"
        if italic:
            return "italic"
        return "regular"

    def covers(self, text: str) -> bool:
        return all(ord(char) in self.codepoints for char in text if not char.isspace())


def _read_tables(handle) -> dict[bytes, tuple[int, int]]:
    """Table directory: tag -> (offset, length). Handles font collections."""
    head = handle.read(12)
    if len(head) < 12:
        raise FontFileError("file is too short to be a font")

    if head[:4] == b"ttcf":
        # A collection; read the directory of its first font.
        handle.seek(12)
        (first,) = struct.unpack(">I", handle.read(4))
        handle.seek(first)
        head = handle.read(12)

    if head[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO", b"typ1"):
        raise FontFileError(f"unrecognised sfnt signature {head[:4]!r}")

    (count,) = struct.unpack(">H", head[4:6])
    raw = handle.read(16 * count)
    tables: dict[bytes, tuple[int, int]] = {}
    for index in range(count):
        tag, _checksum, offset, length = struct.unpack(
            ">4sIII", raw[index * 16 : (index + 1) * 16]
        )
        tables[tag] = (offset, length)
    return tables


def _read_fs_type(handle, tables) -> int | None:
    entry = tables.get(b"OS/2")
    if not entry:
        return None
    handle.seek(entry[0])
    data = handle.read(12)
    if len(data) < 10:
        return None
    return struct.unpack(">H", data[8:10])[0]


_WANTED_NAMES = frozenset(
    {NAME_FAMILY, NAME_SUBFAMILY, NAME_TYPO_FAMILY, NAME_TYPO_SUBFAMILY,
     NAME_LICENSE, NAME_LICENSE_URL}
)


def _decode_name(data: bytes, platform: int) -> str:
    encoding = "utf-16-be" if platform in (0, 3) else "latin-1"
    try:
        return data.decode(encoding, errors="replace").strip("\x00").strip()
    except (UnicodeDecodeError, LookupError):
        return ""


def _read_names(handle, tables) -> dict[int, str]:
    entry = tables.get(b"name")
    if not entry:
        return {}

    base = entry[0]
    handle.seek(base)
    header = handle.read(6)
    if len(header) < 6:
        return {}
    _fmt, count, string_offset = struct.unpack(">HHH", header)
    records = handle.read(12 * count)

    found: dict[int, str] = {}
    for index in range(count):
        platform, _enc, _lang, name_id, length, offset = struct.unpack(
            ">HHHHHH", records[index * 12 : (index + 1) * 12]
        )
        if name_id not in _WANTED_NAMES:
            continue
        handle.seek(base + string_offset + offset)
        text = _decode_name(handle.read(length), platform)
        # Prefer the first readable value; Windows records come first in practice.
        if text and name_id not in found:
            found[name_id] = text
    return found


def _parse_cmap_format4(data: bytes) -> set[int]:
    if len(data) < 14:
        return set()
    (seg_x2,) = struct.unpack(">H", data[6:8])
    count = seg_x2 // 2
    if count == 0:
        return set()

    def words(start: int) -> tuple[int, ...]:
        return struct.unpack(f">{count}H", data[start : start + seg_x2])

    end_codes = words(14)
    start_codes = words(16 + seg_x2)
    deltas = struct.unpack(f">{count}h", data[16 + seg_x2 * 2 : 16 + seg_x2 * 3])
    range_offset_base = 16 + seg_x2 * 3
    range_offsets = words(range_offset_base)

    covered: set[int] = set()
    for index in range(count):
        start, end = start_codes[index], end_codes[index]
        if start > end or end == 0xFFFF and start == 0xFFFF:
            continue
        for code in range(start, min(end, 0xFFFE) + 1):
            if range_offsets[index] == 0:
                glyph = (code + deltas[index]) & 0xFFFF
            else:
                pos = (
                    range_offset_base
                    + index * 2
                    + range_offsets[index]
                    + (code - start) * 2
                )
                if pos + 2 > len(data):
                    continue
                (glyph,) = struct.unpack(">H", data[pos : pos + 2])
                if glyph:
                    glyph = (glyph + deltas[index]) & 0xFFFF
            # Glyph 0 is .notdef: present in the table but renders as nothing.
            if glyph:
                covered.add(code)
    return covered


def _parse_cmap_format12(data: bytes) -> set[int]:
    if len(data) < 16:
        return set()
    (groups,) = struct.unpack(">I", data[12:16])
    covered: set[int] = set()
    for index in range(groups):
        offset = 16 + index * 12
        if offset + 12 > len(data):
            break
        start, end, start_glyph = struct.unpack(">III", data[offset : offset + 12])
        if start > end or end > 0x10FFFF:
            continue
        if start_glyph == 0:
            # The first codepoint would map to .notdef; skip just that one.
            start += 1
        covered.update(range(start, end + 1))
    return covered


def _read_codepoints(handle, tables) -> set[int]:
    entry = tables.get(b"cmap")
    if not entry:
        return set()

    base = entry[0]
    handle.seek(base)
    header = handle.read(4)
    if len(header) < 4:
        return set()
    _version, count = struct.unpack(">HH", header)
    records = handle.read(8 * count)

    # Prefer full-Unicode subtables over BMP-only ones.
    preference = {(3, 10): 0, (0, 4): 1, (0, 6): 2, (3, 1): 3, (0, 3): 4, (0, 0): 5}
    candidates: list[tuple[int, int]] = []
    for index in range(count):
        platform, encoding, offset = struct.unpack(
            ">HHI", records[index * 8 : (index + 1) * 8]
        )
        rank = preference.get((platform, encoding))
        if rank is not None:
            candidates.append((rank, base + offset))

    for _rank, offset in sorted(candidates):
        handle.seek(offset)
        head = handle.read(4)
        if len(head) < 4:
            continue
        (subtable_format,) = struct.unpack(">H", head[:2])

        if subtable_format == 4:
            (length,) = struct.unpack(">H", head[2:4])
            handle.seek(offset)
            covered = _parse_cmap_format4(handle.read(length))
        elif subtable_format == 12:
            handle.seek(offset + 4)
            (length,) = struct.unpack(">I", handle.read(4))
            handle.seek(offset)
            covered = _parse_cmap_format12(handle.read(length))
        else:
            continue

        if covered:
            return covered
    return set()


def read(path: str, with_coverage: bool = True) -> FontInfo:
    """Read a font file's identity, embedding permission, and character coverage."""
    try:
        with open(path, "rb") as handle:
            tables = _read_tables(handle)
            names = _read_names(handle, tables)
            fs_type = _read_fs_type(handle, tables)
            codepoints = _read_codepoints(handle, tables) if with_coverage else set()
    except FontFileError:
        raise
    except (OSError, struct.error) as exc:
        raise FontFileError(f"could not read {path}: {exc}") from exc

    family = names.get(NAME_TYPO_FAMILY) or names.get(NAME_FAMILY) or ""
    subfamily = names.get(NAME_TYPO_SUBFAMILY) or names.get(NAME_SUBFAMILY) or "Regular"
    if not family:
        raise FontFileError(f"{path} has no usable family name")

    if b"glyf" in tables and b"loca" in tables:
        outlines = TRUETYPE
    elif b"CFF " in tables:
        outlines = POSTSCRIPT
    else:
        outlines = BITMAP

    return FontInfo(
        path=path,
        family=family,
        subfamily=subfamily,
        outlines=outlines,
        fs_type=fs_type,
        codepoints=codepoints,
        license_text=names.get(NAME_LICENSE, ""),
        license_url=names.get(NAME_LICENSE_URL, ""),
    )


def describe_fs_type(fs_type: int | None) -> str:
    """Plain-language summary of what a font's fsType permits."""
    if fs_type is None:
        return "unspecified (no OS/2 table)"
    if fs_type & FSTYPE_RESTRICTED:
        return "restricted - embedding not permitted"

    parts = []
    if fs_type & FSTYPE_EDITABLE:
        parts.append("editable embedding")
    elif fs_type & FSTYPE_PREVIEW_PRINT:
        parts.append("preview & print")
    else:
        parts.append("installable (unrestricted)")
    if fs_type & FSTYPE_NO_SUBSET:
        parts.append("no subsetting")
    if fs_type & FSTYPE_BITMAP_ONLY:
        parts.append("bitmap only")
    return ", ".join(parts)
