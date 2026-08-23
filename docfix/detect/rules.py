"""Issue detection.

Two families of rule:

* **Structure rules** run on the IR, so they apply to every format equally.
* **Source rules** run on raw text, catching problems the parser dissolves
  (trailing whitespace, blank-line runs). The Markdown writer fixes these by
  construction, but `detect()` still reports them so a report-only run is useful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from docfix.config import DEFAULT, Config
from docfix.ir import (
    Document,
    Heading,
    Image,
    ListBlock,
    Paragraph,
    Table,
    iter_block_sequences,
    iter_runs,
    plain_text,
    walk,
)


def emits(*rule_ids: str):
    """Declare the rule ids a check can produce.

    One function often emits several -- `check_heading_levels` reports both
    `heading-skip` and `heading-multiple-h1` -- and the ids, not the function
    names, are what a config file addresses. Declaring them lets `docfix rules`
    show the right identifiers, and lets a test assert none drift.
    """

    def decorate(func):
        func.rule_ids = rule_ids
        return func

    return decorate


ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass(frozen=True)
class Issue:
    rule: str
    message: str
    severity: str = WARNING
    line: int | None = None
    auto_fixable: bool = False

    def __str__(self) -> str:
        where = f"line {self.line}" if self.line else "document"
        return f"[{self.severity}] {self.rule} ({where}): {self.message}"


# --------------------------------------------------------------------------
# Structure rules (IR)
# --------------------------------------------------------------------------


@emits("heading-multiple-h1", "heading-skip")
def check_heading_levels(doc: Document, config: Config = DEFAULT) -> list[Issue]:
    """Headings should descend one level at a time, with a single H1."""
    issues: list[Issue] = []
    headings = [b for b in walk(doc) if isinstance(b, Heading)]

    h1_count = sum(1 for h in headings if h.level == 1)
    if h1_count > 1:
        issues.append(
            Issue(
                "heading-multiple-h1",
                f"document has {h1_count} level-1 headings; exactly one is expected",
                WARNING,
            )
        )

    previous = None
    for heading in headings:
        if previous is not None and heading.level > previous + 1:
            issues.append(
                Issue(
                    "heading-skip",
                    f"heading level jumps from h{previous} to h{heading.level} "
                    f"at {plain_text(heading.runs)[:40]!r}",
                    WARNING,
                )
            )
        previous = heading.level
    return issues


@emits("heading-empty")
def check_empty_headings(doc: Document, config: Config = DEFAULT) -> list[Issue]:
    """A heading with no text renders as a gap in the outline."""
    return [
        Issue("heading-empty", "heading has no text", ERROR)
        for block in walk(doc)
        if isinstance(block, Heading) and not plain_text(block.runs).strip()
    ]


def _mixed_marker_issue(markers: set[str]) -> Issue:
    return Issue(
        "list-mixed-markers",
        f"bullet list mixes markers {sorted(markers)}; "
        "the template's marker will be used throughout",
        WARNING,
        auto_fixable=True,
    )


@emits("list-mixed-markers")
def check_list_markers(doc: Document, config: Config = DEFAULT) -> list[Issue]:
    """A bullet list should use one marker character throughout.

    Changing the marker mid-list makes CommonMark start a *new* list, so the
    problem shows up two ways depending on whether normalization has run yet:
    as several adjacent lists, or as one merged list carrying several markers.
    Both are checked so detection gives the same answer either way.
    """
    issues: list[Issue] = []
    seen: set[int] = set()

    def flush(markers: set[str], length: int) -> None:
        """A run of adjacent bullet lists means the marker changed mid-list."""
        if length > 1 and len(markers) > 1:
            issues.append(_mixed_marker_issue(markers))

    for sequence in iter_block_sequences(doc):
        run_markers: set[str] = set()
        run_length = 0

        for block in sequence:
            if isinstance(block, ListBlock) and not block.ordered:
                markers = {m for m in block.source_markers if m}
                # One merged list already carrying several markers.
                if len(markers) > 1 and id(block) not in seen:
                    seen.add(id(block))
                    issues.append(_mixed_marker_issue(markers))
                    run_markers, run_length = set(), 0
                    continue
                run_markers |= markers
                run_length += 1
            else:
                flush(run_markers, run_length)
                run_markers, run_length = set(), 0
        flush(run_markers, run_length)

    return issues


@emits("image-missing-alt")
def check_image_alt(doc: Document, config: Config = DEFAULT) -> list[Issue]:
    """An image without alt text is invisible to a screen reader."""
    issues: list[Issue] = []
    for block in walk(doc):
        if isinstance(block, Image) and not block.alt.strip():
            issues.append(
                Issue(
                    "image-missing-alt",
                    f"image {block.src!r} has no alt text",
                    WARNING,
                )
            )
    return issues


@emits("table-ragged-row")
def check_table_shape(doc: Document, config: Config = DEFAULT) -> list[Issue]:
    """Every row should have as many cells as the header."""
    issues: list[Issue] = []
    for block in walk(doc):
        if not isinstance(block, Table) or not block.header:
            continue
        width = len(block.header)
        for index, row in enumerate(block.rows, start=1):
            if len(row) != width:
                issues.append(
                    Issue(
                        "table-ragged-row",
                        f"row {index} has {len(row)} cells but the header has {width}",
                        ERROR,
                    )
                )
    return issues


DATE_STYLES = {
    "iso": re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    "slashed": re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    "month-first": re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s*\d{4}\b"
    ),
    "day-first": re.compile(
        r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b"
    ),
}


@emits("date-inconsistent")
def check_date_consistency(doc: Document, config: Config = DEFAULT) -> list[Issue]:
    """Mixed date formats read as sloppy -- and matter most on a CV."""
    text = " ".join(
        run.text for block in walk(doc) for run in iter_runs(block) if not run.code
    )
    found = sorted(style for style, pattern in DATE_STYLES.items() if pattern.search(text))
    if len(found) > 1:
        return [
            Issue(
                "date-inconsistent",
                f"document mixes date formats: {', '.join(found)}",
                WARNING,
            )
        ]
    return []


@emits("paragraph-empty")
def check_empty_paragraphs(doc: Document, config: Config = DEFAULT) -> list[Issue]:
    """Paragraphs with no content are stray spacing."""
    return [
        Issue(
            "paragraph-empty",
            "paragraph contains no text",
            INFO,
            auto_fixable=True,
        )
        for block in walk(doc)
        if isinstance(block, Paragraph) and not plain_text(block.runs).strip()
    ]


STRUCTURE_RULES = (
    check_heading_levels,
    check_empty_headings,
    check_list_markers,
    check_image_alt,
    check_table_shape,
    check_date_consistency,
    check_empty_paragraphs,
)


# --------------------------------------------------------------------------
# Source rules (raw text)
# --------------------------------------------------------------------------

CURLY_QUOTES = re.compile(r"[‘’“”]")
STRAIGHT_QUOTES = re.compile(r"[\"']")
FENCE = re.compile(r"^\s*(```|~~~)")


def _non_code_lines(text: str) -> list[tuple[int, str]]:
    """Lines outside fenced code blocks, as (1-based line number, text)."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append((number, line))
    return out


@emits("whitespace-trailing")
def check_trailing_whitespace(text: str, config: Config = DEFAULT) -> list[Issue]:
    """Spaces at end of line are invisible and meaningless."""
    return [
        Issue(
            "whitespace-trailing",
            "line has trailing whitespace",
            INFO,
            line=number,
            auto_fixable=True,
        )
        for number, line in _non_code_lines(text)
        if line != line.rstrip()
    ]


@emits("whitespace-blank-run")
def check_blank_line_runs(text: str, config: Config = DEFAULT) -> list[Issue]:
    """More than one blank line in a row carries no meaning in Markdown.

    Fenced code is scanned inline rather than filtered out first: dropping the
    fence and its body would make the blank lines on either side look adjacent
    and report a run that is not there.
    """
    issues: list[Issue] = []
    blanks = 0
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            blanks = 0
            continue
        if in_fence or line.strip():
            blanks = 0
            continue
        blanks += 1
        if blanks == 2:
            issues.append(
                Issue(
                    "whitespace-blank-run",
                    "more than one consecutive blank line",
                    INFO,
                    line=number,
                    auto_fixable=True,
                )
            )
    return issues


@emits("whitespace-tab-indent")
def check_tab_indentation(text: str, config: Config = DEFAULT) -> list[Issue]:
    """Tab-indented lines render inconsistently between viewers."""
    return [
        Issue(
            "whitespace-tab-indent",
            "line is indented with a tab; spaces are expected",
            INFO,
            line=number,
            auto_fixable=True,
        )
        for number, line in _non_code_lines(text)
        if line.startswith("\t")
    ]


@emits("quotes-mixed")
def check_quote_consistency(text: str, config: Config = DEFAULT) -> list[Issue]:
    """Mixing straight and curly quotes looks unintentional."""
    body = "\n".join(line for _, line in _non_code_lines(text))
    if CURLY_QUOTES.search(body) and STRAIGHT_QUOTES.search(body):
        return [
            Issue(
                "quotes-mixed",
                "document mixes straight and curly quotation marks",
                INFO,
            )
        ]
    return []


SOURCE_RULES = (
    check_trailing_whitespace,
    check_blank_line_runs,
    check_tab_indentation,
    check_quote_consistency,
)


def run_all(
    doc: Document, source: str | None = None, config: Config | None = None
) -> list[Issue]:
    """Run every applicable rule, sorted by line then rule name.

    `config` disables rules and overrides severities afterwards rather than
    skipping rule functions -- one function can emit several rule ids, so
    skipping it would be the wrong granularity.
    """
    config = config or DEFAULT
    issues: list[Issue] = []
    for rule in STRUCTURE_RULES:
        issues.extend(rule(doc, config))
    if source is not None:
        for source_rule in SOURCE_RULES:
            issues.extend(source_rule(source, config))
    return config.apply(sorted(issues, key=lambda i: (i.line or 0, i.rule)))
