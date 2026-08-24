"""DOCX adapter: Word documents in and out.

Unlike PDF, DOCX is a structured format: styles name what a paragraph *is*, so
extraction is faithful rather than inferred. Everything the IR models survives a
round trip -- headings (empty ones included), both list kinds with their
nesting, code blocks with their language, thematic breaks, tables, inline marks
and hyperlinks.

That works only because each one is written with a style the reader can key off.
Nesting rides in "List Bullet 2"/"3", code in a created "Code python" style, and
a thematic break in a bottom border rather than dash characters -- dashes are
literal content that reads back as a paragraph.

The one remaining ambiguity is Word's own: its bullet and numbered buttons both
produce "List Paragraph", and only w:numPr says which it was.

Also unlike PDF, there is no font-coverage problem. DOCX stores text as XML, so
any character survives regardless of the font; a font name is a *request* the
reader substitutes if it lacks it, and nothing is embedded. That is why this
adapter declares no `coverage_issues`.

`python-docx` is an optional dependency:

    pip install "docfix[docx]"
"""

from __future__ import annotations

import contextlib
import re

from docfix.ir import (
    Block,
    BlockQuote,
    CodeBlock,
    Document,
    Heading,
    ListBlock,
    ListItem,
    Paragraph,
    Run,
    Table,
    ThematicBreak,
    plain_text,
)
from docfix.templates import Template

EXTENSIONS = (".docx",)

HEADING_STYLE = re.compile(r"^Heading (\d)$", re.IGNORECASE)
BULLET_STYLE = re.compile(r"^List Bullet", re.IGNORECASE)
NUMBER_STYLE = re.compile(r"^List Number", re.IGNORECASE)
# Word's bullet and numbered buttons both produce "List Paragraph"; whether
# it is ordered lives in w:numPr, not the style. Bullets are far the commoner
# case, so it is read as one rather than silently renumbering a bullet list.
LIST_PARAGRAPH_STYLE = re.compile(r"^List Paragraph$", re.IGNORECASE)
QUOTE_STYLE = re.compile(r"quote", re.IGNORECASE)
# "Code python" carries the fence's language in the style name -- DOCX has
# nowhere else to put it, and dropping it loses something the author wrote.
# The language is restricted to a plain identifier so a style name stays sane.
CODE_STYLE = re.compile(r"^(?:HTML Code|Macro Text|Code)(?: ([\w+#.-]+))?$", re.IGNORECASE)
SAFE_LANGUAGE = re.compile(r"^[\w+#.-]{1,20}$")
# "List Bullet 2" is Word's second nesting level. The trailing digit is the
# only place the depth survives, so it is what rebuilds the nesting on read.
LIST_LEVEL = re.compile(r"\s(\d)$")

def _is_horizontal_rule(element) -> bool:
    """Whether an empty paragraph carries the bottom border Word draws a
    horizontal rule with. There is no thematic-break element in DOCX."""
    properties = element.find(f"{W_NS}pPr")
    if properties is None:
        return False
    borders = properties.find(f"{W_NS}pBdr")
    return borders is not None and borders.find(f"{W_NS}bottom") is not None


def _list_level(style: str) -> int:
    """Nesting depth from a list style name; 1 when it names no level."""
    match = LIST_LEVEL.search(style)
    return int(match.group(1)) if match else 1


def _nest_items(entries: list) -> list:
    """Rebuild nested lists from flat (level, ordered, item) in document order.

    DOCX has no tree: nesting is carried by the style name alone, so the
    structure has to be reconstructed rather than read.
    """
    top: list = []
    stack: list = []  # (level, ListBlock)

    for level, ordered, item in entries:
        # Close any list deeper than this one, or one at the same depth that
        # is a different kind -- a bullet list does not continue a numbered one.
        while stack and (
            stack[-1][0] > level
            or (stack[-1][0] == level and stack[-1][1].ordered != ordered)
        ):
            stack.pop()

        if not stack or stack[-1][0] < level:
            block = ListBlock(ordered=ordered, items=[])
            if stack and stack[-1][1].items:
                stack[-1][1].items[-1].blocks.append(block)
            else:
                top.append(block)
            stack.append((level, block))

        stack[-1][1].items.append(item)

    return top


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class MissingDependencyError(ImportError):
    """Raised when the DOCX extra is not installed."""


def _require():
    try:
        import docx  # type: ignore[import-not-found]  # noqa: F401
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        raise MissingDependencyError(
            "python-docx is needed for DOCX support but could not be imported "
            f"({type(exc).__name__}: {exc}); "
            'install it with: pip install "docfix[docx]"'
        ) from exc


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _runs_from_element(element, part) -> list[Run]:
    """Runs inside a paragraph element, following hyperlinks into their runs."""
    from docx.text.run import Run as DocxRun  # type: ignore[import-not-found]

    out: list[Run] = []
    for child in element.iterchildren():
        tag = child.tag
        if tag == f"{W_NS}r":
            docx_run = DocxRun(child, part)
            text = docx_run.text
            if not text:
                continue
            font = docx_run.font
            out.append(
                Run(
                    text,
                    bold=bool(docx_run.bold),
                    italic=bool(docx_run.italic),
                    strike=bool(font.strike),
                )
            )
        elif tag == f"{W_NS}hyperlink":
            target = None
            rel_id = child.get(f"{R_NS}id")
            if rel_id:
                try:
                    target = part.part.rels[rel_id].target_ref
                except (KeyError, AttributeError):
                    target = None
            for run in _runs_from_element(child, part):
                out.append(
                    Run(
                        run.text,
                        bold=run.bold,
                        italic=run.italic,
                        strike=run.strike,
                        link=target,
                    )
                )
    return out


def _merge(runs: list[Run]) -> list[Run]:
    merged: list[Run] = []
    for run in runs:
        if merged and merged[-1].same_marks_as(run):
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


def _table_block(table) -> Table | None:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return None
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    return Table(
        header=[[Run(cell)] for cell in padded[0]],
        rows=[[[Run(cell)] for cell in row] for row in padded[1:]],
        alignments=[None] * width,
    )


def _style_name(paragraph) -> str:
    try:
        return paragraph.style.name or "Normal"
    except (AttributeError, KeyError):
        return "Normal"


def read_path(path: str) -> Document:
    """Read a Word document into the IR."""
    _require()
    from docx import Document as DocxDocument  # type: ignore[import-not-found]
    from docx.table import Table as DocxTable  # type: ignore[import-not-found]
    from docx.text.paragraph import Paragraph as DocxParagraph  # type: ignore

    source = DocxDocument(path)
    blocks: list[Block] = []

    # Consecutive list paragraphs form one list, so they are accumulated with
    # their depth and rebuilt into a tree when the run ends.
    pending: list = []  # (level, ordered, ListItem)

    def flush_list() -> None:
        nonlocal pending
        if pending:
            blocks.extend(_nest_items(pending))
            pending = []

    # Consecutive Code paragraphs are one code block: the writer emits a
    # paragraph per line, because Word has no multi-line block element.
    code_lines: list[str] = []
    code_language: str | None = None

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            blocks.append(CodeBlock(code="\n".join(code_lines), language=code_language))
            code_lines = []

    for child in source.element.body.iterchildren():
        tag = child.tag

        if tag == f"{W_NS}tbl":
            flush_list()
            table = _table_block(DocxTable(child, source))
            if table is not None:
                blocks.append(table)
            continue

        if tag != f"{W_NS}p":
            continue

        paragraph = DocxParagraph(child, source)
        runs = _merge(_runs_from_element(child, source))
        style = _style_name(paragraph)
        text = plain_text(runs)

        # A code line may legitimately be blank, so the code run is continued
        # before the empty-paragraph skip below rather than after it.
        code_match = CODE_STYLE.match(style)
        if code_match:
            flush_list()
            language = code_match.group(1)
            if code_lines and language != code_language:
                flush_code()
            code_language = language
            code_lines.append(text)
            continue
        flush_code()

        if not text.strip():
            # An empty paragraph is spacing, not content -- unless it carries a
            # bottom border, which is how Word draws a horizontal rule.
            if _is_horizontal_rule(child):
                flush_list()
                blocks.append(ThematicBreak())
                continue
            # An empty *heading* is structure, not spacing: it holds a place in
            # the outline, and check_empty_headings exists to report it -- which
            # it cannot do if the reader drops it first.
            empty_heading = HEADING_STYLE.match(style)
            if empty_heading:
                flush_list()
                blocks.append(Heading(level=min(6, max(1, int(empty_heading.group(1)))), runs=[]))
            continue

        heading = HEADING_STYLE.match(style)
        if heading or style.lower() == "title":
            flush_list()
            level = int(heading.group(1)) if heading else 1
            blocks.append(Heading(level=min(6, max(1, level)), runs=runs))
            continue

        if (
            BULLET_STYLE.match(style)
            or NUMBER_STYLE.match(style)
            or LIST_PARAGRAPH_STYLE.match(style)
        ):
            ordered = bool(NUMBER_STYLE.match(style)) and not BULLET_STYLE.match(style)
            pending.append((_list_level(style), ordered, ListItem(runs=runs)))
            continue

        flush_list()

        if QUOTE_STYLE.search(style):
            blocks.append(BlockQuote(blocks=[Paragraph(runs=runs)]))
        else:
            blocks.append(Paragraph(runs=runs))

    flush_list()
    flush_code()

    properties = source.core_properties
    return Document(
        blocks=blocks,
        meta={
            "source_format": "docx",
            "title": (properties.title or "").strip(),
        },
    )


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _first_family(stack: str, fallback: str = "Calibri") -> str:
    """The first real family in a CSS-style stack.

    DOCX names a font rather than embedding it, so generic CSS keywords are
    meaningless here -- Word would look for a font literally called
    "sans-serif". They are skipped.
    """
    generic = {"serif", "sans-serif", "monospace", "system-ui", "cursive", "fantasy"}
    for part in (stack or "").split(","):
        name = part.strip().strip("'\"").strip()
        if name and name.lower() not in generic:
            return name
    return fallback


def _apply_font(style, family: str, size: float | None = None, color: str | None = None):
    from docx.shared import Pt, RGBColor  # type: ignore[import-not-found]

    style.font.name = family
    # Word uses a separate attribute for East Asian text; without it, CJK
    # falls back to the reader's default rather than the chosen family.
    element = style.element.rPr
    if element is not None and element.rFonts is not None:
        element.rFonts.set(f"{W_NS}eastAsia", family)
    if size:
        style.font.size = Pt(size)
    if color:
        # A malformed colour in a template should not stop the document.
        with contextlib.suppress(ValueError, AttributeError):
            style.font.color.rgb = RGBColor.from_string(color.lstrip("#").upper())


def _apply_template(document, template: Template) -> None:
    """Push the template's fonts, spacing, and colours into the document styles."""
    from docx.shared import Pt  # type: ignore[import-not-found]

    fonts = template.fonts or {}
    spacing = template.spacing or {}
    palette = template.colors or {}

    body = fonts.get("body") or {}
    heading = fonts.get("heading") or {}
    mono = fonts.get("mono") or {}

    body_family = _first_family(body.get("family", ""))
    heading_family = _first_family(heading.get("family", ""), body_family)
    mono_family = _first_family(mono.get("family", ""), "Consolas")

    body_size = float(body.get("size", 11))
    heading_size = float(heading.get("size", 16))

    styles = document.styles
    normal = styles["Normal"]
    _apply_font(normal, body_family, body_size, palette.get("text"))

    paragraph_format = normal.paragraph_format
    if spacing.get("paragraph_after") is not None:
        paragraph_format.space_after = Pt(float(spacing["paragraph_after"]))
    if spacing.get("line_height") is not None:
        paragraph_format.line_spacing = float(spacing["line_height"])

    for level in range(1, 7):
        try:
            style = styles[f"Heading {level}"]
        except KeyError:
            continue
        size = max(body_size + 1, heading_size - (level - 1) * 1.8)
        _apply_font(style, heading_family, size, palette.get("heading"))
        if spacing.get("heading_before") is not None:
            style.paragraph_format.space_before = Pt(float(spacing["heading_before"]))
        if spacing.get("heading_after") is not None:
            style.paragraph_format.space_after = Pt(float(spacing["heading_after"]))

    for name in ("HTML Code", "Macro Text"):
        try:
            _apply_font(styles[name], mono_family, float(mono.get("size", 9)))
        except KeyError:
            continue


def _add_hyperlink(paragraph, url: str, run: Run, palette: dict):
    """Word has no high-level hyperlink API; build the element directly."""
    from docx.oxml.ns import qn  # type: ignore[import-not-found]
    from docx.oxml.shared import OxmlElement  # type: ignore[import-not-found]

    part = paragraph.part
    rel_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )

    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)

    node = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")

    color = OxmlElement("w:color")
    color.set(qn("w:val"), (palette.get("link") or "#0000EE").lstrip("#").upper())
    properties.append(color)

    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(underline)

    if run.bold:
        properties.append(OxmlElement("w:b"))
    if run.italic:
        properties.append(OxmlElement("w:i"))

    node.append(properties)
    text = OxmlElement("w:t")
    text.text = run.text
    text.set(qn("xml:space"), "preserve")
    node.append(text)

    link.append(node)
    paragraph._p.append(link)


def _write_runs(paragraph, runs: list[Run], template: Template) -> None:
    palette = template.colors or {}
    mono_family = _first_family((template.fonts or {}).get("mono", {}).get("family", ""), "Consolas")

    for run in runs:
        if run.link:
            _add_hyperlink(paragraph, run.link, run, palette)
            continue

        text = run.text
        if run.raw:
            # Raw runs carry Markdown syntax for inline images; show the alt text.
            match = re.match(r"!\[(.*?)\]\((.*?)\)", text)
            text = (match.group(1) or match.group(2)) if match else text

        added = paragraph.add_run(text)
        added.bold = run.bold
        added.italic = run.italic
        added.font.strike = run.strike
        if run.code:
            added.font.name = mono_family


def _ensure_code_style(document, template: Template, language: str | None = None) -> str:
    """A paragraph style named "Code", or "Code python", created on demand.

    Word ships no code style, and a custom one is better than borrowing a
    semantically wrong built-in: it survives the round trip, and a user editing
    the result sees it in Word's own style list. The language rides in the name
    because DOCX offers nowhere else to keep it.
    """
    from docx.enum.style import WD_STYLE_TYPE  # type: ignore[import-not-found]
    from docx.shared import Pt  # type: ignore[import-not-found]

    name = "Code"
    if language and SAFE_LANGUAGE.match(language):
        name = f"Code {language}"

    styles = document.styles
    try:
        styles[name]
        return name
    except KeyError:
        pass

    mono = (template.fonts or {}).get("mono", {})
    style = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    _apply_font(
        style,
        _first_family(mono.get("family", ""), "Consolas"),
        float(mono.get("size", 9)),
    )
    style.paragraph_format.space_after = Pt(0)
    return name


def _add_horizontal_rule(document) -> None:
    """An empty paragraph with a bottom border -- Word's horizontal rule."""
    from docx.oxml.ns import qn  # type: ignore[import-not-found]
    from docx.oxml.shared import OxmlElement  # type: ignore[import-not-found]

    paragraph = document.add_paragraph()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "auto")
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def _write_blocks(document, blocks: list[Block], template: Template, depth: int = 0) -> None:
    from docx.shared import Pt  # type: ignore[import-not-found]

    for block in blocks:
        if isinstance(block, Heading):
            paragraph = document.add_paragraph(style=f"Heading {min(6, max(1, block.level))}")
            _write_runs(paragraph, block.runs, template)

        elif isinstance(block, Paragraph):
            _write_runs(document.add_paragraph(), block.runs, template)

        elif isinstance(block, ListBlock):
            base = "List Number" if block.ordered else "List Bullet"
            # Word ships levels 1-3 only; deeper nesting is drawn at 3 rather
            # than falling back to level 1, which would read back as flat.
            style = base if depth == 0 else f"{base} {min(depth + 1, 3)}"
            for item in block.items:
                paragraph = document.add_paragraph(style=style)
                _write_runs(paragraph, item.runs, template)
                if item.blocks:
                    _write_blocks(document, item.blocks, template, depth + 1)

        elif isinstance(block, CodeBlock):
            # Styled, not merely monospaced. Word has no multi-line block
            # element, so a code block is one paragraph per line -- and without
            # a style saying so, they read back as ordinary prose and every
            # prose rule then applies to code.
            style = _ensure_code_style(document, template, block.language)
            for line in block.code.split("\n"):
                paragraph = document.add_paragraph(style=style)
                paragraph.add_run(line)

        elif isinstance(block, BlockQuote):
            for inner in block.blocks:
                if isinstance(inner, Paragraph):
                    # Use the built-in Quote style, not just an indent: the
                    # style is what identifies it as a quotation on the way
                    # back in, so an indented Normal paragraph would read back
                    # as ordinary prose.
                    try:
                        paragraph = document.add_paragraph(style="Quote")
                    except KeyError:
                        paragraph = document.add_paragraph()
                        paragraph.paragraph_format.left_indent = Pt(24)
                    _write_runs(paragraph, inner.runs, template)
                else:
                    _write_blocks(document, [inner], template)

        elif isinstance(block, Table):
            columns = len(block.header) or (len(block.rows[0]) if block.rows else 0)
            if not columns:
                continue
            table = document.add_table(rows=0, cols=columns)
            table.style = "Table Grid"
            if block.header:
                cells = table.add_row().cells
                for index, cell_runs in enumerate(block.header[:columns]):
                    paragraph = cells[index].paragraphs[0]
                    _write_runs(paragraph, cell_runs, template)
                    for run in paragraph.runs:
                        run.bold = True
            for row in block.rows:
                cells = table.add_row().cells
                for index, cell_runs in enumerate(row[:columns]):
                    _write_runs(cells[index].paragraphs[0], cell_runs, template)

        elif isinstance(block, ThematicBreak):
            # A row of dash characters is *content*: it reads back as a
            # paragraph of dashes, and a second pass would keep it. A bottom
            # border is how Word actually draws a rule, and carries no text.
            _add_horizontal_rule(document)


def write_path(doc: Document, template: Template, path: str) -> None:
    """Write the IR out as a Word document."""
    _require()
    from docx import Document as DocxDocument  # type: ignore[import-not-found]

    document = DocxDocument()
    _apply_template(document, template)
    _write_blocks(document, doc.blocks, template)

    title = (doc.meta or {}).get("title")
    if title:
        document.core_properties.title = title

    document.save(path)
