"""Choosing a font per span of text, and reporting what nothing can render.

reportlab performs no font fallback. A font missing a glyph emits `\\x00`
silently -- verified: "English Привет 你好世界 end" set in DejaVu renders as
"English Привет \\x00\\x00\\x00\\x00 end" with no warning at all. Assigning a
font per span is therefore a correctness requirement, not a refinement.

Spans are chosen by **coverage, not by script**. Python exposes no Unicode
Script property, so a script-based approach would mean hand-maintaining range
tables that drift out of date. Asking each font what it can actually render is
simpler and correct by construction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from docfix.fonts.registry import Family, Pool

# WinAnsi, the base-14 encoding, is close enough to Latin-1 for this purpose.
# The point is not precision: it is that anything above it gets *reported* as
# unrenderable rather than silently emitted as the wrong glyph.
BASE14_LIMIT = 0x100

# CID fonts have no file whose cmap could be read, so their coverage is
# described by the Unicode blocks the collection serves. This is the one
# approximation in the module, and it is why the ranges are split by script:
# no single CID collection covers all of CJK.
#
# Measured by rendering and extracting back:
#   HeiseiKakuGo-W5     han (simplified AND traditional), kana -- NOT hangul
#   HYSMyeongJo-Medium  hangul, han, kana -- NOT simplified chinese
#   STSong-Light        same profile as HeiseiKakuGo-W5
#   MSung-Light         did not survive a round trip at all; not used
SCRIPT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "han": (
        (0x2E80, 0x2EFF),  # CJK radicals
        (0x3400, 0x4DBF),  # CJK extension A
        (0x4E00, 0x9FFF),  # CJK unified ideographs
        (0xF900, 0xFAFF),  # CJK compatibility ideographs
    ),
    "kana": (
        (0x3000, 0x303F),  # CJK symbols and punctuation
        (0x3040, 0x309F),  # Hiragana
        (0x30A0, 0x30FF),  # Katakana
        (0x3100, 0x312F),  # Bopomofo
        (0xFF00, 0xFFEF),  # Halfwidth and fullwidth forms
    ),
    "hangul": (
        (0x1100, 0x11FF),  # Hangul jamo
        (0x3130, 0x318F),  # Hangul compatibility jamo
        (0xAC00, 0xD7AF),  # Hangul syllables
    ),
}

CJK_RANGES: tuple[tuple[int, int], ...] = tuple(
    span for spans in SCRIPT_RANGES.values() for span in spans
)


def ranges_for(scripts: list[str]) -> tuple[tuple[int, int], ...]:
    return tuple(span for name in scripts for span in SCRIPT_RANGES.get(name, ()))


def in_ranges(code: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= code <= high for low, high in spans)


def in_cjk(code: int) -> bool:
    return in_ranges(code, CJK_RANGES)


@dataclass
class FontOption:
    """One candidate in a fallback chain."""

    name: str                                   # the registered reportlab name
    family: str                                 # human-readable family name
    covers: Callable[[int], bool] = field(repr=False, default=lambda _c: True)
    is_cid: bool = False
    # True for the PDF base-14, where reportlab maps <b>/<i> to a built-in face
    # itself. A real TrueType family instead names its bold face directly,
    # because a <font face> tag overrides the family mapping and would lose the
    # weight if nested inside <b>.
    use_tags: bool = False
    _family: Family | None = field(default=None, repr=False, compare=False)

    def can_render(self, char: str) -> bool:
        return char.isspace() or self.covers(ord(char))

    def name_for(self, bold: bool = False, italic: bool = False) -> tuple[str, bool]:
        """Registered face name for a style, and whether it was the exact one."""
        if self._family is None:
            return self.name, not (bold or italic)
        from docfix.fonts.registry import face_name

        return face_name(self._family, bold, italic)


def option_for_family(family: Family, registered_name: str) -> FontOption:
    codepoints = family.coverage()
    return FontOption(
        name=registered_name,
        family=family.name,
        covers=lambda code: code in codepoints,
        _family=family,
    )


def option_for_cid(
    registered_name: str, scripts: list[str] | None = None
) -> FontOption:
    spans = ranges_for(scripts) if scripts else CJK_RANGES
    return FontOption(
        name=registered_name,
        family=registered_name,
        covers=lambda code: in_ranges(code, spans),
        is_cid=True,
    )


def base14_option(category: str = "sans") -> FontOption:
    """The last-resort chain when no real font is available.

    Coverage is capped at Latin-1 deliberately, so text beyond it is reported
    rather than silently mis-rendered -- which is what the base-14 fonts do on
    their own.
    """
    name = {"serif": "Times-Roman", "mono": "Courier"}.get(category, "Helvetica")
    return FontOption(
        name=name,
        family=name,
        covers=lambda code: code < BASE14_LIMIT,
        use_tags=True,
    )


def resolve_spans(
    text: str, chain: list[FontOption]
) -> tuple[list[tuple[str, FontOption]], set[str]]:
    """Split text into spans, each rendered by the first font that covers it.

    Returns the spans and the set of characters no font in the chain could
    render. Unrenderable characters stay in the text -- dropping them silently
    is the failure mode this whole module exists to prevent -- and are reported
    so the caller can surface them.
    """
    if not text:
        return [], set()
    if not chain:
        return [(text, FontOption(name="Helvetica", family="Helvetica", use_tags=True))], set()

    primary = chain[0]
    spans: list[tuple[str, FontOption]] = []
    missing: set[str] = set()

    current: list[str] = []
    current_font = primary

    for char in text:
        chosen = None
        # Whitespace never forces a font change; it would fragment every span.
        if char.isspace():
            chosen = current_font
        else:
            for option in chain:
                if option.can_render(char):
                    chosen = option
                    break
        if chosen is None:
            missing.add(char)
            chosen = current_font  # Keep the character; the reader sees a gap.

        if chosen is not current_font and current:
            spans.append(("".join(current), current_font))
            current = []
        current_font = chosen
        current.append(char)

    if current:
        spans.append(("".join(current), current_font))
    return spans, missing


def build_chain(pool: Pool, families: list[str], fallback: list[str] | None = None,
                cid: str | None = None,
                cid_defaults: list[dict] | None = None) -> tuple[list[FontOption], list[str]]:
    """Assemble a fallback chain from family names, in order.

    Returns the chain and the names that could not be resolved, so the caller
    can report a template asking for a font this machine does not have.
    """
    from docfix.fonts.registry import FontError, register, register_cid

    chain: list[FontOption] = []
    unresolved: list[str] = []
    seen: set[str] = set()

    wanted = list(families) + list(fallback or [])
    for name in wanted:
        key = name.lower().strip()
        if key in seen:
            continue
        seen.add(key)

        family = pool.get(name)
        if family is None or not family.usable:
            unresolved.append(name)
            continue
        try:
            chain.append(option_for_family(family, register(family)))
        except FontError:
            unresolved.append(name)

    # The catalogue's own fallbacks go last, so a template's choices win.
    for family in pool.fallback_families():
        if family.name.lower() in seen:
            continue
        seen.add(family.name.lower())
        try:
            chain.append(option_for_family(family, register(family)))
        except FontError:
            continue

    # A template's own CJK choice goes first, then the catalogue defaults. CID
    # fonts cost nothing to register -- no file, no download -- so including
    # them by default is what makes CJK work without configuration.
    cid_entries: list[dict] = []
    if cid:
        cid_entries.append({"name": cid, "scripts": None})
    cid_entries.extend(cid_defaults or [])

    for entry in cid_entries:
        name = entry.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            chain.append(option_for_cid(register_cid(name), entry.get("scripts")))
        except FontError:
            if cid and name == cid:
                unresolved.append(name)

    return chain, unresolved
