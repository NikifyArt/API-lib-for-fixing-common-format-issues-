"""PDF adapter: scan for risk, extract to the IR, generate a fresh PDF.

PDF is fixed-layout and is not symmetrical with the other formats. `docfix`
never edits a PDF in place. Instead it extracts the document into the IR,
formats there, and **generates a new PDF** from the result.

That conversion loses things -- figures, exact layout, multi-column reading
order -- so `scan()` inspects the file page by page first and reports what would
be at risk. The CLI shows that report and asks before converting anything.

Both `pdfplumber` (reading) and `reportlab` (writing) are optional:

    pip install "docfix[pdf]"
"""

from __future__ import annotations

import collections
import os
import re
import statistics
from dataclasses import dataclass, field

from docfix.detect.rules import ERROR, INFO, WARNING, Issue
from docfix.fonts import FontOption, build_chain, pool, resolve_spans
from docfix.fonts.coverage import base14_option
from docfix.ir import (
    Block,
    BlockQuote,
    CodeBlock,
    Document,
    Heading,
    Image,
    ListBlock,
    ListItem,
    Paragraph,
    Run,
    Table,
    ThematicBreak,
    iter_runs,
    plain_text,
    walk,
)
from docfix.templates import Template

EXTENSIONS = (".pdf",)

BLOCKER = "blocker"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

SEVERITY_RANK = {BLOCKER: 0, HIGH: 1, MEDIUM: 2, LOW: 3}


class MissingDependencyError(ImportError):
    """Raised when the PDF extras are not installed."""


def _require(module: str, extra: str = "pdf"):
    try:
        return __import__(module)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        # Not just ImportError: a broken native dependency (a bad `cryptography`
        # build, say) surfaces as a Rust panic, and a raw panic traceback tells
        # the user nothing about what to install.
        raise MissingDependencyError(
            f"{module} is needed for PDF support but could not be imported "
            f"({type(exc).__name__}: {exc}); "
            f'install the extras with: pip install "docfix[{extra}]"'
        ) from exc


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskFactor:
    code: str
    severity: str
    detail: str


@dataclass
class PageRisk:
    page: int
    factors: list[RiskFactor] = field(default_factory=list)

    @property
    def severity(self) -> str | None:
        if not self.factors:
            return None
        return min((f.severity for f in self.factors), key=lambda s: SEVERITY_RANK[s])


@dataclass
class PdfScan:
    """What a conversion would put at risk, page by page."""

    path: str
    page_count: int
    pages: list[PageRisk] = field(default_factory=list)

    @property
    def risky_pages(self) -> list[PageRisk]:
        return [p for p in self.pages if p.factors]

    @property
    def blocking_pages(self) -> list[PageRisk]:
        return [p for p in self.pages if p.severity == BLOCKER]

    @property
    def serious_pages(self) -> list[PageRisk]:
        return [p for p in self.pages if p.severity in (BLOCKER, HIGH)]

    @property
    def worst_severity(self) -> str | None:
        severities = [p.severity for p in self.pages if p.severity]
        if not severities:
            return None
        return min(severities, key=lambda s: SEVERITY_RANK[s])

    def factor_counts(self) -> dict[str, int]:
        counts: collections.Counter[str] = collections.Counter()
        for page in self.pages:
            for factor in page.factors:
                counts[factor.code] += 1
        return dict(counts.most_common())

    def needs_confirmation(self) -> bool:
        """True when a conversion would plausibly produce an unwanted result."""
        return bool(self.serious_pages)

    def report(self, max_pages: int = 12) -> str:
        """A human-readable report, scaled to the document's length."""
        lines = [
            f"{os.path.basename(self.path)}: {self.page_count} page"
            f"{'s' if self.page_count != 1 else ''}, "
            f"{len(self.risky_pages)} with conversion risks"
        ]

        if not self.risky_pages:
            lines.append("  Nothing found that conversion would damage.")
            return "\n".join(lines)

        lines.append("")
        for code, count in self.factor_counts().items():
            share = f"{count}/{self.page_count} pages"
            lines.append(f"  {RISK_LABELS.get(code, code):24} {share}")

        lines.append("")
        shown = self.risky_pages[:max_pages]
        for page in shown:
            detail = "; ".join(f"{f.severity}: {f.detail}" for f in page.factors)
            lines.append(f"  page {page.page:>4}  {detail}")
        if len(self.risky_pages) > max_pages:
            lines.append(f"  ... and {len(self.risky_pages) - max_pages} more pages")

        return "\n".join(lines)


RISK_LABELS = {
    "no-text-layer": "no text layer (scanned)",
    "multi-column": "multi-column layout",
    "table": "tables",
    "image": "images / figures",
    "vector-graphics": "charts / drawings",
    "rotated-text": "rotated text",
    "annotations": "annotations / form fields",
    "undecodable-text": "undecodable characters",
}


def _column_count(words: list[dict], page_width: float) -> int:
    """Guess the number of text columns from the spread of word start positions."""
    if len(words) < 40 or not page_width:
        return 1

    starts = sorted(w["x0"] for w in words)
    # A gutter is a horizontal band, wider than a normal word gap, that no line
    # starts in. Two clusters of line-starts means two columns.
    gap_threshold = page_width * 0.18
    clusters = 1
    for previous, current in zip(starts, starts[1:], strict=False):
        if current - previous > gap_threshold:
            clusters += 1
    return min(clusters, 4)


def _scan_page(page, index: int) -> PageRisk:
    risk = PageRisk(page=index)

    try:
        text = page.extract_text() or ""
    except Exception:  # noqa: BLE001 - a broken page must not abort the scan
        text = ""

    try:
        words = page.extract_words(extra_attrs=["upright"]) or []
    except Exception:  # noqa: BLE001
        words = []

    images = list(getattr(page, "images", []) or [])
    curves = list(getattr(page, "curves", []) or [])
    lines = list(getattr(page, "lines", []) or [])
    rects = list(getattr(page, "rects", []) or [])
    annots = list(getattr(page, "annots", []) or [])

    if not text.strip():
        if images:
            risk.factors.append(
                RiskFactor(
                    "no-text-layer",
                    BLOCKER,
                    "page is an image with no text layer; nothing can be extracted "
                    "without OCR",
                )
            )
        else:
            risk.factors.append(
                RiskFactor("no-text-layer", MEDIUM, "page has no extractable text")
            )
        return risk

    columns = _column_count(words, float(page.width or 0))
    if columns > 1:
        risk.factors.append(
            RiskFactor(
                "multi-column",
                HIGH,
                f"looks like {columns} columns; extracted reading order may be wrong",
            )
        )

    try:
        tables = page.find_tables() or []
    except Exception:  # noqa: BLE001
        tables = []
    if tables:
        risk.factors.append(
            RiskFactor(
                "table",
                HIGH,
                f"{len(tables)} table(s); structure is approximated from ruling lines",
            )
        )

    rotated = sum(1 for w in words if w.get("upright") is False)
    if rotated > 3:
        risk.factors.append(
            RiskFactor("rotated-text", HIGH, f"{rotated} rotated words will not extract cleanly")
        )

    if images:
        risk.factors.append(
            RiskFactor("image", MEDIUM, f"{len(images)} image(s) will not carry over")
        )

    # Bars in a chart are rects, points are curves, table rules are lines --
    # all three have to be counted or a plain bar chart slips through.
    shapes = len(curves) + len(lines) + len(rects)
    if shapes > 40:
        risk.factors.append(
            RiskFactor(
                "vector-graphics",
                MEDIUM,
                f"{shapes} vector shapes (a chart, diagram, or table rules) will be lost",
            )
        )

    if annots:
        risk.factors.append(
            RiskFactor(
                "annotations", MEDIUM, f"{len(annots)} annotation(s) or form field(s) will be lost"
            )
        )

    bad = text.count("\ufffd") + len(re.findall(r"\(cid:\d+\)", text))
    if bad:
        risk.factors.append(
            RiskFactor(
                "undecodable-text",
                HIGH if bad > 10 else MEDIUM,
                f"{bad} undecodable character(s); the font lacks a usable character map",
            )
        )

    return risk


def scan(path: str) -> PdfScan:
    """Inspect a PDF page by page and report what conversion would put at risk."""
    pdfplumber = _require("pdfplumber")
    with pdfplumber.open(path) as pdf:
        pages = [_scan_page(page, index) for index, page in enumerate(pdf.pages, start=1)]
        return PdfScan(path=path, page_count=len(pdf.pages), pages=pages)


# --------------------------------------------------------------------------
# Reading (PDF -> IR)
# --------------------------------------------------------------------------

CID = re.compile(r"\(cid:\d+\)")
BULLET_MARKER = re.compile(r"^\s*(?:\(cid:\d+\)|[•·▪◦‣∙\-–—*])\s+")
ORDERED_MARKER = re.compile(r"^\s*\(?(\d{1,3})[.)]\s+")
MONO_FONT = re.compile(r"mono|courier|consol", re.IGNORECASE)
BOLD_FONT = re.compile(r"bold|black|heavy|semib", re.IGNORECASE)
PAGE_NUMBER = re.compile(r"^\s*(page\s+)?[ivxlcdm\d]+\s*(/\s*\d+)?\s*$", re.IGNORECASE)

LINE_TOLERANCE = 3.0
HEADING_RATIO = 1.12
MARGIN_FRACTION = 0.15


@dataclass
class _Line:
    text: str
    top: float
    size: float
    font: str
    page: int
    x0: float = 0.0
    bottom: float = 0.0

    @property
    def bold(self) -> bool:
        return bool(BOLD_FONT.search(self.font))

    @property
    def mono(self) -> bool:
        return bool(MONO_FONT.search(self.font))


def _page_lines(page, index: int) -> list[_Line]:
    try:
        words = page.extract_words(extra_attrs=["size", "fontname"]) or []
    except Exception:  # noqa: BLE001
        return []

    grouped: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if grouped and abs(word["top"] - grouped[-1][0]["top"]) <= LINE_TOLERANCE:
            grouped[-1].append(word)
        else:
            grouped.append([word])

    lines: list[_Line] = []
    for group in grouped:
        group.sort(key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in group).strip()
        if not text:
            continue
        sizes = [float(w.get("size") or 0) for w in group if w.get("size")]
        fonts = [str(w.get("fontname") or "") for w in group]
        lines.append(
            _Line(
                text=text,
                top=float(group[0]["top"]),
                size=statistics.median(sizes) if sizes else 0.0,
                font=collections.Counter(fonts).most_common(1)[0][0] if fonts else "",
                page=index,
                x0=float(min(w["x0"] for w in group)),
                bottom=float(max(w.get("bottom", w["top"]) for w in group)),
            )
        )
    return lines


def _table_block(data: list[list]) -> Table | None:
    """Turn an extracted grid into a Table block, or None if it is not one."""
    rows = [[(cell or "").strip() for cell in row] for row in data if row]
    rows = [row for row in rows if any(row)]
    if len(rows) < 2:
        return None
    width = max(len(row) for row in rows)
    if width < 2:
        return None
    padded = [row + [""] * (width - len(row)) for row in rows]
    return Table(
        header=[[Run(cell)] for cell in padded[0]],
        rows=[[[Run(cell)] for cell in row] for row in padded[1:]],
        alignments=[None] * width,
    )


def _inside(line: _Line, boxes: list[tuple[float, float, float, float]]) -> bool:
    for x0, top, x1, bottom in boxes:
        if top - 2 <= line.top <= bottom + 2 and x0 - 2 <= line.x0 <= x1 + 2:
            return True
    return False


def _page_content(page, index: int) -> list:
    """Lines and tables for one page, in vertical order.

    Tables are pulled out first and their text excluded from the line flow --
    otherwise table cells extract as loose words and get absorbed into whatever
    paragraph or list item happens to precede them.
    """
    try:
        found = page.find_tables() or []
    except Exception:  # noqa: BLE001
        found = []

    items: list[tuple[float, object]] = []
    boxes: list[tuple[float, float, float, float]] = []
    for table in found:
        try:
            data = table.extract()
        except Exception:  # noqa: BLE001
            continue
        block = _table_block(data)
        if block is None:
            continue
        boxes.append(tuple(table.bbox))
        items.append((float(table.bbox[1]), block))

    for line in _page_lines(page, index):
        if not _inside(line, boxes):
            items.append((line.top, line))

    items.sort(key=lambda pair: pair[0])
    return [item for _, item in items]


def _strip_running_headers(pages: list[list[_Line]], heights: list[float]) -> list[list[_Line]]:
    """Drop text repeated in the top or bottom margin of most pages.

    Running headers, footers, and page numbers are page furniture, not content.
    Carried into the IR they would appear as stray paragraphs mid-document.
    """
    if len(pages) < 3:
        return pages

    margin_texts: collections.Counter[str] = collections.Counter()
    for lines, height in zip(pages, heights, strict=True):
        if not height:
            continue
        band = height * MARGIN_FRACTION
        for line in lines:
            if not isinstance(line, _Line):
                continue
            if line.top <= band or line.top >= height - band:
                margin_texts[line.text.strip()] += 1

    threshold = max(2, len(pages) // 2)
    repeated = {text for text, count in margin_texts.items() if count >= threshold}

    cleaned: list[list[_Line]] = []
    for lines, height in zip(pages, heights, strict=True):
        band = height * MARGIN_FRACTION if height else 0
        kept = []
        for line in lines:
            if not isinstance(line, _Line):
                kept.append(line)
                continue
            in_margin = height and (line.top <= band or line.top >= height - band)
            if in_margin and (line.text.strip() in repeated or PAGE_NUMBER.match(line.text)):
                continue
            kept.append(line)
        cleaned.append(kept)
    return cleaned


def _heading_levels(lines: list) -> tuple[float, dict[float, int]]:
    """Body text size, and a map from heading size to heading level."""
    sizes = [round(line.size, 1) for line in lines if isinstance(line, _Line) and line.size]
    if not sizes:
        return 0.0, {}

    body = collections.Counter(sizes).most_common(1)[0][0]
    bigger = sorted({s for s in sizes if s >= body * HEADING_RATIO}, reverse=True)
    return body, {size: level for level, size in enumerate(bigger[:6], start=1)}


def _runs(text: str, bold: bool = False, mono: bool = False) -> list[Run]:
    return [Run(text, bold=bold, code=mono)]


def _dehyphenate(first: str, second: str) -> str | None:
    """Join a word split across lines, or return None if it was not split."""
    if first.endswith("-") and not first.endswith("--") and second[:1].islower():
        return first[:-1] + second
    return None


def _lines_to_blocks(lines: list, body_size: float, levels: dict[float, int]) -> list[Block]:
    blocks: list[Block] = []
    paragraph: list[str] = []
    list_items: list[ListItem] = []
    list_ordered = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(Paragraph(runs=_runs(" ".join(paragraph))))
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append(ListBlock(ordered=list_ordered, items=list_items))
            list_items = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            blocks.append(CodeBlock(code="\n".join(code_lines)))
            code_lines = []

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_code()

    previous_bottom: float | None = None

    for line in lines:
        # Tables arrive pre-built; they end whatever block was accumulating.
        if isinstance(line, Block):
            flush_all()
            blocks.append(line)
            previous_bottom = None
            continue

        text = CID.sub("", line.text).strip() if not BULLET_MARKER.match(line.text) else line.text
        text = text.strip()
        if not text:
            continue

        # A wide vertical gap ends the current block, so an unmarked line far
        # below a bullet is not swallowed into it.
        gap = None if previous_bottom is None else line.top - previous_bottom
        if gap is not None and body_size and gap > body_size * 1.6:
            flush_all()
        previous_bottom = line.bottom or line.top

        size = round(line.size, 1)
        level = levels.get(size)
        is_heading = level is not None or (
            line.bold and body_size and size >= body_size and len(text) < 80
        )

        if is_heading:
            flush_all()
            blocks.append(
                Heading(level=level or min(6, len(levels) + 1), runs=_runs(text))
            )
            continue

        if line.mono:
            flush_paragraph()
            flush_list()
            code_lines.append(text)
            continue
        flush_code()

        bullet = BULLET_MARKER.match(text)
        ordered = ORDERED_MARKER.match(text)
        marker = bullet or ordered
        if marker is not None:
            flush_paragraph()
            if list_items and list_ordered != bool(ordered):
                flush_list()
            list_ordered = bool(ordered)
            body = CID.sub("", text[marker.end():]).strip()
            list_items.append(ListItem(runs=_runs(body)))
            continue

        if list_items:
            # An unmarked line right after a bullet continues that bullet.
            previous = list_items[-1]
            existing = plain_text(previous.runs)
            joined = _dehyphenate(existing, text)
            previous.runs = _runs(joined if joined else f"{existing} {text}")
            continue

        if paragraph:
            joined = _dehyphenate(paragraph[-1], text)
            if joined:
                paragraph[-1] = joined
                continue
        paragraph.append(text)

    flush_all()
    return blocks


def read_path(path: str) -> Document:
    """Extract a PDF into the IR.

    Lossy by nature -- see `scan()` for what a given file would lose. Figures,
    exact layout, and multi-column reading order do not survive.
    """
    pdfplumber = _require("pdfplumber")

    with pdfplumber.open(path) as pdf:
        per_page = [_page_content(page, index) for index, page in enumerate(pdf.pages, start=1)]
        heights = [float(page.height or 0) for page in pdf.pages]
        meta = dict(pdf.metadata or {})
        page_count = len(pdf.pages)

    per_page = _strip_running_headers(per_page, heights)
    lines = [line for page in per_page for line in page]

    body_size, levels = _heading_levels(lines)
    blocks = _lines_to_blocks(lines, body_size, levels)

    return Document(
        blocks=blocks,
        meta={
            "source_format": "pdf",
            "page_count": page_count,
            "title": (meta.get("Title") or "").strip(),
        },
    )


# --------------------------------------------------------------------------
# Writing (IR -> PDF)
# --------------------------------------------------------------------------

XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}

# Categories a template's font stack can fall back to when nothing resolves.
SANS_HINT = re.compile(r"sans", re.IGNORECASE)
SERIF_HINT = re.compile(r"serif|georgia|times|garamond|book|minion|cambria", re.IGNORECASE)
MONO_HINT = re.compile(r"mono|courier|consol|menlo", re.IGNORECASE)


def _category(family: str) -> str:
    """Which base-14 category a font stack resembles, for the last-resort path."""
    family = family or ""
    if MONO_HINT.search(family):
        return "mono"
    # "sans-serif" contains "serif", so sans has to be ruled out first.
    if SANS_HINT.search(family):
        return "sans"
    if SERIF_HINT.search(family):
        return "serif"
    return "sans"


def _stack(value: str) -> list[str]:
    """Split a CSS-style font stack into candidate family names."""
    return [part.strip().strip("'\"").strip() for part in (value or "").split(",") if part.strip()]


@dataclass
class FontContext:
    """The fonts one write will use, and what went wrong assembling them."""

    body: list[FontOption] = field(default_factory=list)
    heading: list[FontOption] = field(default_factory=list)
    mono: list[FontOption] = field(default_factory=list)
    # Characters no font in any chain could render.
    missing: set[str] = field(default_factory=set)
    # Families a template asked for that this machine does not have.
    unavailable: list[str] = field(default_factory=list)
    # Families used without the exact weight or slant requested.
    degraded: set[str] = field(default_factory=set)
    # Families available in a chain that did not come from the pinned cache.
    unpinned: set[str] = field(default_factory=set)
    # Families a span was actually set in. A fallback that never rendered
    # anything does not make the output machine-dependent.
    used: set[str] = field(default_factory=set)
    # Set when embedding a CJK font was requested but none is installed.
    embed_cjk_unavailable: bool = False

    def primary(self, role: str) -> str:
        chain = getattr(self, role) or self.body
        return chain[0].name if chain else "Helvetica"


def _record_pinning(available, chain: list[FontOption], context: FontContext) -> None:
    """Note any family that came from the machine rather than the pinned set."""
    for option in chain:
        if option.is_cid or option.use_tags:
            continue
        family = available.get(option.family)
        if family is not None and not family.pinned:
            context.unpinned.add(family.name)


def _role_chain(available, spec: dict, fallback: list[str], cid: str | None,
                context: FontContext, embed_cjk: bool = False) -> list[FontOption]:
    families = _stack(spec.get("family", ""))
    chain, unresolved = build_chain(
        available, families, fallback, cid, available.catalog.cid_fonts, embed_cjk
    )

    if not chain:
        context.unavailable.extend(families or ["(none specified)"])
        return [base14_option(_category(spec.get("family", "")))]

    # A font stack is a list of alternatives, so a missing entry is only worth
    # reporting when *none* of them resolved and the chain fell through to the
    # catalogue's own fallbacks. Reporting every absent alternative would flag
    # "Georgia, Times New Roman, serif" three times on a machine that has none
    # of them but renders the document perfectly well in Liberation Serif.
    if families and all(name in unresolved for name in families):
        context.unavailable.extend(families)
    return chain


def build_font_context(template: Template) -> FontContext:
    """Resolve a template's fonts against the pool available on this machine.

    Never raises: with no usable fonts it falls back to the base-14, whose
    coverage is capped at Latin-1 so anything beyond gets reported instead of
    silently mis-rendered.
    """
    context = FontContext()
    fonts = template.fonts or {}
    fallback = [str(name) for name in (fonts.get("fallback") or [])]
    cid = fonts.get("cjk") or None
    embed_cjk = bool(fonts.get("embed_cjk"))

    try:
        available = pool()
    except Exception:  # noqa: BLE001 - a font-discovery failure must not stop a write
        available = None

    for role in ("body", "heading", "mono"):
        spec = fonts.get(role) or {}
        if available is None or not available.families:
            chain = [base14_option(_category(spec.get("family", "")))]
        else:
            chain = _role_chain(available, spec, fallback, cid, context, embed_cjk)
            _record_pinning(available, chain, context)
        setattr(context, role, chain)

    if embed_cjk and available is not None:
        from docfix.fonts.coverage import embeddable_cjk

        context.embed_cjk_unavailable = not embeddable_cjk(available)

    # Deduplicate while keeping the order the template implied.
    context.unavailable = list(dict.fromkeys(context.unavailable))
    return context


def _escape(text: str) -> str:
    return "".join(XML_ESCAPES.get(char, char) for char in text)


def _span_markup(text: str, chain: list[FontOption], context: FontContext,
                 bold: bool = False, italic: bool = False) -> str:
    """Escape text and wrap each span in the font that can actually render it."""
    spans, missing = resolve_spans(text, chain)
    context.missing |= missing

    parts: list[str] = []
    for chunk, option in spans:
        if not option.is_cid and not option.use_tags:
            context.used.add(option.family)
        escaped = _escape(chunk)
        if option.use_tags:
            # base-14: reportlab maps <b>/<i> to a built-in face itself.
            if bold:
                escaped = f"<b>{escaped}</b>"
            if italic:
                escaped = f"<i>{escaped}</i>"
            parts.append(f'<font face="{option.name}">{escaped}</font>')
            continue

        face, exact = option.name_for(bold, italic)
        if not exact:
            context.degraded.add(option.family)
        parts.append(f'<font face="{face}">{escaped}</font>')
    return "".join(parts)


def _markup(runs: list[Run], context: FontContext, role: str = "body") -> str:
    """Render runs as the limited inline markup reportlab understands."""
    chain = getattr(context, role) or context.body
    parts: list[str] = []

    for run in runs:
        if run.raw:
            # Raw runs carry Markdown syntax (inline images); show the alt text.
            match = re.match(r"!\[(.*?)\]\((.*?)\)", run.text)
            shown = (match.group(1) or match.group(2)) if match else run.text
            parts.append(_span_markup(shown, chain, context))
            continue

        if run.code:
            text = _span_markup(run.text, context.mono or chain, context, run.bold, run.italic)
        else:
            text = _span_markup(run.text, chain, context, run.bold, run.italic)

        if run.strike:
            text = f"<strike>{text}</strike>"
        if run.link:
            text = f'<a href="{_escape(run.link)}" color="blue">{text}</a>'
        parts.append(text)
    return "".join(parts)


# These are produced by coverage_issues rather than by a decorated rule, but
# Config.apply filters them like any other, so they belong in `docfix rules`.
COVERAGE_RULE_IDS = (
    ("font-coverage", "Characters no available font can render."),
    ("font-unavailable", "None of the template's fonts are installed here."),
    ("font-embed-cjk-unavailable", "--embed-cjk asked for, no suitable font installed."),
    ("font-style-missing", "A family with no bold or italic face."),
    ("font-not-pinned", "Output used a font that is not from the pinned set."),
)


def coverage_issues(doc: Document, template: Template) -> list[Issue]:
    """Report what this machine's fonts cannot render for this template.

    Runs the same resolution the writer will, so the report matches the output.
    """
    context = build_font_context(template)
    for block in walk(doc):
        for run in iter_runs(block):
            _markup([run], context, "body")

    issues: list[Issue] = []
    if context.missing:
        shown = "".join(sorted(context.missing)[:20])
        issues.append(
            Issue(
                "font-coverage",
                f"{len(context.missing)} character(s) cannot be rendered by any "
                f"available font and will be missing from the PDF: {shown}",
                ERROR,
            )
        )
    if context.unavailable:
        issues.append(
            Issue(
                "font-unavailable",
                "none of the template's fonts are installed here ("
                + ", ".join(context.unavailable)
                + "); a substitute is used, so the output will not look as intended",
                WARNING,
            )
        )
    if context.embed_cjk_unavailable:
        issues.append(
            Issue(
                "font-embed-cjk-unavailable",
                "embedding a CJK font was requested but no installed font with an "
                "open licence covers CJK; falling back to the built-in CID "
                "collections, so the PDF will rely on the reader's own fonts",
                WARNING,
            )
        )
    machine_fonts = context.unpinned & context.used
    if machine_fonts:
        issues.append(
            Issue(
                "font-not-pinned",
                "output used font(s) installed on this machine rather than the "
                "pinned set, so it may render differently elsewhere: "
                + ", ".join(sorted(machine_fonts))
                + "; run `docfix fonts install` for reproducible output",
                INFO,
            )
        )
    if context.degraded:
        issues.append(
            Issue(
                "font-style-missing",
                "no bold or italic face for: " + ", ".join(sorted(context.degraded))
                + "; the regular face is used instead",
                INFO,
            )
        )
    return issues


def _styles(template: Template, context: FontContext):
    """Build the reportlab paragraph styles a template describes."""
    _require("reportlab")
    from reportlab.lib import colors  # type: ignore[import-untyped]
    from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped]

    fonts = template.fonts or {}
    spacing = template.spacing or {}
    palette = template.colors or {}

    body_font = context.primary("body")
    head_font = context.primary("heading")
    mono_font = context.primary("mono")

    body_size = float((fonts.get("body") or {}).get("size", 11))
    head_size = float((fonts.get("heading") or {}).get("size", 16))
    mono_size = float((fonts.get("mono") or {}).get("size", 9))

    line_height = float(spacing.get("line_height", 1.4))
    after = float(spacing.get("paragraph_after", 10))
    head_before = float(spacing.get("heading_before", 16))
    head_after = float(spacing.get("heading_after", 6))

    def color(key: str, fallback: str):
        try:
            return colors.HexColor(palette.get(key, fallback))
        except (ValueError, AttributeError):
            return colors.HexColor(fallback)

    text_color = color("text", "#000000")
    heading_color = color("heading", "#000000")
    muted = color("muted", "#666666")

    built = {
        "body": ParagraphStyle(
            "body",
            fontName=body_font,
            fontSize=body_size,
            leading=body_size * line_height,
            spaceAfter=after,
            textColor=text_color,
        ),
        "code": ParagraphStyle(
            "code",
            fontName=mono_font,
            fontSize=mono_size,
            leading=mono_size * 1.3,
            spaceAfter=after,
            leftIndent=12,
            textColor=text_color,
        ),
        "quote": ParagraphStyle(
            "quote",
            fontName=body_font,
            fontSize=body_size,
            leading=body_size * line_height,
            spaceAfter=after,
            leftIndent=18,
            textColor=muted,
        ),
        "item": ParagraphStyle(
            "item",
            fontName=body_font,
            fontSize=body_size,
            leading=body_size * line_height,
            spaceAfter=after / 3,
            leftIndent=16,
            bulletIndent=4,
            # reportlab defaults bulletFontName to Helvetica and bulletFontSize
            # to 10, independently of fontName/fontSize. Left alone, every
            # bullet and list number draws in a base-14 font at the wrong size
            # while its text uses the document's font -- visibly mismatched,
            # and not reproducible, since Helvetica is never embedded.
            bulletFontName=body_font,
            bulletFontSize=body_size,
            textColor=text_color,
        ),
    }

    for level in range(1, 7):
        # Each level steps down toward body size, never below it.
        size = max(body_size + 1, head_size - (level - 1) * 1.8)
        built[f"h{level}"] = ParagraphStyle(
            f"h{level}",
            fontName=head_font,
            fontSize=size,
            leading=size * 1.25,
            spaceBefore=head_before if level <= 2 else head_before * 0.7,
            spaceAfter=head_after,
            textColor=heading_color,
        )
    return built


def _flowables(blocks: list[Block], styles, template: Template, context: FontContext) -> list:
    from reportlab.lib import colors  # type: ignore[import-untyped]
    from reportlab.platypus import (  # type: ignore[import-untyped]
        HRFlowable,
        Preformatted,
        Spacer,
        TableStyle,
    )
    from reportlab.platypus import Image as RLImage  # type: ignore[import-untyped]
    from reportlab.platypus import Paragraph as RLParagraph  # type: ignore[import-untyped]
    from reportlab.platypus import Table as RLTable  # type: ignore[import-untyped]

    bullet = template.markdown.bullet_marker or "-"
    out: list = []

    for block in blocks:
        if isinstance(block, Heading):
            level = max(1, min(6, block.level))
            out.append(RLParagraph(_markup(block.runs, context, "heading"), styles[f"h{level}"]))

        elif isinstance(block, Paragraph):
            out.append(RLParagraph(_markup(block.runs, context, "body"), styles["body"]))

        elif isinstance(block, ListBlock):
            for index, item in enumerate(block.items):
                marker = f"{block.start + index}." if block.ordered else bullet
                out.append(
                    RLParagraph(_markup(item.runs, context), styles["item"], bulletText=marker)
                )
                if item.blocks:
                    out.extend(_flowables(item.blocks, styles, template, context))
            out.append(Spacer(1, 4))

        elif isinstance(block, CodeBlock):
            out.append(Preformatted(block.code, styles["code"]))

        elif isinstance(block, BlockQuote):
            out.extend(_flowables(block.blocks, styles, template, context))

        elif isinstance(block, Table):
            data = [[_markup(cell, context) for cell in block.header]] if block.header else []
            data += [[_markup(cell, context) for cell in row] for row in block.rows]
            if not data:
                continue
            wrapped = [[RLParagraph(cell, styles["body"]) for cell in row] for row in data]
            table = RLTable(wrapped, hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            out.append(table)
            out.append(Spacer(1, 8))

        elif isinstance(block, Image):
            if block.src and os.path.exists(block.src):
                try:
                    out.append(RLImage(block.src, width=380, height=None, kind="proportional"))
                except Exception:  # noqa: BLE001 - an unreadable image is not fatal
                    out.append(RLParagraph(_markup([Run(f"[image: {block.alt or block.src}]")], context),
                                           styles["quote"]))
            else:
                label = block.alt or block.src or "image"
                out.append(RLParagraph(_escape(f"[image: {label}]"), styles["quote"]))

        elif isinstance(block, ThematicBreak):
            out.append(Spacer(1, 6))
            out.append(HRFlowable(width="100%", thickness=0.6, color=colors.grey))
            out.append(Spacer(1, 6))

    return out


def write_path(doc: Document, template: Template, path: str) -> None:
    """Generate a new PDF from the IR."""
    _require("reportlab")
    from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
    from reportlab.lib.units import mm  # type: ignore[import-untyped]
    from reportlab.platypus import SimpleDocTemplate  # type: ignore[import-untyped]

    context = build_font_context(template)
    styles = _styles(template, context)
    story = _flowables(doc.blocks, styles, template, context)

    document = SimpleDocTemplate(
        path,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=(doc.meta or {}).get("title") or os.path.basename(path),
    )
    document.build(story)


def source_text(path: str) -> None:
    """PDF has no raw text source, so the source-level rules do not apply."""
    return None
