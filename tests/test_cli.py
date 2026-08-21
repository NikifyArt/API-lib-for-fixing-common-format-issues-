"""CLI contract: exit codes and the source-safety guarantee."""

import shlex
import subprocess
import sys

import pytest

from docfix.cli import EXIT_ERROR, EXIT_ISSUES, EXIT_OK, main

MESSY = "#  Title  \n\n\n* a\n+ b\n"
CLEAN = "# Title\n\n- a\n- b\n"


@pytest.fixture
def messy(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(MESSY)
    return path


def test_check_exits_1_when_issues_exist(messy, capsys):
    assert main(["check", str(messy)]) == EXIT_ISSUES
    assert "list-mixed-markers" in capsys.readouterr().out


def test_check_exits_0_on_a_clean_file(tmp_path, capsys):
    path = tmp_path / "clean.md"
    path.write_text(CLEAN)
    assert main(["check", str(path)]) == EXIT_OK
    assert "no issues found" in capsys.readouterr().out


def test_format_writes_the_new_file(messy, tmp_path, capsys):
    out = tmp_path / "out.md"
    assert main(["format", str(messy), "-o", str(out)]) == EXIT_OK
    assert out.read_text() == "# Title\n\n- a\n- b\n"
    assert messy.read_text() == MESSY
    assert "->" in capsys.readouterr().out


def test_format_accepts_a_template(messy, tmp_path):
    out = tmp_path / "out.md"
    assert main(["format", str(messy), "-t", "friendly", "-o", str(out)]) == EXIT_OK


def test_quiet_suppresses_the_issue_list(messy, tmp_path, capsys):
    main(["format", str(messy), "-o", str(tmp_path / "o.md"), "--quiet"])
    assert "Needs a look" not in capsys.readouterr().out


def test_templates_lists_every_preset(capsys):
    assert main(["templates"]) == EXIT_OK
    out = capsys.readouterr().out
    for name in ("formal", "friendly", "minimal", "technical"):
        assert name in out


@pytest.mark.parametrize(
    "argv, needle",
    [
        (["check", "missing.md"], "no such file"),
        (["format", "missing.md"], "no such file"),
    ],
)
def test_missing_file_exits_2(argv, needle, capsys):
    assert main(argv) == EXIT_ERROR
    assert needle in capsys.readouterr().err


def test_unknown_template_exits_2(messy, capsys):
    assert main(["format", str(messy), "-t", "nope"]) == EXIT_ERROR
    assert "available presets" in capsys.readouterr().err


def test_unsupported_extension_exits_2(tmp_path, capsys):
    path = tmp_path / "a.rtf"
    path.write_text("x")
    assert main(["format", str(path)]) == EXIT_ERROR
    assert "no adapter" in capsys.readouterr().err


def test_closed_pipe_does_not_traceback():
    """`docfix templates | head -1` must exit quietly, not crash."""
    result = subprocess.run(
        f"{shlex.quote(sys.executable)} -m docfix.cli templates | head -1",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert "BrokenPipeError" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout.strip()
