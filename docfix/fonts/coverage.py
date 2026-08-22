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

# CID fonts have no file to read a cmap from, so their coverage is described by
# the Unicode blocks the collection is for. The only approximation here.
CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x2EFF),  # CJK radicals
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x3100, 0x312F),  # Bopomofo
    (0x3130, 0x318F),  # Hangul compatibility jamo
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF00, 0xFFEF),  # Halfwidth and fullwidth forms
)


def in_cjk(code: int) -> bool:
    return any(low <= code <= high for low, high in CJK_RANGES)


@dataclass
class FontOption:
    """One candidate in a fallback chain."""

    name: str                                   # the registered reportlab name
    family: str                                 # human-readable family name
    covers: Callable[[int], bool] = field(repr=False, default=lambda _c: True)
    is_cid: bool = False
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


def option_for_cid(registered_name: str) -> FontOption:
    return FontOption(
        name=registered_name,
        family=registered_name,
        covers=in_cjk,
        is_cid=True,
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
        return [(text, FontOption(name="Helvetica", family="Helvetica"))], set()

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
                cid: str | None = None) -> tuple[list[FontOption], list[str]]:
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

    if cid:
        try:
            chain.append(option_for_cid(register_cid(cid)))
        except FontError:
            unresolved.append(cid)

    return chain, unresolved
