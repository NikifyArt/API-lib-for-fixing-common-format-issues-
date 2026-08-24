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


# --------------------------------------------------------------------------
# Batch, --diff, and the messages an adopter's CI will actually see
# --------------------------------------------------------------------------


def test_nothing_to_format_is_not_an_error(tmp_path, capsys):
    """An empty directory means there was nothing to do, not that something
    went wrong -- a CI step must not fail on it."""
    (tmp_path / "empty").mkdir()
    assert main(["format", str(tmp_path / "empty")]) == EXIT_OK
    assert "nothing to format" in capsys.readouterr().err


def test_nothing_to_check_is_not_an_error(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert main(["check", str(tmp_path / "empty")]) == EXIT_OK


def test_output_flag_with_several_files_is_refused(tmp_path, capsys):
    """-o names one destination, so it cannot mean several. Silently formatting
    only the first, or overwriting one output repeatedly, would lose work."""
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text(MESSY)
    code = main(["format", str(tmp_path), "-o", str(tmp_path / "out.md")])
    assert code == EXIT_ERROR
    assert "use --out-dir for several" in capsys.readouterr().err


def test_keep_intermediate_with_several_files_is_refused(tmp_path, capsys):
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text(MESSY)
    assert main(["format", str(tmp_path), "--keep-intermediate"]) == EXIT_ERROR
    assert "single file" in capsys.readouterr().err


def test_diff_writes_nothing_and_exits_1_when_changes_are_pending(messy, tmp_path, capsys):
    """The CI-gate contract: --diff must never write, and must fail the build."""
    before = sorted(p.name for p in tmp_path.iterdir())
    assert main(["format", str(messy), "--diff"]) == EXIT_ISSUES
    out = capsys.readouterr().out
    assert "1 of 1 file(s) would change" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == before, "--diff must write nothing"
    assert messy.read_text() == MESSY, "--diff must not touch the source"


def test_diff_exits_0_when_nothing_would_change(tmp_path, capsys):
    path = tmp_path / "clean.md"
    path.write_text(CLEAN)
    assert main(["format", str(path), "--diff"]) == EXIT_OK
    assert "0 of 1 file(s) would change" in capsys.readouterr().out


def test_diff_shows_the_actual_lines(messy, capsys):
    assert main(["format", str(messy), "--diff"]) == EXIT_ISSUES
    out = capsys.readouterr().out
    assert "---" in out and "+++" in out
    assert any(line.startswith("+# Title") for line in out.splitlines())


def test_batch_formats_every_file_into_an_out_dir(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    for name in ("a.md", "b.md"):
        (source / name).write_text(MESSY)
    out = tmp_path / "out"
    assert main(["format", str(source), "--out-dir", str(out)]) == EXIT_OK
    assert (out / "a.md").exists() and (out / "b.md").exists()
    assert (source / "a.md").read_text() == MESSY, "sources must be untouched"


def test_batch_mirrors_the_tree_rather_than_flattening_it(tmp_path):
    """Flattening would make a/README.md and b/README.md overwrite each other."""
    source = tmp_path / "src"
    (source / "a").mkdir(parents=True)
    (source / "b").mkdir(parents=True)
    (source / "a" / "README.md").write_text("# A  \n")
    (source / "b" / "README.md").write_text("# B  \n")
    out = tmp_path / "out"

    assert main(["format", str(source), "--out-dir", str(out)]) == EXIT_OK
    assert "A" in (out / "a" / "README.md").read_text()
    assert "B" in (out / "b" / "README.md").read_text()


def test_one_unreadable_file_does_not_abort_the_batch(tmp_path, capsys):
    """A CI gate reporting failure having examined a fraction of the tree is
    worse than one that fails loudly on the file it could not read."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "good.md").write_text(MESSY)
    (source / "bad.docx").write_bytes(b"not a docx at all")

    code = main(["check", str(source)])
    err = capsys.readouterr().err
    assert code in (EXIT_ISSUES, EXIT_ERROR)
    assert "bad.docx" in err
    assert (source / "good.md").exists()


def test_an_excluded_file_is_skipped(tmp_path, capsys):
    source = tmp_path / "src"
    (source / "vendor").mkdir(parents=True)
    (source / "vendor" / "x.md").write_text(MESSY)
    (source / "mine.md").write_text(MESSY)
    (tmp_path / "docfix.toml").write_text('exclude = ["**/vendor/**"]\n')

    main(["check", str(source), "--config", str(tmp_path / "docfix.toml")])
    out = capsys.readouterr().out
    assert "mine.md" in out
    assert "vendor" not in out


def test_a_dot_directory_is_not_walked(tmp_path, capsys):
    source = tmp_path / "src"
    (source / ".git").mkdir(parents=True)
    (source / ".git" / "x.md").write_text(MESSY)
    (source / "mine.md").write_text(MESSY)
    main(["check", str(source)])
    assert ".git" not in capsys.readouterr().out


def test_rules_lists_a_plugin_rule_and_marks_it(capsys):
    """A rule registered from outside must be as discoverable as a built-in,
    or an adopter cannot configure their own."""
    import docfix
    from docfix import plugins
    from docfix.detect.rules import Issue, emits

    @emits("plugin-visible-rule")
    def visible(doc, config):
        return [Issue("plugin-visible-rule", "x", "info")]

    plugins.reset()
    try:
        docfix.register_rule(visible)
        assert main(["rules", "--no-config"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "plugin-visible-rule" in out
        assert "[plugin]" in out
    finally:
        plugins.reset()


def test_a_failed_font_download_is_a_message_not_a_traceback(capsys):
    """CacheError was not in main()'s except list, so the likeliest failure of
    `fonts install` -- offline, air-gapped, behind a proxy -- printed a raw
    Python traceback."""
    from unittest.mock import patch

    from docfix.fonts.cache import CacheError

    with patch(
        "docfix.fonts.cache.install",
        side_effect=CacheError("could not download https://x/y.ttf: no route to host"),
    ):
        code = main(["fonts", "install"])

    assert code == EXIT_ERROR
    assert "no route to host" in capsys.readouterr().err
