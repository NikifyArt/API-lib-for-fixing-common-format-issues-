"""Format adapter registry.

An adapter converts one file format to and from the IR and holds no formatting
logic of its own. Adding a format means adding a module -- detection, fixing,
and templates need no changes.

Three adapters ship with docfix. Anything else registers from outside via
`docfix.register_adapter()` or a `docfix.plugins` entry point, so a fork adding
a format never edits this file and never conflicts with upstream. See
`docfix/plugins.py` and `docs/EXTENDING.md`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from docfix.adapters import docx as _docx
from docfix.adapters import markdown as _markdown
from docfix.adapters import pdf as _pdf
from docfix.ir import Document
from docfix.plugins import (
    register_adapter,
    registered_adapters,
    unregister_adapter,
)
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

DOCX = Adapter(
    name="docx",
    extensions=_docx.EXTENSIONS,
    read_path=_docx.read_path,
    write_path=_docx.write_path,
    # DOCX stores text as XML, so there is no raw source to scan and no font
    # coverage problem: any character survives whatever font is named.
    source_text=None,
    coverage_issues=None,
)

BUILTIN_ADAPTERS: tuple[Adapter, ...] = (MARKDOWN, PDF, DOCX)


def adapters() -> tuple[Adapter, ...]:
    """Every adapter in effect: registered ones first, then the built-ins.

    Registered first so that claiming an extension docfix already handles
    overrides it. That is what lets a fork substitute its own PDF reader
    without touching the built-in one.
    """
    return registered_adapters() + BUILTIN_ADAPTERS


def supported_extensions() -> list[str]:
    return sorted({ext for adapter in adapters() for ext in adapter.extensions})


def for_path(path: str) -> Adapter:
    extension = os.path.splitext(path)[1].lower()
    for adapter in adapters():
        if extension in adapter.extensions:
            return adapter
    raise UnsupportedFormatError(
        f"no adapter for {extension or 'a file with no extension'!r}; "
        f"supported: {', '.join(supported_extensions())}"
    )


def __getattr__(name: str):
    # ADAPTERS was a plain tuple before adapters became registrable. Kept as a
    # live view rather than removed, so existing code keeps working and picks
    # up registered adapters rather than silently missing them.
    if name == "ADAPTERS":
        import warnings

        warnings.warn(
            "docfix.adapters.ADAPTERS is deprecated; call adapters() instead, "
            "which includes adapters registered by plugins",
            DeprecationWarning,
            stacklevel=2,
        )
        return adapters()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BUILTIN_ADAPTERS",
    "DOCX",
    "MARKDOWN",
    "PDF",
    "Adapter",
    "UnsupportedFormatError",
    "adapters",
    "for_path",
    "register_adapter",
    "registered_adapters",
    "supported_extensions",
    "unregister_adapter",
]
