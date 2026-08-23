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
        ("Georgia, serif", "serif"),
        ("'Inter', sans-serif", "sans"),
        ("'JetBrains Mono', monospace", "mono"),
        ("", "sans"),
    ],
)
def test_font_stacks_map_onto_a_category(family, expected):
    """Guards the "sans-serif contains serif" trap in the last-resort path."""
    assert pdf_adapter._category(family) == expected


def test_base14_fallback_uses_the_right_face():
    from docfix.fonts.coverage import base14_option

    assert base14_option("serif").name == "Times-Roman"
    assert base14_option("mono").name == "Courier"
    assert base14_option("sans").name == "Helvetica"


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


# --------------------------------------------------------------------------
# Font coverage: the regression this whole font pool exists to prevent
# --------------------------------------------------------------------------

SCRIPT_SAMPLES = {
    "latin": "Hello world",
    "latin-ext": "Zażółć gęślą jaźń",
    "cyrillic": "Привет мир",
    "greek": "Γειά σου κόσμε",
    "symbols": "→ ★ ½ € ≈ ✓",
    "hebrew": "שלום עולם",
    "cjk-japanese": "日本語のテキスト",
    "cjk-chinese": "你好世界",
    "cjk-korean": "한국어",
}


def _render_lines(texts, tmp_path, template="formal"):
    """Write each string as its own paragraph and read the PDF back."""
    import warnings

    doc = Document(blocks=[Paragraph(runs=[Run(text)]) for text in texts])
    out = tmp_path / "scripts.pdf"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pdf_adapter.write_path(doc, load(template), str(out))
    with pdfplumber.open(str(out)) as opened:
        return (opened.pages[0].extract_text() or "").splitlines()


def _unrenderable(text, template="formal"):
    """Characters no font on *this* machine can render.

    Taken from the resolver itself rather than by string-matching the reported
    message -- the message contains spaces, so substring matching would count
    the space character as unrenderable.
    """
    context = pdf_adapter.build_font_context(load(template))
    pdf_adapter._markup([Run(text)], context)
    return set(context.missing)


@pytest.mark.parametrize("script", sorted(SCRIPT_SAMPLES))
def test_every_script_round_trips_or_is_reported(script, tmp_path):
    """The base-14 fonts silently corrupted all of these.

    Polish became "Zanónn gnnln jann"; "→ ★ ≈ ✓" became "fi H » 3"; Cyrillic and
    CJK became boxes -- with no error and no warning.

    The contract is not "everything always renders" -- that depends on which
    fonts a machine has, and a bare CI runner has few. It is that a character
    **either renders exactly or is reported as unrenderable**. Never silently
    wrong, which is what the base-14 did.
    """
    text = SCRIPT_SAMPLES[script]
    lines = _render_lines([text], tmp_path)
    if not lines:
        pytest.skip("no font on this machine can render this script")

    missing = _unrenderable(text)
    if not missing:
        assert lines[0] == text
        return

    # Whatever could not be rendered was reported; everything else is intact.
    survived = "".join(char for char in text if char not in missing)
    got = lines[0].replace("\x00", "")
    assert got == survived, f"characters were corrupted rather than reported: {missing}"


def test_mixed_script_line_keeps_every_run(tmp_path):
    """reportlab emits \\x00 for a missing glyph and warns about nothing.

    A single line mixing scripts is the case per-span font assignment exists
    for: one font cannot cover it, and there is no automatic fallback.
    """
    text = "English Привет 你好世界 end"
    lines = _render_lines([text], tmp_path)
    assert lines and "\x00" not in lines[0]
    assert lines[0] == text


def test_no_notdef_for_anything_the_fonts_do_cover(tmp_path):
    """A dropped glyph is only acceptable where docfix named that character.

    Asserting merely that *something* was reported would pass even if the
    resolver named one character and reportlab dropped a different one -- which
    is exactly the silent-corruption failure the font pool exists to prevent.
    Each rendered line is checked against its own sample.
    """
    samples = list(SCRIPT_SAMPLES.values())
    lines = _render_lines(samples, tmp_path)
    assert len(lines) == len(samples), "a sample failed to render at all"

    for sample, line in zip(samples, lines, strict=True):
        missing = _unrenderable(sample)
        survived = "".join(char for char in sample if char not in missing)
        assert line.replace("\x00", "") == survived, (
            f"dropped glyphs do not match what was reported for {sample!r}"
        )


def test_unrenderable_characters_are_reported_not_dropped(tmp_path):
    """No available font has colour emoji, so it must be reported, not silent."""
    doc = Document(blocks=[Paragraph(runs=[Run("party 🎉 time")])])
    issues = pdf_adapter.coverage_issues(doc, load("minimal"))
    coverage = [i for i in issues if i.rule == "font-coverage"]
    assert coverage, "an unrenderable character must produce an issue"
    assert "🎉" in coverage[0].message
    assert coverage[0].severity == "error"

    # And the file is still written -- reporting, not refusing.
    out = tmp_path / "emoji.pdf"
    pdf_adapter.write_path(doc, load("minimal"), str(out))
    assert out.exists()


def test_plain_latin_reports_no_coverage_problem():
    doc = Document(blocks=[Paragraph(runs=[Run("Ordinary English text.")])])
    issues = pdf_adapter.coverage_issues(doc, load("minimal"))
    assert not [i for i in issues if i.rule == "font-coverage"]


def test_coverage_issues_reach_the_format_result(table_pdf, tmp_path):
    """The adapter's coverage report must surface through the public API."""
    result = docfix.format_file(table_pdf, output=str(tmp_path / "o.pdf"))
    assert isinstance(result.issues, list)  # merged, not dropped


def test_bold_uses_the_real_bold_face(tmp_path):
    """A <font face> overrides the family mapping, so the span must name the
    bold face itself or the weight is silently lost."""
    context = pdf_adapter.build_font_context(load("minimal"))
    markup = pdf_adapter._markup([Run("bold text", bold=True)], context)
    primary = context.body[0]
    if primary.use_tags:
        assert "<b>" in markup  # base-14 path: reportlab maps the weight itself
    else:
        face, _exact = primary.name_for(bold=True)
        assert f'face="{face}"' in markup
        assert "-bold" in face or primary.name == face


def test_missing_bold_face_degrades_and_is_reported():
    from docfix.fonts.registry import Family, License, face_name
    from docfix.fonts.sfnt import TRUETYPE, FontInfo

    regular = FontInfo("r.ttf", "OneFace", "Regular", TRUETYPE, 0)
    family = Family(
        name="OneFace",
        category="sans",
        license=License("OFL-1.1", "SIL OFL", True),
        faces={"regular": regular},
    )
    name, exact = face_name(family, bold=True)
    assert name == "OneFace", "must fall back to the regular face"
    assert not exact, "and say that it is not the face asked for"


def test_font_context_survives_a_template_naming_unknown_fonts():
    """A template asking for fonts this machine lacks must still produce output."""
    from docfix.templates import from_dict

    template = from_dict(
        {"name": "t", "fonts": {"body": {"family": "Nonexistent Font, Also Fake"}}}
    )
    context = pdf_adapter.build_font_context(template)
    assert context.body, "a chain must always be produced"
    assert "Nonexistent Font" in context.unavailable


def test_font_stack_alternatives_are_not_reported_individually():
    """A stack is a list of alternatives; a missing entry is not an error when
    a later one resolves."""
    from docfix.fonts import pool
    from docfix.templates import from_dict

    installed = [f.name for f in pool().usable_families()]
    if not installed:
        pytest.skip("no fonts installed")
    template = from_dict(
        {"name": "t", "fonts": {"body": {"family": f"Nonexistent Font, {installed[0]}"}}}
    )
    context = pdf_adapter.build_font_context(template)
    assert not context.unavailable, "a resolved alternative means nothing to report"


def test_cjk_needs_no_font_file(tmp_path):
    """CID collections are built into reportlab: no file shipped, none downloaded."""
    lines = _render_lines(["日本語 你好 한국어"], tmp_path)
    assert lines and "\x00" not in lines[0]


# --------------------------------------------------------------------------
# --embed-cjk: self-contained CJK output
# --------------------------------------------------------------------------


def _font_embedding(path):
    """(base font name -> whether its glyphs are embedded) for page 1."""
    pypdf = pytest.importorskip("pypdf")
    page = pypdf.PdfReader(str(path)).pages[0]
    fonts = page["/Resources"].get("/Font", {})
    out = {}
    for key in fonts:
        font = fonts[key].get_object()
        descriptor = font.get("/FontDescriptor")
        if descriptor is None and font.get("/DescendantFonts"):
            descriptor = font["/DescendantFonts"][0].get_object().get("/FontDescriptor")
        out[str(font.get("/BaseFont"))] = bool(
            descriptor
            and any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
        )
    return out


def _cjk_doc():
    return Document(blocks=[Paragraph(runs=[Run("日本語 你好世界 한국어")])])


def _has_embeddable_cjk():
    from docfix.fonts import pool
    from docfix.fonts.coverage import embeddable_cjk

    return bool(embeddable_cjk(pool()))


def test_default_cjk_uses_unembedded_cid_fonts(tmp_path):
    """The default costs no font file, but the reader supplies the glyphs."""
    out = tmp_path / "cid.pdf"
    pdf_adapter.write_path(_cjk_doc(), load("formal"), str(out))
    embedding = _font_embedding(out)
    cid = [name for name in embedding if "Heisei" in name or "HYSMyeongJo" in name]
    assert cid, "the CID collections should be doing the CJK work by default"
    assert not any(embedding[name] for name in cid), "CID fonts are not embedded"


def test_embed_cjk_produces_a_self_contained_pdf(tmp_path):
    if not _has_embeddable_cjk():
        pytest.skip("no open-licensed CJK font installed")

    from docfix.templates import from_dict

    template = load("formal")
    embedded_template = from_dict(
        {**{"name": "e"}, "fonts": {**template.fonts, "embed_cjk": True}}
    )
    out = tmp_path / "embedded.pdf"
    pdf_adapter.write_path(_cjk_doc(), embedded_template, str(out))

    embedding = _font_embedding(out)
    assert any(embedding.values()), "at least one font must carry its own glyphs"
    assert not [n for n in embedding if "Heisei" in n or "HYSMyeongJo" in n], (
        "an embedded CJK font should replace the CID collections, not sit beside them"
    )


def test_embed_cjk_still_renders_every_cjk_script(tmp_path):
    if not _has_embeddable_cjk():
        pytest.skip("no open-licensed CJK font installed")
    from docfix.templates import from_dict

    template = from_dict(
        {"name": "e", "fonts": {**load("formal").fonts, "embed_cjk": True}}
    )
    out = tmp_path / "e.pdf"
    pdf_adapter.write_path(_cjk_doc(), template, str(out))
    with pdfplumber.open(str(out)) as opened:
        text = opened.pages[0].extract_text()
    assert "日本語" in text and "你好世界" in text and "한국어" in text
    assert "\x00" not in text


def test_embed_cjk_only_ever_uses_an_open_licensed_font():
    """The licence gate is not relaxed for the sake of self-containment."""
    from docfix.fonts import pool
    from docfix.fonts.coverage import embeddable_cjk

    for family, _scripts in embeddable_cjk(pool()):
        assert family.license.open, f"{family.name} embedded without an open licence"
        assert family.embeddable, f"{family.name} embedded despite its fsType"


def test_embed_cjk_falls_back_rather_than_failing(tmp_path):
    """Asking for it must never make CJK worse than the default."""
    from docfix.templates import from_dict

    template = from_dict(
        {"name": "e", "fonts": {**load("minimal").fonts, "embed_cjk": True}}
    )
    out = tmp_path / "fb.pdf"
    pdf_adapter.write_path(_cjk_doc(), template, str(out))
    with pdfplumber.open(str(out)) as opened:
        assert "日本語" in (opened.pages[0].extract_text() or "")


def test_unavailable_embed_cjk_is_reported(monkeypatch):
    """When nothing qualifies, say so rather than silently using CID."""
    from docfix.templates import from_dict

    monkeypatch.setattr("docfix.fonts.coverage.embeddable_cjk", lambda _pool: [])
    template = from_dict(
        {"name": "e", "fonts": {**load("formal").fonts, "embed_cjk": True}}
    )
    issues = pdf_adapter.coverage_issues(_cjk_doc(), template)
    assert [i for i in issues if i.rule == "font-embed-cjk-unavailable"]


def test_embed_cjk_flag_reaches_the_api(tmp_path):
    source = tmp_path / "a.md"
    source.write_text("# T\n\n日本語\n")
    out = tmp_path / "a.pdf"
    result = docfix.format_file(str(source), output=str(out), embed_cjk=True)
    assert out.exists() and result.output_path == str(out)


def test_embed_cjk_does_not_leak_into_the_shared_template(tmp_path):
    """Presets are shared objects; a per-call flag must not mutate them."""
    source = tmp_path / "a.md"
    source.write_text("# T\n")
    docfix.format_file(str(source), output=str(tmp_path / "a.pdf"), embed_cjk=True)
    assert not load("formal").fonts.get("embed_cjk")
