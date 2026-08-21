"""Markdown adapter: CommonMark/GFM source in, canonical Markdown out.

Reading uses markdown-it-py so CommonMark edge cases are handled correctly.
Writing renders canonically from the IR rather than preserving the source --
normalizing to one consistent form is the whole point of a formatter.
"""

from __future__ import annotations

import re
import textwrap

from markdown_it import MarkdownIt

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
)
from docfix.templates import MarkdownStyle, Template

EXTENSIONS = (".md", ".markdown", ".mdown")


def _parser() -> MarkdownIt:
    # gfm-like gives tables and strikethrough. linkify is disabled because
    # silently turning bare URLs into links would change content, not format.
    return MarkdownIt("gfm-like").disable("linkify")


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _inline_runs(token) -> tuple[list[Run], list[Image]]:
    """Convert an inline token's children into runs, plus any standalone images."""
    runs: list[Run] = []
    images: list[Image] = []
    bold = italic = strike = 0
    link: str | None = None

    for child in token.children or []:
        kind = child.type
        if kind == "text":
            if child.content:
                runs.append(
                    Run(
                        child.content,
                        bold=bold > 0,
                        italic=italic > 0,
                        strike=strike > 0,
                        link=link,
                    )
                )
        elif kind == "code_inline":
            runs.append(
                Run(
                    child.content,
                    bold=bold > 0,
                    italic=italic > 0,
                    strike=strike > 0,
                    code=True,
                    link=link,
                )
            )
        elif kind == "strong_open":
            bold += 1
        elif kind == "strong_close":
            bold = max(0, bold - 1)
        elif kind == "em_open":
            italic += 1
        elif kind == "em_close":
            italic = max(0, italic - 1)
        elif kind == "s_open":
            strike += 1
        elif kind == "s_close":
            strike = max(0, strike - 1)
        elif kind == "link_open":
            link = child.attrGet("href") or ""
        elif kind == "link_close":
            link = None
        elif kind == "image":
            src = child.attrGet("src") or ""
            alt = "".join(c.content for c in (child.children or []))
            title = child.attrGet("title")
            images.append(Image(src=src, alt=alt, title=title))
            runs.append(Run(_render_image(src, alt, title), raw=True))
        elif kind in ("softbreak", "hardbreak"):
            runs.append(Run(" " if kind == "softbreak" else "  \n", raw=kind == "hardbreak"))

    return _merge_runs(runs), images


def _merge_runs(runs: list[Run]) -> list[Run]:
    """Collapse adjacent runs that carry identical marks."""
    merged: list[Run] = []
    for run in runs:
        if merged and not run.raw and not merged[-1].raw and merged[-1].same_marks_as(run):
            merged[-1] = Run(
                merged[-1].text + run.text,
                bold=run.bold,
                italic=run.italic,
                code=run.code,
                strike=run.strike,
                link=run.link,
            )
        else:
            merged.append(run)
    return merged


class _Reader:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def blocks(self, stop: str | None = None) -> list[Block]:
        out: list[Block] = []
        while self.pos < len(self.tokens):
            token = self.tokens[self.pos]
            if stop and token.type == stop:
                return out
            block = self.one()
            if block is not None:
                out.extend(block if isinstance(block, list) else [block])
        return out

    def one(self):
        token = self.tokens[self.pos]
        kind = token.type

        if kind == "heading_open":
            level = int(token.tag[1])
            self.pos += 1
            runs, _ = _inline_runs(self.tokens[self.pos])
            self.pos += 2  # inline + heading_close
            return Heading(level=level, runs=runs)

        if kind == "paragraph_open":
            self.pos += 1
            inline = self.tokens[self.pos]
            runs, images = _inline_runs(inline)
            self.pos += 2  # inline + paragraph_close
            # A paragraph that is nothing but an image becomes an Image block.
            if len(images) == 1 and len(runs) == 1 and runs[0].raw:
                return images[0]
            return Paragraph(runs=runs)

        if kind in ("fence", "code_block"):
            self.pos += 1
            language = (token.info or "").strip() or None
            return CodeBlock(code=token.content.rstrip("\n"), language=language)

        if kind == "hr":
            self.pos += 1
            return ThematicBreak()

        if kind == "blockquote_open":
            self.pos += 1
            inner = self.blocks(stop="blockquote_close")
            self.pos += 1
            return BlockQuote(blocks=inner)

        if kind in ("bullet_list_open", "ordered_list_open"):
            return self.list_block(token)

        if kind == "table_open":
            return self.table()

        # Anything unrecognized is skipped rather than silently mangled.
        self.pos += 1
        return None

    def list_block(self, token):
        ordered = token.type == "ordered_list_open"
        close = "ordered_list_close" if ordered else "bullet_list_close"
        start = int(token.attrGet("start") or 1) if ordered else 1
        self.pos += 1

        items: list[ListItem] = []
        markers: list[str] = []
        while self.pos < len(self.tokens) and self.tokens[self.pos].type != close:
            if self.tokens[self.pos].type != "list_item_open":
                self.pos += 1
                continue
            markers.append(self.tokens[self.pos].markup or "")
            self.pos += 1
            inner = self.blocks(stop="list_item_close")
            self.pos += 1

            runs: list[Run] = []
            rest: list = []
            for index, block in enumerate(inner):
                if index == 0 and isinstance(block, Paragraph):
                    runs = block.runs
                else:
                    rest.append(block)
            items.append(ListItem(runs=runs, blocks=rest))

        self.pos += 1  # list close
        return ListBlock(ordered=ordered, items=items, start=start, source_markers=markers)

    def table(self):
        self.pos += 1  # table_open
        header: list[list[Run]] = []
        rows: list[list[list[Run]]] = []
        alignments: list[str | None] = []
        current: list[list[Run]] = []
        in_header = False

        while self.pos < len(self.tokens) and self.tokens[self.pos].type != "table_close":
            token = self.tokens[self.pos]
            if token.type == "thead_open":
                in_header = True
            elif token.type == "thead_close":
                in_header = False
            elif token.type == "tr_open":
                current = []
            elif token.type == "tr_close":
                if in_header:
                    header = current
                else:
                    rows.append(current)
            elif token.type in ("th_open", "td_open"):
                if in_header:
                    style = token.attrGet("style") or ""
                    match = re.search(r"text-align:\s*(left|center|right)", style)
                    alignments.append(match.group(1) if match else None)
                self.pos += 1
                cell_runs, _ = _inline_runs(self.tokens[self.pos])
                current.append(cell_runs)
            self.pos += 1

        self.pos += 1  # table_close
        return Table(header=header, rows=rows, alignments=alignments)


def read(text: str) -> Document:
    """Parse Markdown source into the IR."""
    tokens = _parser().parse(text)
    return Document(blocks=_Reader(tokens).blocks())


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

_ESCAPE_LINE_START = re.compile(r"^(\s*)([#>]|[-+*](?=\s)|\d+(?=[.)]\s))")


def _render_image(src: str, alt: str, title: str | None) -> str:
    suffix = f' "{title}"' if title else ""
    return f"![{alt}]({src}{suffix})"


def _render_runs(runs: list[Run], style: MarkdownStyle) -> str:
    parts: list[str] = []
    for run in runs:
        if run.raw:
            parts.append(run.text)
            continue

        text = run.text
        if run.code:
            fence = "`"
            while fence in text:
                fence += "`"
            pad = " " if text.startswith("`") or text.endswith("`") else ""
            parts.append(f"{fence}{pad}{text}{pad}{fence}")
            continue

        if run.strike:
            text = f"~~{text}~~"
        if run.bold:
            text = f"{style.strong_marker}{text}{style.strong_marker}"
        if run.italic:
            text = f"{style.emphasis_marker}{text}{style.emphasis_marker}"
        if run.link is not None:
            text = f"[{text}]({run.link})"
        parts.append(text)
    return "".join(parts)


def _wrap(text: str, width: int) -> str:
    if width <= 0 or not text.strip():
        return text
    return "\n".join(
        textwrap.wrap(
            text,
            width=width,
            break_long_words=False,
            break_on_hyphens=False,
        )
        or [text]
    )


def _escape_block_start(text: str) -> str:
    """Keep a paragraph's first characters from being read as block syntax."""
    return _ESCAPE_LINE_START.sub(lambda m: f"{m.group(1)}\\{m.group(2)}", text, count=1)


def _render_block(block, style: MarkdownStyle) -> str:
    if isinstance(block, Heading):
        text = _render_runs(block.runs, style)
        if style.heading_style == "setext" and block.level in (1, 2):
            underline = "=" if block.level == 1 else "-"
            return f"{text}\n{underline * max(3, len(text))}"
        return f"{'#' * block.level} {text}"

    if isinstance(block, Paragraph):
        return _wrap(_escape_block_start(_render_runs(block.runs, style)), style.wrap_width)

    if isinstance(block, CodeBlock):
        fence = style.code_fence
        while fence in block.code:
            fence += style.code_fence[0]
        return f"{fence}{block.language or ''}\n{block.code}\n{fence}"

    if isinstance(block, ThematicBreak):
        return style.thematic_break

    if isinstance(block, Image):
        return _render_image(block.src, block.alt, block.title)

    if isinstance(block, BlockQuote):
        inner = _render_blocks(block.blocks, style)
        return "\n".join(f"> {line}".rstrip() for line in inner.split("\n"))

    if isinstance(block, ListBlock):
        return _render_list(block, style)

    if isinstance(block, Table):
        return _render_table(block, style)

    return ""


def _render_list(block: ListBlock, style: MarkdownStyle) -> str:
    lines: list[str] = []
    for index, item in enumerate(block.items):
        marker = f"{block.start + index}." if block.ordered else style.bullet_marker
        indent = " " * (len(marker) + 1)
        text = _render_runs(item.runs, style)
        lines.append(f"{marker} {text}".rstrip())

        for child in item.blocks:
            rendered = _render_block(child, style)
            if not rendered:
                continue
            lines.append("")
            lines.extend(f"{indent}{line}".rstrip() for line in rendered.split("\n"))
    return "\n".join(lines)


def _render_table(block: Table, style: MarkdownStyle) -> str:
    header = [_render_runs(cell, style) for cell in block.header]
    rows = [[_render_runs(cell, style) for cell in row] for row in block.rows]
    width_count = len(header)
    alignments = list(block.alignments) + [None] * (width_count - len(block.alignments))

    widths = [len(h) for h in header]
    for row in rows:
        for index, cell in enumerate(row[:width_count]):
            widths[index] = max(widths[index], len(cell))
    widths = [max(3, w) for w in widths]

    def line(cells: list[str]) -> str:
        padded = [
            cells[i].ljust(widths[i]) if i < len(cells) else " " * widths[i]
            for i in range(width_count)
        ]
        return "| " + " | ".join(padded) + " |"

    separators = []
    for index in range(width_count):
        width = widths[index]
        align = alignments[index]
        if align == "left":
            separators.append(":" + "-" * (width - 1))
        elif align == "right":
            separators.append("-" * (width - 1) + ":")
        elif align == "center":
            separators.append(":" + "-" * (width - 2) + ":")
        else:
            separators.append("-" * width)

    out = [line(header), "| " + " | ".join(separators) + " |"]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


def _render_blocks(blocks: list, style: MarkdownStyle) -> str:
    rendered = [_render_block(b, style) for b in blocks]
    return "\n\n".join(r for r in rendered if r.strip())


def write(doc: Document, template: Template) -> str:
    """Render the IR as canonical Markdown, ending with exactly one newline."""
    body = _render_blocks(doc.blocks, template.markdown)
    return body.rstrip("\n") + "\n" if body.strip() else ""


# --------------------------------------------------------------------------
# Path-level IO (the interface the registry uses)
# --------------------------------------------------------------------------


def read_path(path: str) -> Document:
    with open(path, encoding="utf-8") as handle:
        return read(handle.read())


def write_path(doc: Document, template: Template, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(write(doc, template))


def source_text(path: str) -> str:
    """Raw source, so the source-level rules can run."""
    with open(path, encoding="utf-8") as handle:
        return handle.read()
