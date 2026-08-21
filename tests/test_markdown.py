"""Markdown adapter: structure on read, canonical form on write.

The two properties that matter most for a formatter are **idempotence**
(formatting twice changes nothing the second time) and **round-trip stability**
(the written form parses back to the same structure).
"""

import pytest

from docfix.adapters import markdown as md
from docfix.fix.normalize import normalize
from docfix.ir import (
    BlockQuote,
    CodeBlock,
    Document,
    Heading,
    Image,
    ListBlock,
    Paragraph,
    Table,
    plain_text,
)
from docfix.templates import load

SAMPLES = {
    "headings": "# One\n\n## Two\n\n### Three\n",
    "emphasis": "Plain *italic* **bold** `code` ~~struck~~ and [a link](http://x.test).\n",
    "bullets": "- a\n- b\n- c\n",
    "ordered": "1. first\n2. second\n3. third\n",
    "nested": "- outer\n\n  - inner\n  - inner two\n",
    "quote": "> quoted **text**\n",
    "code": "```python\nx = 1\n```\n",
    "table": "| a   | b   |\n| --- | --- |\n| 1   | 2   |\n",
    "image": "![alt text](pic.png)\n",
    "rule": "---\n",
    "mixed": "# Title\n\nText.\n\n- a\n- b\n\n## Next\n\n1. x\n",
}


def fmt(text, template="minimal"):
    return md.write(normalize(md.read(text)), load(template))


@pytest.mark.parametrize("name", sorted(SAMPLES))
@pytest.mark.parametrize("template", ["minimal", "formal", "friendly", "technical"])
def test_formatting_is_idempotent(name, template):
    once = fmt(SAMPLES[name], template)
    twice = fmt(once, template)
    assert once == twice, f"{name}/{template} kept changing on a second pass"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_round_trip_preserves_structure(name):
    """write(read(x)) must parse back to the same block structure."""
    original = normalize(md.read(SAMPLES[name]))
    reparsed = normalize(md.read(md.write(original, load("minimal"))))
    assert [type(b).__name__ for b in original.blocks] == [
        type(b).__name__ for b in reparsed.blocks
    ]


def test_already_canonical_text_is_unchanged():
    for text in ("# Title\n", "- a\n- b\n", "Just a paragraph.\n"):
        assert fmt(text) == text


def test_heading_levels_and_text():
    doc = md.read("# One\n\n### Three\n")
    headings = [b for b in doc.blocks if isinstance(b, Heading)]
    assert [h.level for h in headings] == [1, 3]
    assert plain_text(headings[0].runs) == "One"


def test_inline_marks_are_captured():
    doc = md.read("a *i* **b** `c` ~~s~~ [l](http://x.test)\n")
    runs = doc.blocks[0].runs
    assert any(r.italic for r in runs)
    assert any(r.bold for r in runs)
    assert any(r.code for r in runs)
    assert any(r.strike for r in runs)
    assert any(r.link == "http://x.test" for r in runs)


def test_marker_split_lists_merge_into_one():
    """`*`/`+`/`-` mixing parses as three lists; normalization merges them."""
    doc = md.read("* a\n+ b\n- c\n")
    assert sum(isinstance(b, ListBlock) for b in doc.blocks) == 3
    merged = normalize(doc)
    lists = [b for b in merged.blocks if isinstance(b, ListBlock)]
    assert len(lists) == 1
    assert len(lists[0].items) == 3
    assert fmt("* a\n+ b\n- c\n") == "- a\n- b\n- c\n"


def test_template_controls_the_bullet_marker(tmp_path):
    path = tmp_path / "star.yaml"
    path.write_text("name: star\nmarkdown:\n  bullet_marker: '*'\n")
    assert fmt("- a\n- b\n", str(path)) == "* a\n* b\n"


def test_template_controls_the_emphasis_marker():
    assert fmt("*word*\n", "formal") == "*word*\n"
    assert fmt("*word*\n", "friendly") == "_word_\n"


def test_block_types_are_recognized():
    doc = md.read(
        "# H\n\npara\n\n- l\n\n> q\n\n```\nc\n```\n\n| a |\n|---|\n| 1 |\n\n![x](i.png)\n\n---\n"
    )
    kinds = {type(b).__name__ for b in doc.blocks}
    assert {
        "Heading", "Paragraph", "ListBlock", "BlockQuote",
        "CodeBlock", "Table", "Image", "ThematicBreak",
    } <= kinds


def test_code_block_content_and_language_survive():
    doc = md.read("```python\nx = 1\ny = 2\n```\n")
    block = doc.blocks[0]
    assert isinstance(block, CodeBlock)
    assert block.language == "python"
    assert block.code == "x = 1\ny = 2"


def test_code_block_content_is_never_reformatted():
    source = "```\n-  weird   spacing\n\n\n   kept\n```\n"
    assert "-  weird   spacing" in fmt(source)


def test_fence_widens_when_code_contains_a_fence():
    doc = Document(blocks=[CodeBlock(code="```\ninner\n```", language=None)])
    out = md.write(doc, load("minimal"))
    assert out.startswith("````")


def test_standalone_image_becomes_an_image_block():
    doc = md.read("![alt](pic.png)\n")
    assert isinstance(doc.blocks[0], Image)
    assert doc.blocks[0].alt == "alt"
    assert doc.blocks[0].src == "pic.png"


def test_inline_image_stays_inside_its_paragraph():
    doc = md.read("text ![alt](pic.png) more\n")
    assert isinstance(doc.blocks[0], Paragraph)
    assert "![alt](pic.png)" in md.write(doc, load("minimal"))


def test_table_alignment_is_preserved():
    doc = md.read("| a | b | c |\n|:--|:-:|--:|\n| 1 | 2 | 3 |\n")
    table = doc.blocks[0]
    assert isinstance(table, Table)
    assert table.alignments == ["left", "center", "right"]
    out = md.write(doc, load("minimal"))
    assert ":--" in out and "--:" in out


def test_blockquote_nesting_is_preserved():
    doc = md.read("> outer\n>\n> > inner\n")
    quote = doc.blocks[0]
    assert isinstance(quote, BlockQuote)
    assert any(isinstance(b, BlockQuote) for b in quote.blocks)


def test_nested_list_is_indented_and_reparses():
    out = fmt("- outer\n\n  - inner\n")
    doc = md.read(out)
    lists = [b for b in doc.blocks if isinstance(b, ListBlock)]
    assert lists and lists[0].items[0].blocks


def test_ordered_list_numbering_is_sequential():
    assert fmt("1. a\n1. b\n1. c\n") == "1. a\n2. b\n3. c\n"


def test_wrap_width_reflows_prose_only():
    long_text = "word " * 40
    out = fmt(long_text.strip() + "\n", "technical")  # technical wraps at 80
    assert all(len(line) <= 80 for line in out.splitlines())
    # A template with wrap_width 0 leaves the line alone.
    assert len(fmt(long_text.strip() + "\n", "minimal").splitlines()) == 1


def test_empty_document_produces_empty_output():
    assert fmt("") == ""
    assert fmt("\n\n\n") == ""


def test_trailing_whitespace_and_blank_runs_are_removed():
    out = fmt("# Title   \n\n\n\ntext   \n")
    assert out == "# Title\n\ntext\n"


def test_output_ends_with_exactly_one_newline():
    for sample in SAMPLES.values():
        out = fmt(sample)
        assert out.endswith("\n")
        assert not out.endswith("\n\n")
