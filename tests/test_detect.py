"""Every rule must fire on the problem it targets and stay quiet otherwise."""

import pytest

from docfix.adapters import markdown as md
from docfix.detect.rules import ERROR, INFO, WARNING, Issue, run_all
from docfix.ir import Document, Run, Table


def fired(text):
    return {issue.rule for issue in run_all(md.read(text), text)}


CLEAN = "# Title\n\nA paragraph.\n\n- a\n- b\n\n![alt](p.png)\n"


def test_clean_document_reports_nothing():
    assert fired(CLEAN) == set()


@pytest.mark.parametrize(
    "rule, text",
    [
        ("heading-skip", "# One\n\n#### Four\n"),
        ("heading-multiple-h1", "# One\n\n# Two\n"),
        ("heading-empty", "# \n\ntext\n"),
        ("list-mixed-markers", "* a\n+ b\n- c\n"),
        ("image-missing-alt", "![](p.png)\n"),
        ("date-inconsistent", "Started 2024-01-15 and left 03/04/2025.\n"),
        ("whitespace-trailing", "text   \n"),
        ("whitespace-blank-run", "a\n\n\n\nb\n"),
        ("whitespace-tab-indent", "\tindented\n"),
        ("quotes-mixed", 'He said “hi” and "bye".\n'),
    ],
)
def test_rule_fires_on_its_problem(rule, text):
    assert rule in fired(text)


@pytest.mark.parametrize(
    "rule, text",
    [
        ("heading-skip", "# One\n\n## Two\n\n### Three\n"),
        ("heading-multiple-h1", "# One\n\n## Two\n"),
        ("list-mixed-markers", "- a\n- b\n- c\n"),
        ("image-missing-alt", "![alt](p.png)\n"),
        ("date-inconsistent", "Started 2024-01-15 and left 2025-03-04.\n"),
        ("whitespace-trailing", "text\n"),
        ("whitespace-blank-run", "a\n\nb\n"),
        ("quotes-mixed", "All “curly” here.\n"),
    ],
)
def test_rule_stays_quiet_when_clean(rule, text):
    assert rule not in fired(text)


def test_headings_may_descend_more_than_one_level_at_a_time():
    """Going h3 -> h2 is fine; only *skipping down* is a problem."""
    assert "heading-skip" not in fired("# A\n\n## B\n\n### C\n\n## D\n")


def test_ragged_table_row_is_an_error():
    """Built from the IR directly: GFM pads short rows to the header width, so a
    ragged table cannot reach the IR through Markdown. The rule guards the
    adapters that can produce one -- DOCX -- and hand-built documents."""
    table = Table(
        header=[[Run("a")], [Run("b")]],
        rows=[[[Run("1")]]],
        alignments=[None, None],
    )
    issues = run_all(Document(blocks=[table]))
    ragged = [i for i in issues if i.rule == "table-ragged-row"]
    assert ragged and ragged[0].severity == ERROR


def test_markdown_tables_are_normalized_to_header_width():
    """Confirms the assumption above rather than leaving it implicit."""
    for source in ("| a | b |\n|---|---|\n| 1 |\n", "| a | b |\n|---|---|\n| 1 | 2 | 3 |\n"):
        table = [b for b in md.read(source).blocks if isinstance(b, Table)][0]
        assert all(len(row) == len(table.header) for row in table.rows)


def test_code_block_contents_are_exempt_from_source_rules():
    """Trailing spaces and tabs inside a fence are the author's business."""
    text = "```\ncode   \n\ttabbed\n```\n"
    assert "whitespace-trailing" not in fired(text)
    assert "whitespace-tab-indent" not in fired(text)


def test_dates_inside_inline_code_are_not_compared():
    text = "Use `2024-01-15` or `01/15/2024` as the format string.\n"
    assert "date-inconsistent" not in fired(text)


def test_issues_report_line_numbers_where_known():
    issues = run_all(md.read("ok\ntext   \n"), "ok\ntext   \n")
    trailing = [i for i in issues if i.rule == "whitespace-trailing"]
    assert trailing and trailing[0].line == 2


def test_source_rules_are_skipped_without_source():
    """Binary formats have no raw source; only structure rules should run."""
    assert "whitespace-trailing" not in {i.rule for i in run_all(md.read("text   \n"))}


def test_issues_are_sorted_by_line():
    issues = run_all(md.read("a   \nb\nc   \n"), "a   \nb\nc   \n")
    lines = [i.line for i in issues if i.line]
    assert lines == sorted(lines)


def test_auto_fixable_flag_matches_what_normalization_repairs():
    issues = run_all(md.read("* a\n+ b\n"), "* a\n+ b\n")
    marker = [i for i in issues if i.rule == "list-mixed-markers"]
    assert marker and marker[0].auto_fixable
    # Structural problems are reported, never silently rewritten.
    skip = run_all(md.read("# A\n\n#### D\n"))
    assert all(not i.auto_fixable for i in skip if i.rule == "heading-skip")


def test_issue_renders_readably():
    assert "line 3" in str(Issue("r", "m", WARNING, line=3))
    assert "document" in str(Issue("r", "m", INFO))
