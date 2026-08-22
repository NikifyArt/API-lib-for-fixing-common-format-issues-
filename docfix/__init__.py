"""docfix -- find and fix common formatting problems in documents.

Prettier, but for documents. Reads a file into a shared intermediate
representation, reports the formatting problems it finds, repairs the safe
ones, applies a named template, and writes the result to a **new file**. The
source is never modified.

    >>> import docfix
    >>> result = docfix.format_file("notes.md", template="formal")
    >>> result.output_path
    'notes.formatted.md'
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from docfix import adapters
from docfix.detect.rules import Issue, run_all
from docfix.fix.normalize import normalize
from docfix.ir import Document
from docfix.templates import Template, TemplateError, list_presets, load

__version__ = "0.1.0"

DEFAULT_TEMPLATE = "minimal"
OUTPUT_INFIX = "formatted"


@dataclass
class Result:
    """What a format run found and produced."""

    source_path: str
    output_path: str | None
    template: str
    issues: list[Issue] = field(default_factory=list)
    # Set when the Markdown intermediate was kept; see format_file().
    intermediate_path: str | None = None

    @property
    def fixed(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.auto_fixable]

    @property
    def remaining(self) -> list[Issue]:
        return [issue for issue in self.issues if not issue.auto_fixable]

    def __str__(self) -> str:
        return (
            f"{self.source_path} -> {self.output_path} "
            f"[{self.template}] {len(self.fixed)} fixed, "
            f"{len(self.remaining)} to review"
        )


def default_output_path(path: str) -> str:
    """`notes.md` -> `notes.formatted.md`. Never returns the input path."""
    stem, extension = os.path.splitext(path)
    return f"{stem}.{OUTPUT_INFIX}{extension}"


def _resolve_template(template: str | Template) -> Template:
    return template if isinstance(template, Template) else load(template)


def scan(path: str):
    """Inspect a PDF page by page and report what conversion would put at risk.

    PDF only -- it is the one supported format where reading is lossy enough that
    the user should see what they are about to lose before converting.
    """
    from docfix.adapters import pdf

    if os.path.splitext(path)[1].lower() not in pdf.EXTENSIONS:
        raise adapters.UnsupportedFormatError(
            f"scan is only meaningful for PDF files, not {os.path.splitext(path)[1]!r}; "
            "use detect() to report formatting issues in any supported format"
        )
    return pdf.scan(path)


def detect(path: str) -> list[Issue]:
    """Report formatting problems without writing anything."""
    adapter = adapters.for_path(path)
    doc = adapter.read_path(path)
    source = adapter.source_text(path) if adapter.source_text else None
    return run_all(doc, source)


def format_file(
    path: str,
    template: str | Template = DEFAULT_TEMPLATE,
    output: str | None = None,
    keep_intermediate: str | bool | None = None,
) -> Result:
    """Normalize a document and write the result to a new file.

    The source file is never modified. Writing over the input is refused.

    `keep_intermediate` also writes the extracted content as Markdown, which is
    the readable form of what was understood from the source. Useful for a PDF,
    where extraction is lossy and worth eyeballing -- and hand-editable, so the
    Markdown can be corrected and re-formatted. Pass a path, or True to derive
    one (`report.pdf` -> `report.extracted.md`).
    """
    adapter = adapters.for_path(path)
    resolved = _resolve_template(template)

    doc = adapter.read_path(path)
    source = adapter.source_text(path) if adapter.source_text else None
    issues = run_all(doc, source)
    if adapter.coverage_issues:
        # What the target format cannot render, which only the template and the
        # fonts on this machine can decide.
        issues.extend(adapter.coverage_issues(doc, resolved))

    destination = output or default_output_path(path)
    if os.path.abspath(destination) == os.path.abspath(path):
        raise ValueError(
            f"refusing to overwrite the source file {path!r}; "
            "docfix always writes to a separate file"
        )

    normalized = normalize(doc)

    intermediate_path = None
    if keep_intermediate:
        from docfix.adapters import markdown as _md

        intermediate_path = (
            keep_intermediate
            if isinstance(keep_intermediate, str)
            else f"{os.path.splitext(path)[0]}.extracted.md"
        )
        with open(intermediate_path, "w", encoding="utf-8") as handle:
            handle.write(_md.write(normalized, resolved))

    adapter.write_path(normalized, resolved, destination)
    return Result(
        source_path=path,
        output_path=destination,
        template=resolved.name,
        issues=issues,
        intermediate_path=intermediate_path,
    )


def format_text(text: str, template: str | Template = DEFAULT_TEMPLATE) -> str:
    """Normalize a Markdown string and return it. Convenience for tests and pipes."""
    from docfix.adapters import markdown

    return markdown.write(normalize(markdown.read(text)), _resolve_template(template))


def list_templates() -> list[str]:
    """Names of the bundled templates."""
    return list_presets()


__all__ = [
    "DEFAULT_TEMPLATE",
    "Document",
    "Issue",
    "Result",
    "Template",
    "TemplateError",
    "__version__",
    "default_output_path",
    "detect",
    "format_file",
    "format_text",
    "list_templates",
    "load",
    "scan",
]
