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
    Document,
    Heading,
    ListBlock,
    Paragraph,
    Run,
    Table,
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
