"""Normalizers that repair the IR before it is written back out.

Only safe, structure-preserving repairs live here. Anything that would change
what the document *says* -- rewriting quote characters, renumbering headings --
is left to detection, so the user decides.
"""

from __future__ import annotations

from dataclasses import replace

from docfix.ir import (
    Block,
    BlockQuote,
    Document,
    Heading,
    ListBlock,
    ListItem,
    Paragraph,
    Run,
    plain_text,
)


def merge_adjacent_lists(blocks: list[Block]) -> list[Block]:
    """Merge consecutive lists of the same kind into one.

    Changing the bullet marker mid-list makes CommonMark start a *new* list, so
    a document that mixes `-`, `*`, and `+` parses as several lists in a row.
    That is precisely the "mixed markers" problem, and merging is the fix.
    """
    out: list[Block] = []
    for block in blocks:
        previous = out[-1] if out else None
        if (
            isinstance(block, ListBlock)
            and isinstance(previous, ListBlock)
            and previous.ordered == block.ordered
        ):
            previous.items.extend(block.items)
            previous.source_markers.extend(block.source_markers)
            continue
        out.append(block)
    return out


def drop_empty_paragraphs(blocks: list[Block]) -> list[Block]:
    return [
        block
        for block in blocks
        if not (isinstance(block, Paragraph) and not plain_text(block.runs).strip())
    ]


def strip_edge_whitespace(blocks: list[Block]) -> list[Block]:
    """Trim whitespace at the start and end of each text-bearing block."""
    for block in blocks:
        if isinstance(block, (Heading, Paragraph)):
            block.runs = _strip_runs(block.runs)
        elif isinstance(block, ListBlock):
            for item in block.items:
                item.runs = _strip_runs(item.runs)
    return blocks


def _strip_runs(runs: list[Run]) -> list[Run]:
    runs = [r for r in runs if r.text or r.raw]
    if not runs:
        return runs
    first, last = runs[0], runs[-1]
    if not first.code and not first.raw:
        runs[0] = replace(first, text=first.text.lstrip())
    if not last.code and not last.raw:
        runs[-1] = replace(runs[-1], text=runs[-1].text.rstrip())
    return [r for r in runs if r.text or r.raw]


def _recurse(blocks: list[Block]) -> list[Block]:
    """Apply the same normalization inside nested containers."""
    for block in blocks:
        if isinstance(block, BlockQuote):
            block.blocks = normalize_blocks(block.blocks)
        elif isinstance(block, ListBlock):
            for item in block.items:
                item.blocks = normalize_blocks(item.blocks)
    return blocks


def normalize_blocks(blocks: list[Block]) -> list[Block]:
    blocks = drop_empty_paragraphs(blocks)
    blocks = merge_adjacent_lists(blocks)
    blocks = strip_edge_whitespace(blocks)
    return _recurse(blocks)


def normalize(doc: Document) -> Document:
    """Return the document with every safe repair applied."""
    doc.blocks = normalize_blocks(doc.blocks)
    return doc


__all__ = [
    "ListItem",
    "drop_empty_paragraphs",
    "merge_adjacent_lists",
    "normalize",
    "normalize_blocks",
    "strip_edge_whitespace",
]
