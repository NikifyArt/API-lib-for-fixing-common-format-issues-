"""The CV layer: section model, date parsing, and the résumé rules.

Every rule here is report-only, so the tests check what is *said*, never a
rewrite. A CV is a template category rather than a format, so there is no
adapter to exercise -- these run on the IR like any other rule family.
"""

import pytest

import docfix
from docfix import cv
from docfix.adapters import markdown as md

GOOD = """# Ada Lovelace

ada@example.com · +44 20 7946 0000 · https://github.com/ada

## Summary

Systems engineer with a decade building numerical software.

## Experience

### Principal Engineer, Analytical Engines Ltd — 2021–Present

- Led the compiler team through a full rewrite
- Cut build times by 60%

### Senior Engineer, Difference Co — 2016–2021

- Designed the instruction scheduler

## Education

### MSc Mathematics, University of London — 2014–2016

## Skills

Python, Rust, C++
"""

BAD = """# Charles Babbage

## Experience

### Junior Analyst, Somewhere — 2015–2018

- Responsible for maintaining the ledger.
- I worked on the reporting pipeline

### Lead Analyst, Elsewhere — 2019–2023

- Built the forecasting model

## Summary

Analyst.

## Skills

Ledgers
"""


def rules(text):
    return {issue.rule for issue in cv.check(md.read(text))}


# --------------------------------------------------------------------------
# Recognising a CV
# --------------------------------------------------------------------------


def test_a_real_cv_is_recognised():
    assert cv.looks_like_cv(md.read(GOOD))


@pytest.mark.parametrize(
    "label, text",
    [
        ("readme", "# Project\n\n## Install\n\n## Usage\n\n## Skills\n"),
        ("report", "# Q3 Report\n\n## Summary\n\n## Findings\n"),
        ("one section", "# Me\n\n## Experience\n\n### R — 2020\n"),
        ("no headings", "Just a paragraph of prose.\n"),
        ("empty", ""),
    ],
)
def test_non_cvs_are_not_recognised(label, text):
    """Deliberately conservative -- a README with a Skills heading is not a CV,
    and running résumé rules over one would be noise."""
    assert not cv.looks_like_cv(md.read(text))


@pytest.mark.parametrize(
    "heading, kind",
    [
        ("Experience", "experience"),
        ("Work History", "experience"),
        ("Employment", "experience"),
        ("Education", "education"),
        ("Technical Skills", "skills"),
        ("Profile", "summary"),
        ("Publications", "publications"),
        ("Nonsense Heading", None),
    ],
)
def test_section_headings_are_classified(heading, kind):
    assert cv.classify(heading) == kind


def test_sections_are_found_in_order():
    found = cv.sections(md.read(GOOD))
    assert [s.kind for s in found] == ["summary", "experience", "education", "skills"]
    assert [s.title for s in found][0] == "Summary"


def test_entries_are_collected_under_their_section():
    experience = next(s for s in cv.sections(md.read(GOOD)) if s.kind == "experience")
    assert len(experience.entries) == 2
    assert experience.entries[0].ongoing is True
    assert experience.entries[1].start == 2016


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Role — 2020–2023", (2020, 2023, False)),
        ("Role — 2020 - 2023", (2020, 2023, False)),
        ("Role — 2020 to 2023", (2020, 2023, False)),
        ("Role — Jan 2020 – Mar 2023", (2020, 2023, False)),
        ("Role — 2021–Present", (2021, None, True)),
        ("Role — 2021 - Current", (2021, None, True)),
        ("Role — 2019", (2019, 2019, False)),
        ("Role with no dates", (None, None, False)),
    ],
)
def test_date_ranges_are_parsed(text, expected):
    assert cv.date_range(text) == expected


def test_an_ongoing_entry_outranks_every_finished_one():
    ongoing = cv.Entry("now", start=2010, ongoing=True)
    recent = cv.Entry("recent", start=2022, end=2024)
    assert ongoing.sort_key > recent.sort_key


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def test_a_well_formed_cv_reports_nothing():
    assert rules(GOOD) == set()


@pytest.mark.parametrize(
    "rule",
    [
        "cv-missing-contact",
        "cv-not-reverse-chronological",
        "cv-bullet-punctuation",
        "cv-first-person",
        "cv-weak-opener",
        "cv-summary-placement",
    ],
)
def test_each_problem_is_reported(rule):
    assert rule in rules(BAD)


def test_missing_experience_and_education_is_reported():
    text = "# Me\n\nme@x.test\n\n## Skills\n\nThings\n\n## Interests\n\nMore things\n"
    assert "cv-missing-section" in {i.rule for i in cv.check(md.read(text))}


def test_reverse_chronological_order_passes():
    text = (
        "# Me\n\nme@x.test\n\n## Experience\n\n"
        "### Recent — 2022–Present\n\n- did\n\n"
        "### Older — 2018–2022\n\n- did\n\n"
        "## Education\n\n### Deg — 2016\n"
    )
    assert "cv-not-reverse-chronological" not in rules(text)


def test_contact_details_are_accepted_in_several_forms():
    for contact in ("me@x.test", "https://github.com/me", "+44 20 7946 0000"):
        text = f"# Me\n\n{contact}\n\n## Experience\n\n### R — 2020\n\n## Education\n\n### D — 2016\n"
        assert "cv-missing-contact" not in rules(text), contact


def test_a_contact_link_counts_even_without_visible_text():
    text = (
        "# Me\n\n[profile](https://linkedin.com/in/me)\n\n"
        "## Experience\n\n### R — 2020\n\n## Education\n\n### D — 2016\n"
    )
    assert "cv-missing-contact" not in rules(text)


def test_consistent_bullet_punctuation_passes():
    for ending in (".", ""):
        bullets = "\n".join(f"- did a thing{ending}" for _ in range(4))
        text = f"# Me\n\nme@x.test\n\n## Experience\n\n### R — 2020\n\n{bullets}\n\n## Education\n\n### D — 2016\n"
        assert "cv-bullet-punctuation" not in rules(text)


def test_too_few_bullets_to_judge_punctuation():
    text = "# Me\n\nme@x.test\n\n## Experience\n\n### R — 2020\n\n- one.\n- two\n\n## Education\n\n### D — 2016\n"
    assert "cv-bullet-punctuation" not in rules(text)


def test_long_bullets_are_reported():
    long_text = "Delivered " + "many important outcomes across several teams " * 6
    text = f"# Me\n\nme@x.test\n\n## Experience\n\n### R — 2020\n\n- {long_text}\n\n## Education\n\n### D — 2016\n"
    assert "cv-bullet-too-long" in rules(text)


def test_every_rule_is_report_only():
    """CV problems are matters of judgement; rewriting them would change what
    the document says."""
    assert all(not issue.auto_fixable for issue in cv.check(md.read(BAD)))


def test_rules_are_returned_sorted():
    names = [issue.rule for issue in cv.check(md.read(BAD))]
    assert names == sorted(names)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


@pytest.fixture
def cv_file(tmp_path):
    path = tmp_path / "cv.md"
    path.write_text(BAD)
    return path


def test_detect_auto_applies_the_cv_rules(cv_file):
    assert any(i.rule.startswith("cv-") for i in docfix.detect(str(cv_file)))


def test_detect_can_be_told_to_skip_them(cv_file):
    assert not [i for i in docfix.detect(str(cv_file), cv=False) if i.rule.startswith("cv-")]


def test_detect_can_be_told_to_force_them(tmp_path):
    """A document that does not look like a CV can still be checked as one."""
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\n## Thoughts\n\n- a thought\n")
    forced = {i.rule for i in docfix.detect(str(path), cv=True)}
    assert "cv-missing-section" in forced
    assert not [i for i in docfix.detect(str(path)) if i.rule.startswith("cv-")]


def test_cv_rules_do_not_fire_on_ordinary_documents(tmp_path):
    path = tmp_path / "readme.md"
    path.write_text("# Project\n\n## Install\n\n## Usage\n\n- run it\n")
    assert not [i for i in docfix.detect(str(path)) if i.rule.startswith("cv-")]


def test_format_file_reports_cv_issues(cv_file, tmp_path):
    result = docfix.format_file(str(cv_file), output=str(tmp_path / "out.md"))
    assert any(i.rule.startswith("cv-") for i in result.issues)
    assert all(not i.auto_fixable for i in result.issues if i.rule.startswith("cv-"))


def test_looks_like_cv_is_exposed(cv_file, tmp_path):
    assert docfix.looks_like_cv(str(cv_file))
    other = tmp_path / "r.md"
    other.write_text("# R\n\n## Install\n")
    assert not docfix.looks_like_cv(str(other))


# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------


def test_cv_presets_ship():
    names = docfix.list_templates()
    assert {"cv-classic", "cv-modern", "cv-compact"} <= set(names)


@pytest.mark.parametrize("name", ["cv-classic", "cv-modern", "cv-compact"])
def test_cv_presets_are_tighter_than_the_general_ones(name):
    """A CV is dense by nature -- more has to fit on one page."""
    template = docfix.load(name)
    assert template.spacing["line_height"] <= docfix.load("formal").spacing["line_height"]
    assert template.fonts["body"]["size"] <= docfix.load("formal").fonts["body"]["size"]
    assert template.fonts.get("fallback"), "a CV may well contain non-Latin names"


@pytest.mark.parametrize("name", ["cv-classic", "cv-modern", "cv-compact"])
def test_cv_presets_format_a_real_cv(tmp_path, name):
    source = tmp_path / "cv.md"
    source.write_text(GOOD)
    out = tmp_path / f"{name}.md"
    result = docfix.format_file(str(source), template=name, output=str(out))
    assert out.read_text().startswith("# Ada Lovelace")
    assert result.template == name
