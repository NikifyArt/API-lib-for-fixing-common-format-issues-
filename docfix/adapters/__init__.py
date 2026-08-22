"""Format adapter registry.

An adapter converts one file format to and from the IR and holds no formatting
logic of its own. Adding a format means adding a module here -- detection,
fixing, and templates need no changes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from docfix.adapters import markdown as _markdown
from docfix.adapters import pdf as _pdf
from docfix.ir import Document
from docfix.templates import Template


class UnsupportedFormatError(ValueError):
    """Raised for a file extension no adapter handles."""


@dataclass(frozen=True)
class Adapter:
    name: str
    extensions: tuple[str, ...]
    read_path: Callable[[str], Document]
    write_path: Callable[[Document, Template, str], None]
    # Returns raw source for source-level rules, or None for binary formats.
    source_text: Callable[[str], str] | None = None
    # Reports what this format cannot render for a given template -- font
    # coverage, for instance. Depends on the template and the machine, so it
    # cannot be a plain structure rule.
    coverage_issues: Callable[..., list] | None = None


MARKDOWN = Adapter(
    name="markdown",
    extensions=_markdown.EXTENSIONS,
    read_path=_markdown.read_path,
    write_path=_markdown.write_path,
    source_text=_markdown.source_text,
)

PDF = Adapter(
    name="pdf",
    extensions=_pdf.EXTENSIONS,
    read_path=_pdf.read_path,
    write_path=_pdf.write_path,
    # PDF has no raw text source, so the source-level rules do not apply.
    source_text=None,
    coverage_issues=_pdf.coverage_issues,
)

# Phases 2 and 4 register the docx and cv adapters here.
ADAPTERS: tuple[Adapter, ...] = (MARKDOWN, PDF)


def supported_extensions() -> list[str]:
    return sorted(ext for adapter in ADAPTERS for ext in adapter.extensions)


def for_path(path: str) -> Adapter:
    extension = os.path.splitext(path)[1].lower()
    for adapter in ADAPTERS:
        if extension in adapter.extensions:
            return adapter
    raise UnsupportedFormatError(
        f"no adapter for {extension or 'a file with no extension'!r}; "
        f"supported: {', '.join(supported_extensions())}"
    )


__all__ = [
    "ADAPTERS",
    "MARKDOWN",
    "PDF",
    "Adapter",
    "UnsupportedFormatError",
    "for_path",
    "supported_extensions",
]
