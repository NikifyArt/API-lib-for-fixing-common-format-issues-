"""The document intermediate representation.

Every format is read into this IR, and every writer renders out of it.
Detection, fixing, and template application operate on the IR alone, so a rule
is written once and works for Markdown, DOCX, and PDF alike. Format adapters
hold conversion logic only -- never formatting rules.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class Run:
    """A span of inline text carrying its marks."""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    strike: bool = False
    link: str | None = None
    # Emit verbatim, skipping escaping and marker rewriting. Used for inline
    # constructs the IR does not model structurally, such as inline images.
    raw: bool = False

    def same_marks_as(self, other: Run) -> bool:
        return (
            self.bold == other.bold
            and self.italic == other.italic
            and self.code == other.code
            and self.strike == other.strike
            and self.link == other.link
            and self.raw == other.raw
        )


@dataclass
class Block:
    """Base class for block-level content."""


@dataclass
class Heading(Block):
    level: int
    runs: list[Run] = field(default_factory=list)


@dataclass
class Paragraph(Block):
    runs: list[Run] = field(default_factory=list)


@dataclass
class ListItem:
    runs: list[Run] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)


@dataclass
class ListBlock(Block):
    ordered: bool = False
    items: list[ListItem] = field(default_factory=list)
    start: int = 1
    # The marker characters seen in the source, used to detect inconsistency
    # before normalization rewrites them.
    source_markers: list[str] = field(default_factory=list)


@dataclass
class CodeBlock(Block):
    code: str = ""
    language: str | None = None


@dataclass
class BlockQuote(Block):
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Table(Block):
    header: list[list[Run]] = field(default_factory=list)
    rows: list[list[list[Run]]] = field(default_factory=list)
    alignments: list[str | None] = field(default_factory=list)


@dataclass
class Image(Block):
    src: str = ""
    alt: str = ""
    title: str | None = None


@dataclass
class ThematicBreak(Block):
    pass


@dataclass
class Document:
    blocks: list[Block] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def plain_text(runs: list[Run]) -> str:
    """Flatten runs to their text, dropping marks."""
    return "".join(run.text for run in runs)


def walk(node: Document | Block) -> Iterator[Block]:
    """Yield every block in document order, descending into nested containers."""
    blocks = node.blocks if isinstance(node, (Document, BlockQuote)) else [node]
    for block in blocks:
        yield block
        if isinstance(block, BlockQuote):
            yield from walk(block)
        elif isinstance(block, ListBlock):
            for item in block.items:
                for child in item.blocks:
                    yield child
                    if isinstance(child, (BlockQuote, ListBlock)):
                        yield from walk(child)


def iter_block_sequences(node: Document | Block) -> Iterator[list[Block]]:
    """Yield each sibling sequence of blocks: the document body, and the body of
    every block quote and list item.

    Rules that care about *adjacency* need siblings, which `walk` flattens away.
    """
    if isinstance(node, (Document, BlockQuote)):
        yield node.blocks
        for child in node.blocks:
            yield from iter_block_sequences(child)
    elif isinstance(node, ListBlock):
        for item in node.items:
            yield item.blocks
            for child in item.blocks:
                yield from iter_block_sequences(child)


def iter_runs(block: Block) -> Iterator[Run]:
    """Yield every run inside a block, wherever it is nested."""
    if isinstance(block, (Heading, Paragraph)):
        yield from block.runs
    elif isinstance(block, ListBlock):
        for item in block.items:
            yield from item.runs
            for child in item.blocks:
                yield from iter_runs(child)
    elif isinstance(block, BlockQuote):
        for child in block.blocks:
            yield from iter_runs(child)
    elif isinstance(block, Table):
        for cell in block.header:
            yield from cell
        for row in block.rows:
            for cell in row:
                yield from cell
