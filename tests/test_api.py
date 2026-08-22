"""Public API. The safety property that matters most: the source is never touched."""

import os
from pathlib import Path

import pytest

import docfix
from docfix.adapters import UnsupportedFormatError

MESSY = "#  Title  \n\n\n* a\n+ b\n\ntext   \n"


@pytest.fixture
def messy(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(MESSY)
    return path


def test_format_file_writes_a_new_file_and_leaves_the_source_alone(messy):
    before = messy.read_text()
    result = docfix.format_file(str(messy))
    assert messy.read_text() == before, "the source file must never be modified"
    assert os.path.exists(result.output_path)
    assert result.output_path != str(messy)


def test_default_output_name():
    assert docfix.default_output_path("a/b/notes.md") == "a/b/notes.formatted.md"
    assert docfix.default_output_path("notes.markdown") == "notes.formatted.markdown"


def test_explicit_output_path_is_honoured(messy, tmp_path):
    out = tmp_path / "elsewhere.md"
    result = docfix.format_file(str(messy), output=str(out))
    assert result.output_path == str(out)
    assert out.read_text().startswith("# Title")


def test_writing_over_the_source_is_refused(messy):
    with pytest.raises(ValueError, match="refusing to overwrite"):
        docfix.format_file(str(messy), output=str(messy))


def test_result_splits_fixed_from_remaining(messy):
    result = docfix.format_file(str(messy))
    assert {i.rule for i in result.fixed} <= {
        "whitespace-trailing", "whitespace-blank-run", "list-mixed-markers",
        "whitespace-tab-indent", "paragraph-empty",
    }
    assert all(not i.auto_fixable for i in result.remaining)
    assert len(result.fixed) + len(result.remaining) == len(result.issues)


def test_the_output_is_actually_fixed(messy):
    result = docfix.format_file(str(messy))
    assert Path(result.output_path).read_text() == "# Title\n\n- a\n- b\n\ntext\n"


def test_formatting_the_output_again_changes_nothing(messy, tmp_path):
    first = docfix.format_file(str(messy), output=str(tmp_path / "1.md"))
    second = docfix.format_file(first.output_path, output=str(tmp_path / "2.md"))
    assert Path(first.output_path).read_text() == Path(second.output_path).read_text()


def test_detect_writes_nothing(messy, tmp_path):
    before = set(os.listdir(tmp_path))
    issues = docfix.detect(str(messy))
    assert issues
    assert set(os.listdir(tmp_path)) == before


def test_format_text_round_trip():
    assert docfix.format_text("* a\n+ b\n") == "- a\n- b\n"


def test_template_may_be_a_name_or_an_object(messy):
    loaded = docfix.load("friendly")
    by_name = docfix.format_file(str(messy), template="friendly")
    by_object = docfix.format_file(str(messy), template=loaded)
    assert by_name.template == by_object.template == "friendly"


def test_unsupported_extension_is_rejected(tmp_path):
    path = tmp_path / "a.rtf"
    path.write_text("x")
    with pytest.raises(UnsupportedFormatError, match="no adapter"):
        docfix.format_file(str(path))


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        docfix.format_file(str(tmp_path / "nope.md"))


def test_list_templates():
    assert "formal" in docfix.list_templates()


def test_output_extension_selects_the_writer(tmp_path):
    """BUG-001: the writer must come from the output path, not the input.

    It used to resolve one adapter from the input and use it for both, so
    `notes.md -o out.pdf` wrote Markdown text into a file named .pdf and
    reported success.
    """
    pytest.importorskip("reportlab")
    source = tmp_path / "notes.md"
    source.write_text("# Title\n\ntext\n")
    out = tmp_path / "out.pdf"

    docfix.format_file(str(source), output=str(out))

    assert out.read_bytes().startswith(b"%PDF"), "a .pdf output must be a real PDF"


def test_same_extension_still_round_trips(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Title\n\n* a\n+ b\n")
    out = tmp_path / "out.md"
    docfix.format_file(str(source), output=str(out))
    assert out.read_text() == "# Title\n\n- a\n- b\n"


def test_unsupported_output_extension_is_rejected(tmp_path):
    from docfix.adapters import UnsupportedFormatError

    source = tmp_path / "notes.md"
    source.write_text("# T\n")
    with pytest.raises(UnsupportedFormatError, match="no adapter"):
        docfix.format_file(str(source), output=str(tmp_path / "out.rtf"))
