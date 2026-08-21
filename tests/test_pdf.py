"""PDF adapter: risk scanning, extraction, and generation.

Fixtures are generated with reportlab at test time. Committing binary PDFs
would make the diffs opaque and inflate the repo permanently.

Skipped entirely unless the PDF extras are installed (`pip install docfix[pdf]`).
"""

import os
from pathlib import Path

import pytest

# Not importorskip: a broken native dependency raises a pyo3 panic rather than
# ImportError, which importorskip does not catch, and collection would fail
# instead of skipping.
try:
    import pdfplumber
    import reportlab  # noqa: F401
except BaseException as exc:  # noqa: BLE001
    pytest.skip(f"PDF extras unavailable: {exc}", allow_module_level=True)

import docfix
from docfix.adapters import pdf as pdf_adapter
from docfix.ir import CodeBlock, Document, Heading, ListBlock, Paragraph, Run, Table
from docfix.templates import load

# --------------------------------------------------------------------------
# Fixture builders
# --------------------------------------------------------------------------


def build_pdf(path, story_spec, pagesize=None):
    """Build a PDF from a compact spec of (kind, content) pairs."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle
    from reportlab.platypus import Table as RLTable

    sheet = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle("h1", parent=sheet["Heading1"], fontSize=20),
        "h2": ParagraphStyle("h2", parent=sheet["Heading2"], fontSize=15),
        "body": ParagraphStyle("body", parent=sheet["BodyText"], fontSize=10.5),
    }

    story = []
    for kind, content in story_spec:
        if kind in styles:
            story.append(Paragraph(content, styles[kind]))
        elif kind == "table":
            table = RLTable(content)
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
            story.append(table)
        elif kind == "shapes":
            from reportlab.graphics.shapes import Circle, Drawing, Rect

            drawing = Drawing(200, 90)
            for i in range(content):
                drawing.add(Rect(i * 5, 0, 3, 10 + i, fillColor=colors.blue))
                drawing.add(Circle(i * 5, 70, 2, fillColor=colors.red))
            story.append(drawing)
        elif kind == "pagebreak":
            story.append(PageBreak())
        elif kind == "spacer":
            story.append(Spacer(1, content))

    SimpleDocTemplate(str(path), pagesize=pagesize or A4).build(story)
    return str(path)


@pytest.fixture
def simple_pdf(tmp_path):
    return build_pdf(
        tmp_path / "simple.pdf",
        [
            ("h1", "Quarterly Report"),
            ("body", "An introduction paragraph with enough words in it to wrap "
                     "onto a second line when it is laid out on the page."),
            ("h2", "Findings"),
            ("body", "1. First numbered point"),
            ("body", "2. Second numbered point"),
        ],
    )


@pytest.fixture
def table_pdf(tmp_path):
    return build_pdf(
        tmp_path / "table.pdf",
        [
            ("h1", "With A Table"),
            ("body", "Intro line."),
            ("table", [["Region", "Q1", "Q2"], ["North", "10", "12"], ["South", "8", "9"]]),
        ],
    )


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def test_scan_reports_page_count(simple_pdf):
    report = docfix.scan(simple_pdf)
    assert report.page_count == 1
    assert report.path == simple_pdf


def test_clean_text_pdf_needs_no_confirmation(simple_pdf):
    report = docfix.scan(simple_pdf)
    assert not report.needs_confirmation()
    assert "Nothing found" in report.report()


def test_tables_are_flagged_as_high_risk(table_pdf):
    report = docfix.scan(table_pdf)
    codes = {f.code for page in report.pages for f in page.factors}
    assert "table" in codes
    assert report.needs_confirmation(), "a table should prompt before converting"


def test_vector_graphics_are_flagged(tmp_path):
    path = build_pdf(tmp_path / "chart.pdf", [("h1", "Chart"), ("shapes", 30)])
    codes = {f.code for page in docfix.scan(path).pages for f in page.factors}
    assert "vector-graphics" in codes


def test_scan_scales_its_report_to_the_page_count(tmp_path):
    spec = []
    for index in range(6):
        spec += [("h1", f"Page {index}"), ("table", [["a", "b"], ["1", "2"]])]
        if index < 5:
            spec.append(("pagebreak", None))
    path = build_pdf(tmp_path / "many.pdf", spec)

    report = docfix.scan(path)
    assert report.page_count == 6
    assert len(report.risky_pages) == 6
    assert "6/6 pages" in report.report()
    # Long documents summarise instead of listing every page.
    assert "and 4 more pages" in report.report(max_pages=2)


def test_page_with_no_text_is_flagged(tmp_path):
    path = build_pdf(tmp_path / "blank.pdf", [("shapes", 5)])
    codes = {f.code for page in docfix.scan(path).pages for f in page.factors}
    assert "no-text-layer" in codes


def test_scanned_page_with_images_is_a_blocker():
    """A page that is only an image cannot be converted at all without OCR."""

    class StubPage:
        width, height = 600, 800
        images = [{"name": "scan"}]
        curves = lines = rects = annots = []

        def extract_text(self):
            return ""

        def extract_words(self, **_):
            return []

    risk = pdf_adapter._scan_page(StubPage(), 1)
    assert risk.severity == pdf_adapter.BLOCKER
    assert any(f.code == "no-text-layer" for f in risk.factors)


def test_scan_rejects_non_pdf_files(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("# hi\n")
    with pytest.raises(Exception, match="only meaningful for PDF"):
        docfix.scan(str(path))


def test_worst_severity_and_counts(table_pdf):
    report = docfix.scan(table_pdf)
    assert report.worst_severity in (pdf_adapter.BLOCKER, pdf_adapter.HIGH)
    assert report.factor_counts()


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def test_headings_are_recovered_from_font_size(simple_pdf):
    doc = pdf_adapter.read_path(simple_pdf)
    headings = [b for b in doc.blocks if isinstance(b, Heading)]
    assert headings, "font-size ranking should recover headings"
    assert headings[0].level < headings[-1].level or len(headings) == 1


def test_wrapped_lines_join_into_one_paragraph(simple_pdf):
    doc = pdf_adapter.read_path(simple_pdf)
    paragraphs = [b for b in doc.blocks if isinstance(b, Paragraph)]
    joined = " ".join(r.text for p in paragraphs for r in p.runs)
    assert "wrap onto a second line" in joined


def test_ordered_list_is_recovered(simple_pdf):
    doc = pdf_adapter.read_path(simple_pdf)
    lists = [b for b in doc.blocks if isinstance(b, ListBlock)]
    assert lists and lists[0].ordered
    assert len(lists[0].items) == 2


def test_table_is_extracted_as_a_table_not_loose_text(table_pdf):
    """Table cells must not be absorbed into the surrounding paragraph."""
    doc = pdf_adapter.read_path(table_pdf)
    tables = [b for b in doc.blocks if isinstance(b, Table)]
    assert tables, "the table should survive as a Table block"
    assert len(tables[0].header) == 3
    assert len(tables[0].rows) == 2

    prose = " ".join(
        r.text for b in doc.blocks if isinstance(b, Paragraph) for r in b.runs
    )
    assert "North" not in prose, "table cells leaked into a paragraph"


def test_undecodable_bullets_are_still_bullets(tmp_path):
    """Symbol-font bullets extract as "(cid:NNN)" with no Unicode mapping."""
    path = build_pdf(
        tmp_path / "bullets.pdf",
        [("h1", "T"), ("body", "• Alpha"), ("body", "• Beta"), ("body", "• Gamma")],
    )
    doc = pdf_adapter.read_path(path)
    lists = [b for b in doc.blocks if isinstance(b, ListBlock)]
    assert lists, "bullets should form a list even when the glyph does not decode"
    assert len(lists[0].items) == 3
    text = " ".join(r.text for item in lists[0].items for r in item.runs)
    assert "cid:" not in text
    assert "Alpha" in text


def test_running_headers_are_stripped(tmp_path):
    spec = []
    for index in range(4):
        spec += [
            ("body", "ACME CONFIDENTIAL"),
            ("h1", f"Section {index}"),
            ("body", f"Body text for section {index}."),
        ]
        if index < 3:
            spec.append(("pagebreak", None))
    path = build_pdf(tmp_path / "headers.pdf", spec)

    doc = pdf_adapter.read_path(path)
    text = " ".join(
        r.text for b in doc.blocks for r in (b.runs if hasattr(b, "runs") else [])
    )
    assert text.count("ACME CONFIDENTIAL") <= 1, "running header repeated into the body"


def test_metadata_records_the_page_count(simple_pdf):
    doc = pdf_adapter.read_path(simple_pdf)
    assert doc.meta["source_format"] == "pdf"
    assert doc.meta["page_count"] == 1


def test_dehyphenation():
    assert pdf_adapter._dehyphenate("exam-", "ple") == "example"
    assert pdf_adapter._dehyphenate("well", "known") is None
    assert pdf_adapter._dehyphenate("co-", "Operative") is None


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def test_write_produces_a_readable_pdf(tmp_path):
    doc = Document(
        blocks=[
            Heading(level=1, runs=[Run("Title")]),
            Paragraph(runs=[Run("Body text with "), Run("bold", bold=True)]),
            CodeBlock(code="x = 1"),
        ]
    )
    out = tmp_path / "generated.pdf"
    pdf_adapter.write_path(doc, load("formal"), str(out))

    assert out.exists() and out.stat().st_size > 0
    with pdfplumber.open(str(out)) as opened:
        text = opened.pages[0].extract_text()
    assert "Title" in text
    assert "Body text with bold" in text


def test_table_survives_generation(tmp_path):
    doc = Document(
        blocks=[
            Table(
                header=[[Run("A")], [Run("B")]],
                rows=[[[Run("1")], [Run("2")]]],
                alignments=[None, None],
            )
        ]
    )
    out = tmp_path / "t.pdf"
    pdf_adapter.write_path(doc, load("minimal"), str(out))
    with pdfplumber.open(str(out)) as opened:
        assert opened.pages[0].find_tables()


def test_xml_special_characters_are_escaped(tmp_path):
    doc = Document(blocks=[Paragraph(runs=[Run("a < b & c > d")])])
    out = tmp_path / "esc.pdf"
    pdf_adapter.write_path(doc, load("minimal"), str(out))
    with pdfplumber.open(str(out)) as opened:
        assert "a < b & c > d" in opened.pages[0].extract_text()


@pytest.mark.parametrize(
    "family, expected",
    [
        ("Georgia, serif", "Times-Roman"),
        ("'Inter', sans-serif", "Helvetica"),
        ("'JetBrains Mono', monospace", "Courier"),
        ("", "Helvetica"),
    ],
)
def test_font_stacks_map_onto_the_base_14(family, expected):
    assert pdf_adapter._base_font(family) == expected


def test_every_preset_can_generate(tmp_path):
    doc = Document(blocks=[Heading(level=1, runs=[Run("H")]), Paragraph(runs=[Run("p")])])
    for name in docfix.list_templates():
        out = tmp_path / f"{name}.pdf"
        pdf_adapter.write_path(doc, load(name), str(out))
        assert out.stat().st_size > 0


# --------------------------------------------------------------------------
# Round trip through the public API
# --------------------------------------------------------------------------


def test_format_file_round_trips_a_pdf(table_pdf, tmp_path):
    out = tmp_path / "clean.pdf"
    before = Path(table_pdf).read_bytes()

    result = docfix.format_file(table_pdf, template="formal", output=str(out))

    assert out.exists()
    assert Path(table_pdf).read_bytes() == before, "the source PDF must not be modified"
    assert result.output_path == str(out)

    with pdfplumber.open(str(out)) as opened:
        text = opened.pages[0].extract_text()
    assert "With A Table" in text
    assert "North" in text


def test_keep_intermediate_writes_inspectable_markdown(table_pdf, tmp_path):
    out = tmp_path / "clean.pdf"
    md = tmp_path / "extracted.md"
    result = docfix.format_file(
        table_pdf, output=str(out), keep_intermediate=str(md)
    )
    assert result.intermediate_path == str(md)
    content = md.read_text()
    assert content.startswith("#")
    assert "| Region" in content, "the table should be readable in the intermediate"


def test_keep_intermediate_derives_a_default_name(table_pdf, tmp_path):
    out = tmp_path / "clean.pdf"
    result = docfix.format_file(table_pdf, output=str(out), keep_intermediate=True)
    assert result.intermediate_path.endswith(".extracted.md")
    assert os.path.exists(result.intermediate_path)


def test_pdf_skips_the_source_level_rules(simple_pdf):
    """PDF has no raw text source, so whitespace rules cannot apply."""
    issues = docfix.detect(simple_pdf)
    assert all(not i.rule.startswith("whitespace-") for i in issues)


# --------------------------------------------------------------------------
# CLI: the confirmation flow
# --------------------------------------------------------------------------


def test_cli_scan_prints_a_report(table_pdf, capsys):
    from docfix.cli import EXIT_OK, main

    assert main(["scan", table_pdf]) == EXIT_OK
    out = capsys.readouterr().out
    assert "1 page" in out
    assert "table" in out.lower()


def test_cli_scan_exits_1_on_a_blocker(monkeypatch, table_pdf, capsys):
    """A page with no text layer is a blocker, so scan reports failure."""
    from docfix.adapters.pdf import BLOCKER, PageRisk, PdfScan, RiskFactor
    from docfix.cli import EXIT_ISSUES, main

    def fake_scan(path):
        return PdfScan(
            path=path,
            page_count=1,
            pages=[PageRisk(1, [RiskFactor("no-text-layer", BLOCKER, "scanned page")])],
        )

    monkeypatch.setattr(docfix, "scan", fake_scan)
    assert main(["scan", table_pdf]) == EXIT_ISSUES


def test_cli_refuses_a_lossy_conversion_when_unattended(table_pdf, tmp_path, capsys):
    """Without a tty and without --yes, converting must not happen silently."""
    from docfix.cli import EXIT_ERROR, main

    out = tmp_path / "out.pdf"
    assert main(["format", table_pdf, "-o", str(out)]) == EXIT_ERROR
    assert not out.exists(), "nothing should be written when the user did not agree"

    err = capsys.readouterr().err
    assert "cannot restyle a PDF in place" in err
    assert "--yes" in err
    assert "--keep-intermediate" in err, "the tip should point at the inspectable path"


def test_cli_yes_accepts_the_losses(table_pdf, tmp_path):
    from docfix.cli import EXIT_OK, main

    out = tmp_path / "out.pdf"
    assert main(["format", table_pdf, "-o", str(out), "--yes"]) == EXIT_OK
    assert out.exists()


def test_cli_does_not_prompt_for_a_clean_pdf(simple_pdf, tmp_path, capsys):
    from docfix.cli import EXIT_OK, main

    out = tmp_path / "out.pdf"
    assert main(["format", simple_pdf, "-o", str(out)]) == EXIT_OK
    assert "Convert anyway" not in capsys.readouterr().err


def test_cli_never_prompts_for_markdown(tmp_path, capsys):
    """The confirmation is PDF-specific; Markdown conversion loses nothing."""
    from docfix.cli import EXIT_OK, main

    source = tmp_path / "a.md"
    source.write_text("# T\n\n* a\n+ b\n")
    assert main(["format", str(source), "-o", str(tmp_path / "o.md")]) == EXIT_OK
    assert "cannot restyle" not in capsys.readouterr().err


def test_missing_dependency_message_names_the_extra():
    from docfix.adapters.pdf import MissingDependencyError, _require

    with pytest.raises(MissingDependencyError, match=r'pip install "docfix\[pdf\]"'):
        _require("a_module_that_is_not_installed")
