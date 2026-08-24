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
from dataclasses import dataclass, field, replace

from docfix import adapters
from docfix.config import Config, ConfigError, discover
from docfix.detect.rules import Issue, run_all
from docfix.fix.normalize import normalize
from docfix.ir import Document
from docfix.plugins import (
    PluginError,
    load_plugins,
    plugin_errors,
    register_adapter,
    register_rule,
    unregister_adapter,
    unregister_rule,
)
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


def _resolve_template(template: str | Template | None, config: Config | None) -> Template:
    """Explicit argument wins, then the config file, then the default."""
    if template is None:
        template = (config.template if config else None) or DEFAULT_TEMPLATE
    return template if isinstance(template, Template) else load(template)


def _with_embedded_cjk(template: Template) -> Template:
    """A copy of the template that embeds a CJK font rather than relying on CID.

    Copied rather than mutated: presets are shared, and a per-call flag must not
    leak into the next caller's template.
    """
    return replace(template, fonts={**(template.fonts or {}), "embed_cjk": True})


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


def _cv_issues(doc, cv: bool | None, config: Config | None = None) -> list[Issue]:
    """CV rules, when the document is one.

    `cv=None` auto-detects, conservatively: two or more recognised sections,
    one of them experience or education. `True` forces the rules on, `False`
    off. Every rule is report-only -- reverse-chronological order and phrasing
    are matters of judgement, and rewriting them would change what the document
    says.
    """
    if cv is False:
        return []
    from docfix import cv as cv_layer

    if cv is None and not cv_layer.looks_like_cv(doc):
        return []
    return cv_layer.check(doc, config)


def detect(
    path: str, cv: bool | None = None, config: Config | None = None
) -> list[Issue]:
    """Report formatting problems without writing anything.

    `cv` controls the résumé rules: None auto-detects, True forces them on,
    False off. `config` disables rules, overrides severities, and supplies
    per-rule options.
    """
    adapter = adapters.for_path(path)
    doc = adapter.read_path(path)
    source = adapter.source_text(path) if adapter.source_text else None
    if cv is None and config is not None:
        cv = config.cv
    issues = run_all(doc, source, config) + _cv_issues(doc, cv, config)
    return config.apply(issues) if config else issues


def format_file(
    path: str,
    template: str | Template | None = None,
    output: str | None = None,
    keep_intermediate: str | bool | None = None,
    embed_cjk: bool = False,
    cv: bool | None = None,
    config: Config | None = None,
) -> Result:
    """Normalize a document and write the result to a new file.

    The source file is never modified. Writing over the input is refused.

    `keep_intermediate` also writes the extracted content as Markdown, which is
    the readable form of what was understood from the source. Useful for a PDF,
    where extraction is lossy and worth eyeballing -- and hand-editable, so the
    Markdown can be corrected and re-formatted. Pass a path, or True to derive
    one (`report.pdf` -> `report.extracted.md`).

    `embed_cjk` embeds an installed CJK font instead of relying on the reader's
    own, making the PDF self-contained. Falls back to the built-in CID
    collections, and reports it, when no suitable open-licensed font is
    installed -- so asking for it can never make CJK worse.

    `cv` controls the résumé rules: None auto-detects, True forces them on,
    False off. They only ever report.

    `config` supplies project settings -- the default template, disabled rules,
    severity overrides, per-rule options. An explicit argument always wins over
    the config file.
    """
    reader = adapters.for_path(path)
    resolved = _resolve_template(template, config)
    if cv is None and config is not None:
        cv = config.cv
    if embed_cjk:
        resolved = _with_embedded_cjk(resolved)

    destination = output or default_output_path(path)
    if os.path.abspath(destination) == os.path.abspath(path):
        raise ValueError(
            f"refusing to overwrite the source file {path!r}; "
            "docfix always writes to a separate file"
        )

    # The writer comes from the *output* extension, not the input. Any reader
    # can feed any writer -- that is what the shared IR is for -- so
    # `notes.md -> report.pdf` converts rather than writing Markdown into a
    # file named .pdf, which is what it used to do (BUG-001).
    writer = adapters.for_path(destination)

    doc = reader.read_path(path)
    source = reader.source_text(path) if reader.source_text else None
    issues = run_all(doc, source, config) + _cv_issues(doc, cv, config)
    if writer.coverage_issues:
        # What the *target* format cannot render, which only the template and
        # the fonts on this machine can decide.
        issues.extend(writer.coverage_issues(doc, resolved))
    if config:
        issues = config.apply(issues)

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

    writer.write_path(normalized, resolved, destination)
    return Result(
        source_path=path,
        output_path=destination,
        template=resolved.name,
        issues=issues,
        intermediate_path=intermediate_path,
    )


def format_text(text: str, template: str | Template | None = None) -> str:
    """Normalize a Markdown string and return it. Convenience for tests and pipes."""
    from docfix.adapters import markdown

    return markdown.write(normalize(markdown.read(text)), _resolve_template(template, None))


def looks_like_cv(path: str) -> bool:
    """Whether a document reads as a CV, by the same test `detect` uses."""
    from docfix import cv as cv_layer

    return cv_layer.looks_like_cv(adapters.for_path(path).read_path(path))


def list_templates() -> list[str]:
    """Names of the bundled templates."""
    return list_presets()


__all__ = [
    "DEFAULT_TEMPLATE",
    "Config",
    "ConfigError",
    "Document",
    "Issue",
    "PluginError",
    "Result",
    "Template",
    "TemplateError",
    "__version__",
    "default_output_path",
    "detect",
    "discover",
    "format_file",
    "format_text",
    "list_templates",
    "load",
    "load_plugins",
    "looks_like_cv",
    "plugin_errors",
    "register_adapter",
    "register_rule",
    "scan",
    "unregister_adapter",
    "unregister_rule",
]
