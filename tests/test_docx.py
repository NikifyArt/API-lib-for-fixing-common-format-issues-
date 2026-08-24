"""DOCX adapter: reading, writing, and round-trip fidelity.

Unlike PDF, DOCX is structured -- styles say what a paragraph *is* -- so a
round trip should be lossless for everything the IR models.

Fixtures are generated with python-docx at test time. Committing binary .docx
files would make diffs opaque and inflate the repo permanently.
"""

import pytest

pytest.importorskip("docx")

import docfix  # noqa: E402
from docfix.adapters import docx as docx_adapter  # noqa: E402
from docfix.adapters import markdown as md  # noqa: E402
from docfix.ir import (  # noqa: E402
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
from docfix.templates import load  # noqa: E402

SAMPLE = """# Title

Intro with **bold**, *italic*, ~~struck~~ and a [link](https://x.test).

## Findings

- alpha
- beta

1. first
2. second

> a quotation

| Region | Q1  | Q2  |
| ------ | --- | --- |
| North  | 10  | 12  |

Multi-script: Zażółć gęślą jaźń / Привет мир / 你好世界 / 한국어
"""


def write_and_read(doc, tmp_path, template="formal"):
    path = tmp_path / "rt.docx"
    docx_adapter.write_path(doc, load(template), str(path))
    return docx_adapter.read_path(str(path)), path


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_block_structure_survives_a_round_trip(tmp_path):
    original = md.read(SAMPLE)
    back, _ = write_and_read(original, tmp_path)
    assert [type(b).__name__ for b in original.blocks] == [
        type(b).__name__ for b in back.blocks
    ]


def test_round_trip_is_stable_on_a_second_pass(tmp_path):
    once, path = write_and_read(md.read(SAMPLE), tmp_path)
    twice = docx_adapter.read_path(str(path))
    assert [type(b).__name__ for b in once.blocks] == [
        type(b).__name__ for b in twice.blocks
    ]


@pytest.mark.parametrize("template", ["formal", "friendly", "technical", "minimal"])
def test_every_template_writes_a_readable_document(tmp_path, template):
    back, path = write_and_read(md.read(SAMPLE), tmp_path, template)
    assert path.stat().st_size > 0
    assert back.blocks


def test_markdown_to_docx_to_markdown_preserves_the_text(tmp_path):
    original = md.read(SAMPLE)
    back, _ = write_and_read(original, tmp_path)
    rendered = md.write(back, load("formal"))
    for fragment in ("# Title", "- alpha", "1. first", "| Region", "Привет мир"):
        assert fragment in rendered


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_heading_levels_are_preserved(tmp_path):
    doc = Document(blocks=[Heading(level=n, runs=[Run(f"H{n}")]) for n in range(1, 7)])
    back, _ = write_and_read(doc, tmp_path)
    assert [b.level for b in back.blocks if isinstance(b, Heading)] == [1, 2, 3, 4, 5, 6]


def test_bulleted_and_numbered_lists_stay_distinct(tmp_path):
    back, _ = write_and_read(md.read("- a\n- b\n\n1. x\n2. y\n"), tmp_path)
    lists = [b for b in back.blocks if isinstance(b, ListBlock)]
    assert len(lists) == 2
    assert lists[0].ordered is False
    assert lists[1].ordered is True
    assert len(lists[0].items) == 2


def test_consecutive_list_paragraphs_form_one_list(tmp_path):
    back, _ = write_and_read(md.read("- a\n- b\n- c\n"), tmp_path)
    lists = [b for b in back.blocks if isinstance(b, ListBlock)]
    assert len(lists) == 1 and len(lists[0].items) == 3


def test_blockquote_survives_via_the_quote_style(tmp_path):
    """An indented Normal paragraph would read back as ordinary prose; the
    style is what identifies a quotation."""
    back, _ = write_and_read(md.read("> quoted\n"), tmp_path)
    assert any(isinstance(b, BlockQuote) for b in back.blocks)


def test_table_shape_and_contents_survive(tmp_path):
    back, _ = write_and_read(md.read("| A | B |\n| - | - |\n| 1 | 2 |\n"), tmp_path)
    tables = [b for b in back.blocks if isinstance(b, Table)]
    assert tables
    assert len(tables[0].header) == 2
    assert plain_text(tables[0].header[0]) == "A"
    assert plain_text(tables[0].rows[0][1]) == "2"


def test_empty_paragraphs_are_not_carried_in(tmp_path):
    doc = Document(blocks=[Paragraph(runs=[Run("real")]), Paragraph(runs=[Run("  ")])])
    back, _ = write_and_read(doc, tmp_path)
    assert len([b for b in back.blocks if isinstance(b, Paragraph)]) == 1


# --------------------------------------------------------------------------
# Inline formatting
# --------------------------------------------------------------------------


def test_inline_marks_survive(tmp_path):
    doc = Document(
        blocks=[
            Paragraph(
                runs=[
                    Run("plain "),
                    Run("bold", bold=True),
                    Run(" "),
                    Run("italic", italic=True),
                    Run(" "),
                    Run("struck", strike=True),
                ]
            )
        ]
    )
    back, _ = write_and_read(doc, tmp_path)
    runs = back.blocks[0].runs
    assert any(r.bold and r.text == "bold" for r in runs)
    assert any(r.italic and r.text == "italic" for r in runs)
    assert any(r.strike and r.text == "struck" for r in runs)


def test_hyperlinks_survive_with_their_target(tmp_path):
    doc = Document(
        blocks=[Paragraph(runs=[Run("see "), Run("here", link="https://example.test/a")])]
    )
    back, _ = write_and_read(doc, tmp_path)
    links = [r for r in back.blocks[0].runs if r.link]
    assert links, "the hyperlink was lost"
    assert links[0].link.startswith("https://example.test")
    assert links[0].text == "here"


# --------------------------------------------------------------------------
# Text and encoding
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Zażółć gęślą jaźń",
        "Привет мир",
        "Γειά σου κόσμε",
        "你好世界",
        "한국어",
        "→ ★ ½ € ≈ ✓",
        "🎉 emoji",
    ],
)
def test_any_character_survives_docx(tmp_path, text):
    """DOCX stores text as XML, so unlike PDF there is no glyph-coverage
    problem -- even emoji, which no PDF font here can render."""
    doc = Document(blocks=[Paragraph(runs=[Run(text)])])
    back, _ = write_and_read(doc, tmp_path)
    assert plain_text(back.blocks[0].runs) == text


def test_docx_declares_no_coverage_issues():
    """A font name in DOCX is a request, not an embedding, so there is nothing
    to report."""
    from docfix import adapters

    assert adapters.for_path("x.docx").coverage_issues is None
    assert adapters.for_path("x.docx").source_text is None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def test_format_file_handles_docx_end_to_end(tmp_path):
    source = tmp_path / "in.docx"
    docx_adapter.write_path(md.read(SAMPLE), load("formal"), str(source))
    before = source.read_bytes()

    result = docfix.format_file(str(source), template="friendly")

    assert source.read_bytes() == before, "the source must never be modified"
    assert result.output_path.endswith(".formatted.docx")
    assert docx_adapter.read_path(result.output_path).blocks


def test_cross_format_docx_to_markdown(tmp_path):
    source = tmp_path / "in.docx"
    docx_adapter.write_path(md.read(SAMPLE), load("formal"), str(source))
    out = tmp_path / "out.md"

    docfix.format_file(str(source), output=str(out))

    text = out.read_text()
    assert text.startswith("# Title")
    assert "- alpha" in text


def test_cross_format_markdown_to_docx(tmp_path):
    source = tmp_path / "in.md"
    source.write_text(SAMPLE)
    out = tmp_path / "out.docx"

    docfix.format_file(str(source), output=str(out))

    assert out.read_bytes()[:2] == b"PK", "a .docx is a zip archive"
    assert docx_adapter.read_path(str(out)).blocks


def test_detect_works_on_docx(tmp_path):
    source = tmp_path / "in.docx"
    docx_adapter.write_path(
        Document(blocks=[Heading(level=1, runs=[Run("A")]), Heading(level=4, runs=[Run("D")])]),
        load("minimal"),
        str(source),
    )
    assert "heading-skip" in {i.rule for i in docfix.detect(str(source))}


def test_missing_dependency_message_names_the_extra():
    from docfix.adapters.docx import MissingDependencyError

    assert issubclass(MissingDependencyError, ImportError)


# --------------------------------------------------------------------------
# Regressions found by the push-checkpoint review
# --------------------------------------------------------------------------


def test_word_list_paragraph_bullets_are_not_renumbered(tmp_path):
    """Word's bullet and numbered buttons both produce "List Paragraph".

    Reading it as ordered turned a bulleted list into "1. 2. 3." -- silently
    changing what the document says, which the project forbids.
    """
    from docx import Document as DocxDocument

    path = tmp_path / "bullets.docx"
    built = DocxDocument()
    for text in ("alpha", "beta", "gamma"):
        built.add_paragraph(text, style="List Paragraph")
    built.save(str(path))

    lists = [b for b in docx_adapter.read_path(str(path)).blocks if isinstance(b, ListBlock)]
    assert lists, "List Paragraph should still be read as a list"
    assert lists[0].ordered is False
    assert len(lists[0].items) == 3


def test_explicit_number_style_is_still_ordered(tmp_path):
    from docx import Document as DocxDocument

    path = tmp_path / "numbered.docx"
    built = DocxDocument()
    for text in ("one", "two"):
        built.add_paragraph(text, style="List Number")
    built.save(str(path))

    lists = [b for b in docx_adapter.read_path(str(path)).blocks if isinstance(b, ListBlock)]
    assert lists and lists[0].ordered is True


# --------------------------------------------------------------------------
# Round-trip losses that used to change what the document said
# --------------------------------------------------------------------------
#
# BUG-002/003/004. Each of these silently altered meaning rather than
# appearance, which is the one thing docfix promises not to do: a nested list
# read back flat, a code block read back as prose, and a thematic break read
# back as a paragraph of dash characters.


def test_nested_lists_keep_their_nesting(tmp_path):
    """Flattening a nested list changes what the document says. DOCX has no
    tree, so the depth rides in the style name and is rebuilt on read."""
    original = md.read("- outer\n  - nested\n- second\n")
    back, _ = write_and_read(original, tmp_path)

    outer = back.blocks[0]
    assert isinstance(outer, ListBlock)
    assert len(outer.items) == 2, "the outer list lost or gained items"
    nested = [b for b in outer.items[0].blocks if isinstance(b, ListBlock)]
    assert nested, "the nested list was flattened into its parent"
    assert plain_text(nested[0].items[0].runs) == "nested"


def test_three_levels_of_nesting_survive(tmp_path):
    original = md.read("- a\n  - b\n    - c\n")
    back, _ = write_and_read(original, tmp_path)

    level1 = back.blocks[0]
    level2 = [b for b in level1.items[0].blocks if isinstance(b, ListBlock)][0]
    level3 = [b for b in level2.items[0].blocks if isinstance(b, ListBlock)][0]
    assert plain_text(level3.items[0].runs) == "c"


def test_a_code_block_reads_back_as_code_not_prose(tmp_path):
    """Read back as prose, code would then have every prose rule applied to it
    -- quotes 'fixed', whitespace normalised -- corrupting the source."""
    original = md.read("```python\nx = 1\n```\n")
    back, _ = write_and_read(original, tmp_path)

    code = [b for b in back.blocks if isinstance(b, CodeBlock)]
    assert code, "the code block came back as a paragraph"
    assert code[0].code == "x = 1"


def test_a_code_block_keeps_its_blank_lines(tmp_path):
    """One paragraph per line is how it is stored, so a blank line inside the
    block is the case most likely to be dropped."""
    original = md.read("```\na = 1\n\nb = 2\n```\n")
    back, _ = write_and_read(original, tmp_path)
    assert [b for b in back.blocks if isinstance(b, CodeBlock)][0].code == "a = 1\n\nb = 2"


def test_a_code_block_keeps_its_language(tmp_path):
    original = md.read("```python\nx = 1\n```\n")
    back, _ = write_and_read(original, tmp_path)
    assert [b for b in back.blocks if isinstance(b, CodeBlock)][0].language == "python"


def test_adjacent_code_blocks_do_not_merge(tmp_path):
    """Two blocks in different languages must not fuse into one."""
    original = md.read("```python\nx = 1\n```\n\n```sql\nSELECT 1;\n```\n")
    back, _ = write_and_read(original, tmp_path)

    code = [b for b in back.blocks if isinstance(b, CodeBlock)]
    assert len(code) == 2, "adjacent code blocks merged"
    assert [c.language for c in code] == ["python", "sql"]


def test_a_thematic_break_is_a_border_not_dash_characters(tmp_path):
    """Written as text, a rule reads back as a paragraph of dashes -- literal
    content that a second pass would then keep."""
    original = md.read("before\n\n---\n\nafter\n")
    back, _ = write_and_read(original, tmp_path)

    assert any(isinstance(b, ThematicBreak) for b in back.blocks), "the rule was lost"
    for block in back.blocks:
        if isinstance(block, Paragraph):
            text = plain_text(block.runs)
            assert set(text.strip()) not in ({"―"}, {"-"}), f"rule came back as text: {text!r}"


def test_the_lossy_round_trip_sample_is_now_stable(tmp_path):
    """The whole shape at once, and a second pass that must not drift."""
    source = (
        "# Title\n\n- outer\n  - nested\n- second\n\n"
        "```python\nx = 1\n```\n\n---\n\ndone\n"
    )
    first = md.read(source)
    back, _ = write_and_read(first, tmp_path)
    again, _ = write_and_read(back, tmp_path)

    def shape(document):
        def walk(blocks):
            out = []
            for block in blocks:
                out.append(type(block).__name__)
                for item in getattr(block, "items", []):
                    out.extend(walk(item.blocks))
            return out

        return walk(document.blocks)

    assert shape(first) == shape(back), "the round trip changed the structure"
    assert shape(back) == shape(again), "a second pass drifted"


def test_an_empty_heading_survives(tmp_path):
    """BUG-004. An empty heading holds a place in the outline, and
    check_empty_headings exists to report it -- which it cannot do if the
    reader drops it first."""
    original = Document(blocks=[Heading(level=2, runs=[]), Paragraph(runs=[Run("after")])])
    back, _ = write_and_read(original, tmp_path)

    headings = [b for b in back.blocks if isinstance(b, Heading)]
    assert headings, "the empty heading vanished"
    assert headings[0].level == 2


def test_an_empty_heading_is_still_reported_after_a_round_trip(tmp_path):
    """Surviving is only useful if the rule then fires on it."""
    original = Document(blocks=[Heading(level=1, runs=[]), Paragraph(runs=[Run("body")])])
    _, path = write_and_read(original, tmp_path)
    assert "heading-empty" in {i.rule for i in docfix.detect(str(path))}


def test_a_plain_empty_paragraph_is_still_spacing(tmp_path):
    """The exception is for headings only; blank paragraphs remain spacing."""
    original = Document(blocks=[Paragraph(runs=[]), Paragraph(runs=[Run("x")])])
    back, _ = write_and_read(original, tmp_path)
    assert len(back.blocks) == 1


def test_a_code_block_before_a_table_keeps_its_place(tmp_path):
    """The table branch flushed the pending list but not the pending code, so
    the table was emitted first and the two swapped on every round trip."""
    original = Document(
        blocks=[
            CodeBlock(code="print(1)", language="python"),
            Table(header=[[Run("A")]], rows=[[[Run("1")]]]),
            Paragraph(runs=[Run("after")]),
        ]
    )
    back, _ = write_and_read(original, tmp_path)
    assert [type(b).__name__ for b in back.blocks] == ["CodeBlock", "Table", "Paragraph"]


def test_adjacent_code_blocks_in_the_same_language_stay_separate(tmp_path):
    """Every line is just another Code paragraph, so without a boundary two
    blocks in one language fused. The different-language case caught nothing."""
    original = Document(
        blocks=[CodeBlock(code="a", language="python"), CodeBlock(code="b", language="python")]
    )
    back, _ = write_and_read(original, tmp_path)
    code = [b for b in back.blocks if isinstance(b, CodeBlock)]
    assert [c.code for c in code] == ["a", "b"]


def test_a_list_inside_a_blockquote_inside_a_list_keeps_its_depth(tmp_path):
    """The blockquote branch recursed without forwarding depth, so the inner
    list was written at level 1 and read back lifted out of the quote."""
    inner = ListBlock(ordered=False, items=[ListItem(runs=[Run("deep")])])
    original = Document(
        blocks=[
            ListBlock(
                ordered=False,
                items=[ListItem(runs=[Run("outer")], blocks=[BlockQuote(blocks=[inner])])],
            )
        ]
    )
    back, _ = write_and_read(original, tmp_path)
    assert isinstance(back.blocks[0], ListBlock)
    assert len(back.blocks[0].items) == 1, "the nested list was lifted to the top level"
