"""The font pool: licence classification, family assembly, reportlab registration.

Two independent licence checks, and they answer different questions:

* **`fsType`**, read from the font file, answers *may this font be embedded at
  all?* A font whose vendor set Restricted License Embedding is refused
  outright, whoever asked for it.
* **The catalogue** answers *may docfix choose this font on the user's behalf?*
  Only fonts declaring a recognised open licence are auto-selected. A font the
  user names explicitly is still allowed -- they may well own it, and embedding
  is legally distinct from redistributing -- but it warns.

docfix ships no fonts, so it redistributes nothing.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any

import yaml  # type: ignore[import-untyped]

from docfix.fonts import discover, sfnt

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "catalog.yaml")

STYLES = ("regular", "bold", "italic", "bolditalic")


class FontError(RuntimeError):
    """Raised when a requested font cannot be used."""


@dataclass(frozen=True)
class License:
    id: str
    name: str
    open: bool
    url_patterns: tuple[str, ...] = ()
    text_patterns: tuple[str, ...] = ()

    def matches(self, info: sfnt.FontInfo) -> bool:
        url = (info.license_url or "").lower()
        text = (info.license_text or "").lower()
        if any(pattern in url for pattern in self.url_patterns):
            return True
        return any(pattern in text for pattern in self.text_patterns)


UNKNOWN_LICENSE = License(
    id="unknown",
    name="not declared by the font",
    open=False,
)


@dataclass
class Catalog:
    licenses: list[License] = field(default_factory=list)
    categories: dict[str, str] = field(default_factory=dict)
    fallback: list[str] = field(default_factory=list)
    cid_fonts: list[dict] = field(default_factory=list)

    def classify(self, info: sfnt.FontInfo) -> License:
        """The first matching signature wins, so order the catalogue specific-first."""
        for candidate in self.licenses:
            if candidate.matches(info):
                return candidate
        return UNKNOWN_LICENSE

    def category_for(self, family: str) -> str:
        known = self.categories.get(family.lower())
        if known:
            return known
        lowered = family.lower()
        if "mono" in lowered or "code" in lowered or "courier" in lowered:
            return "mono"
        if "serif" in lowered and "sans" not in lowered:
            return "serif"
        return "sans"


def load_catalog(path: str = CATALOG_PATH) -> Catalog:
    with open(path, encoding="utf-8") as handle:
        data: dict[str, Any] = yaml.safe_load(handle) or {}

    licenses = [
        License(
            id=entry["id"],
            name=entry.get("name", entry["id"]),
            open=bool(entry.get("open", False)),
            url_patterns=tuple(p.lower() for p in entry.get("url_patterns", [])),
            text_patterns=tuple(p.lower() for p in entry.get("text_patterns", [])),
        )
        for entry in data.get("licenses", [])
    ]
    categories = {
        entry["name"].lower(): entry.get("category", "sans")
        for entry in data.get("families", [])
    }
    return Catalog(
        licenses=licenses,
        categories=categories,
        fallback=list(data.get("fallback", [])),
        cid_fonts=list(data.get("cid_fonts", [])),
    )


@dataclass
class Family:
    """One font family and everything known about it."""

    name: str
    category: str
    license: License
    faces: dict[str, sfnt.FontInfo] = field(default_factory=dict)
    # True when this family was resolved from the pinned cache rather than
    # from whatever the machine happens to have installed.
    pinned: bool = False
    _coverage: set[int] | None = field(default=None, repr=False, compare=False)

    @property
    def regular(self) -> sfnt.FontInfo | None:
        for style in STYLES:
            if style in self.faces:
                return self.faces[style]
        return None

    @property
    def styles(self) -> list[str]:
        return [style for style in STYLES if style in self.faces]

    @property
    def embeddable(self) -> bool:
        """False if any face forbids embedding -- the strictest face governs."""
        return all(face.embeddable for face in self.faces.values())

    @property
    def loadable(self) -> bool:
        return bool(self.regular and self.regular.loadable)

    @property
    def usable(self) -> bool:
        return self.loadable and self.embeddable

    @property
    def auto_selectable(self) -> bool:
        return self.usable and self.license.open

    def coverage(self) -> set[int]:
        """Codepoints the regular face can render, read on first use."""
        if self._coverage is None:
            face = self.regular
            try:
                self._coverage = (
                    sfnt.read(face.path).codepoints if face else set()
                )
            except sfnt.FontFileError:
                self._coverage = set()
        return self._coverage

    def covers(self, text: str) -> bool:
        codepoints = self.coverage()
        return all(ord(char) in codepoints for char in text if not char.isspace())


@dataclass
class Pool:
    families: dict[str, Family] = field(default_factory=dict)
    catalog: Catalog = field(default_factory=Catalog)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def get(self, name: str) -> Family | None:
        return self.families.get(name.lower().strip())

    def names(self) -> list[str]:
        return sorted(family.name for family in self.families.values())

    def usable_families(self) -> list[Family]:
        return sorted(
            (f for f in self.families.values() if f.usable), key=lambda f: f.name
        )

    def by_category(self, category: str) -> list[Family]:
        return [f for f in self.usable_families() if f.category == category]

    def families_covering(self, probe: str, auto_only: bool = True) -> list[Family]:
        """Families that can render every character of a probe string.

        Coverage is loaded lazily per family, so this is only paid for when
        something actually asks -- embedding a CJK font, for instance.
        """
        found = [
            family
            for family in self.usable_families()
            if (family.auto_selectable if auto_only else True) and family.covers(probe)
        ]
        # Widest coverage first: a font with more glyphs is the safer choice
        # when several qualify.
        return sorted(found, key=lambda f: -len(f.coverage()))

    def pinned_families(self) -> list[Family]:
        return [f for f in self.usable_families() if f.pinned]

    def unpinned_families(self) -> list[Family]:
        return [f for f in self.usable_families() if not f.pinned]

    def fallback_families(self) -> list[Family]:
        """Catalogue-preferred fallbacks that are actually installed and open."""
        out: list[Family] = []
        for name in self.catalog.fallback:
            family = self.get(name)
            if family and family.auto_selectable:
                out.append(family)
        return out


def face_name(family: Family, bold: bool = False, italic: bool = False) -> tuple[str, bool]:
    """The registered face name for a style, and whether it is the one asked for.

    A family with no bold face degrades to its regular one: reportlab cannot
    synthesise weights for a TrueType font, and rendering nothing would be worse
    than rendering unbolded. The caller reports the degradation.
    """
    base = family.name.replace(" ", "")
    wanted = (
        "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    )
    available = family.styles

    if wanted in available:
        return (base if wanted == "regular" else f"{base}-{wanted}"), True

    # Closest acceptable substitute, most specific first.
    for candidate in {
        "bolditalic": ("bold", "italic", "regular"),
        "bold": ("bolditalic", "regular"),
        "italic": ("bolditalic", "regular"),
        "regular": ("bold", "italic", "bolditalic"),
    }[wanted]:
        if candidate in available:
            return (base if candidate == "regular" else f"{base}-{candidate}"), False
    return base, False


def _split_stack(stack: str) -> list[str]:
    """Split a CSS-style font stack into candidate family names."""
    parts = []
    for chunk in (stack or "").split(","):
        cleaned = chunk.strip().strip("'\"").strip()
        if cleaned:
            parts.append(cleaned)
    return parts


def _within(path: str, root: str | None) -> bool:
    """Whether a font file lives inside the cache directory.

    A bare startswith would also match a sibling like `.../fonts-extra`, whose
    contents are unverified system fonts -- and marking those pinned is exactly
    the claim the cache exists to make truthfully.
    """
    if not root:
        return False
    resolved = os.path.realpath(path)
    return resolved == root or resolved.startswith(root.rstrip(os.sep) + os.sep)


def build_pool(
    extra_dirs: list[str] | None = None,
    catalog: Catalog | None = None,
    use_cache: bool = True,
) -> Pool:
    """Assemble the pool from the pinned cache plus whatever the machine has."""
    catalog = catalog or load_catalog()
    pool = Pool(catalog=catalog)

    from docfix.fonts.cache import cache_dir

    cache_root = os.path.realpath(cache_dir()) if use_cache else None

    for info in discover.scan(extra_dirs, use_cache=use_cache):
        if not info.loadable:
            pool.skipped.append((info.path, f"{info.outlines} outlines cannot be rendered"))
            continue
        if not info.embeddable:
            pool.skipped.append(
                (info.path, f"embedding not permitted (fsType {info.fs_type})")
            )
            continue

        from_cache = _within(info.path, cache_root)

        key = info.family.lower().strip()
        family = pool.families.get(key)
        if family is None:
            family = Family(
                name=info.family,
                category=catalog.category_for(info.family),
                license=catalog.classify(info),
                pinned=from_cache,
            )
            pool.families[key] = family
        # Keep the first face seen for a style; directories are walked in order.
        kept = family.faces.setdefault(info.style, info)
        # "Pinned" has to mean *every* face came from the cache, not just the
        # first one seen. pinned.yaml pins a single face of Noto Sans, so on any
        # machine that also has it installed the bold and italic faces come from
        # the system -- and reporting the family as pinned would tell the user
        # the output is reproducible while machine fonts render half of it.
        if kept is info and not from_cache:
            family.pinned = False

    return pool


_POOL: Pool | None = None
_LOCK = threading.Lock()


def pool(refresh: bool = False, extra_dirs: list[str] | None = None) -> Pool:
    """The process-wide pool, built once."""
    global _POOL
    with _LOCK:
        if _POOL is None or refresh:
            _POOL = build_pool(extra_dirs)
        return _POOL


# --------------------------------------------------------------------------
# reportlab registration
# --------------------------------------------------------------------------

_REGISTERED: dict[str, str] = {}


def register(family: Family) -> str:
    """Register a family with reportlab and return its font name.

    Refuses any font whose vendor forbids embedding -- that check happens here,
    at the point of use, not only during discovery.
    """
    if not family.embeddable:
        raise FontError(
            f"{family.name!r} may not be embedded: its fsType is "
            f"{sfnt.describe_fs_type(family.regular.fs_type if family.regular else None)}"
        )
    if not family.loadable:
        raise FontError(
            f"{family.name!r} cannot be rendered: "
            f"{family.regular.outlines if family.regular else 'unknown'} outlines"
        )

    if family.name in _REGISTERED:
        return _REGISTERED[family.name]

    from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
    from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]

    base = family.name.replace(" ", "")
    registered: dict[str, str] = {}
    for style in family.styles:
        face = family.faces[style]
        name = base if style == "regular" else f"{base}-{style}"
        try:
            pdfmetrics.registerFont(TTFont(name, face.path))
        except Exception as exc:  # noqa: BLE001 - a bad face must not kill the run
            raise FontError(f"reportlab could not load {face.path}: {exc}") from exc
        registered[style] = name

    normal = registered.get("regular") or next(iter(registered.values()))
    # A family with no bold face falls back to its regular one: reportlab cannot
    # synthesise weights, and rendering nothing would be worse.
    pdfmetrics.registerFontFamily(
        base,
        normal=normal,
        bold=registered.get("bold", normal),
        italic=registered.get("italic", normal),
        boldItalic=registered.get("bolditalic", registered.get("bold", normal)),
    )

    _REGISTERED[family.name] = normal
    return normal


def register_cid(name: str) -> str:
    """Register one of reportlab's built-in CID fonts (CJK, no font file)."""
    if name in _REGISTERED:
        return _REGISTERED[name]

    from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # type: ignore[import-untyped]

    try:
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    except Exception as exc:  # noqa: BLE001
        raise FontError(f"CID font {name!r} is not available: {exc}") from exc

    _REGISTERED[name] = name
    return name


def reset_pool() -> None:
    """Forget the assembled pool, so the next call rebuilds it.

    Installing or removing pinned fonts changes what is available, and the pool
    is memoised for the life of the process -- without this a library caller
    that installs fonts and then formats a document would still see the old set.
    """
    global _POOL
    with _LOCK:
        _POOL = None
    reset_registrations()


def reset_registrations() -> None:
    """Forget what has been registered. For tests."""
    _REGISTERED.clear()
