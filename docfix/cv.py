"""The CV/résumé layer: recognising a CV, and the conventions that apply to one.

A CV is a *template category*, not a file format. It arrives as Markdown, DOCX
or PDF like anything else, so this adds no adapter -- it adds a structure model
(sections and dated entries) and a family of rules that only make sense for a
résumé.

Every rule here is **report-only**. Reverse-chronological order, first-person
phrasing and weak openers are matters of convention and judgement; rewriting
them would change what the document says, which the project does not do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from docfix.detect.rules import INFO, WARNING, Issue
from docfix.ir import (
    Block,
    Heading,
    ListBlock,
    Paragraph,
    iter_runs,
    plain_text,
    walk,
)

# Section names a CV is expected to use, grouped by what they mean. Matching is
# loose because people phrase them differently ("Work History", "Employment").
SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "experience": ("experience", "employment", "work history", "career", "positions"),
    "education": ("education", "academic", "qualifications", "degrees"),
    "skills": ("skills", "technical skills", "competencies", "technologies", "expertise"),
    "projects": ("projects", "portfolio", "selected work"),
    "summary": ("summary", "profile", "objective", "about", "personal statement"),
    "publications": ("publications", "papers", "research"),
    "awards": ("awards", "honours", "honors", "achievements"),
    "contact": ("contact", "details", "personal details"),
    "languages": ("languages",),
    "references": ("references", "referees"),
}

MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
    "january|february|march|april|june|july|august|september|october|november|december"
)
ONGOING = re.compile(r"\b(present|current|now|ongoing|to date)\b", re.IGNORECASE)
YEAR = re.compile(r"\b(19|20)\d{2}\b")
DATE_RANGE = re.compile(
    rf"(?:(?:{MONTHS})[a-z]*\.?\s+)?((?:19|20)\d{{2}})"
    rf"\s*(?:[-–—]|to|until)\s*"
    rf"(?:(?:{MONTHS})[a-z]*\.?\s+)?((?:19|20)\d{{2}}|present|current|now|ongoing)",
    re.IGNORECASE,
)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}")
URL = re.compile(r"https?://|www\.|\b(?:linkedin|github|gitlab)\b", re.IGNORECASE)

FIRST_PERSON = re.compile(r"\b(I|I'm|I've|my|me|myself)\b")
WEAK_OPENERS = (
    "responsible for",
    "duties included",
    "tasked with",
    "worked on",
    "helped with",
    "involved in",
    "assisted with",
    "in charge of",
)

# A bullet longer than this reads as a paragraph and stops being scannable.
LONG_BULLET = 220


@dataclass
class Entry:
    """One dated item within a section -- a role, a degree, a project."""

    text: str
    start: int | None = None
    end: int | None = None
    ongoing: bool = False

    @property
    def sort_key(self) -> tuple[int, int]:
        """Recency, for checking reverse-chronological order.

        An ongoing entry outranks any finished one, so it sorts above the
        highest real year rather than being treated as undated.
        """
        end = 9999 if self.ongoing else (self.end or self.start or 0)
        return (end, self.start or 0)


@dataclass
class Section:
    """A CV section: its heading, what kind it is, and the blocks under it."""

    title: str
    kind: str | None
    level: int
    blocks: list[Block] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)


def classify(title: str) -> str | None:
    """Which kind of CV section a heading names, if any."""
    lowered = title.strip().lower().strip(":")
    for kind, aliases in SECTION_ALIASES.items():
        if any(alias == lowered or alias in lowered for alias in aliases):
            return kind
    return None


def date_range(text: str) -> tuple[int | None, int | None, bool]:
    """(start year, end year, ongoing) parsed from a line of a CV."""
    match = DATE_RANGE.search(text)
    if match:
        start = int(match.group(1))
        tail = match.group(2)
        if tail.isdigit():
            return start, int(tail), False
        return start, None, True

    years = [int(m.group(0)) for m in YEAR.finditer(text)]
    if years:
        return min(years), max(years), bool(ONGOING.search(text))
    if ONGOING.search(text):
        return None, None, True
    return None, None, False


def _section_level(blocks: list[Block]) -> int:
    """The heading level a CV uses for its sections.

    Usually the shallowest level below the name at the top, so h2 in a document
    titled with an h1. Falling back to the shallowest present handles CVs that
    skip the title.
    """
    levels = sorted({b.level for b in blocks if isinstance(b, Heading)})
    if not levels:
        return 0
    return levels[1] if len(levels) > 1 and levels[0] == 1 else levels[0]


def sections(doc) -> list[Section]:
    """Split a document into its CV sections, in order."""
    level = _section_level(doc.blocks)
    if not level:
        return []

    found: list[Section] = []
    current: Section | None = None
    for block in doc.blocks:
        if isinstance(block, Heading) and block.level == level:
            title = plain_text(block.runs).strip()
            current = Section(title=title, kind=classify(title), level=level)
            found.append(current)
            continue
        if current is not None:
            current.blocks.append(block)

    for section in found:
        section.entries = _entries(section)
    return found


def _entries(section: Section) -> list[Entry]:
    """Dated items in a section: sub-headings first, else dated paragraphs."""
    entries: list[Entry] = []

    subheadings = [b for b in section.blocks if isinstance(b, Heading)]
    if subheadings:
        for heading in subheadings:
            text = plain_text(heading.runs)
            # A role's dates often sit in the paragraph under its heading.
            index = section.blocks.index(heading)
            for following in section.blocks[index + 1 : index + 3]:
                if isinstance(following, Paragraph):
                    text = f"{text} {plain_text(following.runs)}"
                    break
            start, end, ongoing = date_range(text)
            if start or ongoing:
                entries.append(Entry(text.strip(), start, end, ongoing))
        return entries

    for block in section.blocks:
        if not isinstance(block, Paragraph):
            continue
        text = plain_text(block.runs)
        start, end, ongoing = date_range(text)
        if start or ongoing:
            entries.append(Entry(text.strip(), start, end, ongoing))
    return entries


def _bullets(doc) -> list[str]:
    out: list[str] = []
    for block in walk(doc):
        if isinstance(block, ListBlock):
            out.extend(plain_text(item.runs).strip() for item in block.items)
    return [text for text in out if text]


def looks_like_cv(doc) -> bool:
    """Whether a document is confidently a CV.

    Deliberately conservative: two or more recognised sections, one of which is
    experience or education. A single "Skills" heading in a README is not a CV,
    and running résumé rules over one would be noise.
    """
    kinds = {section.kind for section in sections(doc) if section.kind}
    if len(kinds) < 2:
        return False
    return bool(kinds & {"experience", "education"})


# --------------------------------------------------------------------------
# Rules -- all report-only
# --------------------------------------------------------------------------


def check_required_sections(doc) -> list[Issue]:
    kinds = {section.kind for section in sections(doc) if section.kind}
    if not kinds & {"experience", "education"}:
        return [
            Issue(
                "cv-missing-section",
                "no Experience or Education section found; a CV is normally "
                "expected to have at least one",
                WARNING,
            )
        ]
    return []


def check_contact_details(doc) -> list[Issue]:
    """Contact details belong near the top, before the first section."""
    level = _section_level(doc.blocks)
    header: list[Block] = []
    for block in doc.blocks:
        if isinstance(block, Heading) and block.level == level and header:
            break
        header.append(block)

    text = " ".join(
        run.text for block in header for run in iter_runs(block)
    )
    links = [run.link for block in header for run in iter_runs(block) if run.link]

    if EMAIL.search(text) or URL.search(text) or any(links):
        return []
    if PHONE.search(text):
        return []
    return [
        Issue(
            "cv-missing-contact",
            "no email address, phone number or link found near the top; "
            "a reader has no way to make contact",
            WARNING,
        )
    ]


def check_reverse_chronological(doc) -> list[Issue]:
    """Dated entries should run most-recent first."""
    issues: list[Issue] = []
    for section in sections(doc):
        if section.kind not in ("experience", "education", "projects"):
            continue
        dated = [entry for entry in section.entries if entry.start or entry.ongoing]
        if len(dated) < 2:
            continue

        keys = [entry.sort_key for entry in dated]
        if keys != sorted(keys, reverse=True):
            issues.append(
                Issue(
                    "cv-not-reverse-chronological",
                    f"entries under {section.title!r} are not in reverse-"
                    "chronological order; a CV normally lists the most recent first",
                    WARNING,
                )
            )
    return issues


def check_bullet_punctuation(doc) -> list[Issue]:
    """Bullets should either all end with a full stop, or none should."""
    bullets = _bullets(doc)
    if len(bullets) < 3:
        return []

    with_stop = sum(1 for text in bullets if text.endswith("."))
    without = len(bullets) - with_stop
    if with_stop and without:
        return [
            Issue(
                "cv-bullet-punctuation",
                f"{with_stop} bullet(s) end with a full stop and {without} do not; "
                "pick one and use it throughout",
                INFO,
            )
        ]
    return []


def check_first_person(doc) -> list[Issue]:
    offenders = [text for text in _bullets(doc) if FIRST_PERSON.search(text)]
    if not offenders:
        return []
    return [
        Issue(
            "cv-first-person",
            f"{len(offenders)} bullet(s) use first-person pronouns "
            f"(e.g. {offenders[0][:60]!r}); a CV conventionally omits them",
            INFO,
        )
    ]


def check_weak_openers(doc) -> list[Issue]:
    """Bullets should open with what was achieved, not what was assigned."""
    offenders = []
    for text in _bullets(doc):
        lowered = text.lower()
        for phrase in WEAK_OPENERS:
            if lowered.startswith(phrase):
                offenders.append((phrase, text))
                break
    if not offenders:
        return []
    phrases = sorted({phrase for phrase, _ in offenders})
    return [
        Issue(
            "cv-weak-opener",
            f"{len(offenders)} bullet(s) open with a passive phrase "
            f"({', '.join(repr(p) for p in phrases[:3])}); leading with an action "
            "verb reads stronger",
            INFO,
        )
    ]


def check_bullet_length(doc) -> list[Issue]:
    long_ones = [text for text in _bullets(doc) if len(text) > LONG_BULLET]
    if not long_ones:
        return []
    return [
        Issue(
            "cv-bullet-too-long",
            f"{len(long_ones)} bullet(s) run past {LONG_BULLET} characters "
            "(longest is "
            f"{max(len(t) for t in long_ones)}); long bullets stop being scannable",
            INFO,
        )
    ]


def check_section_order(doc) -> list[Issue]:
    """A summary, if present, belongs before experience rather than after."""
    order = [section.kind for section in sections(doc) if section.kind]
    if (
        "summary" in order
        and "experience" in order
        and order.index("summary") > order.index("experience")
    ):
        return [
                Issue(
                    "cv-summary-placement",
                    "the summary appears after the experience section; it is "
                "normally the first thing a reader sees",
                INFO,
            )
        ]
    return []


CV_RULES = (
    check_required_sections,
    check_contact_details,
    check_reverse_chronological,
    check_bullet_punctuation,
    check_first_person,
    check_weak_openers,
    check_bullet_length,
    check_section_order,
)


def check(doc) -> list[Issue]:
    """Run every CV rule, sorted by rule name."""
    issues: list[Issue] = []
    for rule in CV_RULES:
        issues.extend(rule(doc))
    return sorted(issues, key=lambda issue: issue.rule)


__all__ = [
    "CV_RULES",
    "Entry",
    "Section",
    "check",
    "classify",
    "date_range",
    "looks_like_cv",
    "sections",
]
