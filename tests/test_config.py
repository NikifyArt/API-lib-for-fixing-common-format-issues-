"""Project configuration, rule control, batch processing, and --diff."""

import os

import pytest

import docfix
from docfix import config as cfg
from docfix.cli import EXIT_ERROR, EXIT_ISSUES, EXIT_OK, main

MESSY = '#  A  \n\n\n* x\n+ y\n\nHe said "hi" and “bye”.\n'


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "docs" / "a.md").write_text(MESSY)
    (tmp_path / "docs" / "b.md").write_text("# B\n\nclean\n")
    (tmp_path / "vendor" / "v.md").write_text(MESSY)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_no_config_anywhere_gives_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    found = cfg.discover()
    assert found.source is None
    assert found.template is None and not found.rules


def test_docfix_toml_is_found(project):
    (project / "docfix.toml").write_text('template = "formal"\n')
    assert cfg.discover().template == "formal"


def test_docfix_toml_is_found_in_an_ancestor(project, monkeypatch):
    (project / "docfix.toml").write_text('template = "friendly"\n')
    monkeypatch.chdir(project / "docs")
    assert cfg.discover().template == "friendly"


def test_pyproject_tool_table_is_found(project):
    (project / "pyproject.toml").write_text(
        '[project]\nname = "x"\n\n[tool.docfix]\ntemplate = "technical"\n'
    )
    assert cfg.discover().template == "technical"


def test_pyproject_without_a_docfix_table_is_ignored(project):
    (project / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert cfg.discover().source is None


def test_docfix_toml_wins_over_pyproject_in_the_same_directory(project):
    (project / "docfix.toml").write_text('template = "formal"\n')
    (project / "pyproject.toml").write_text("[tool.docfix]\ntemplate = 'minimal'\n")
    assert cfg.discover().template == "formal"


def test_docfix_toml_also_accepts_the_tool_table(project):
    (project / "docfix.toml").write_text("[tool.docfix]\ntemplate = 'minimal'\n")
    assert cfg.discover().template == "minimal"


def test_explicit_path_overrides_discovery(project, tmp_path):
    (project / "docfix.toml").write_text('template = "formal"\n')
    other = tmp_path / "other.toml"
    other.write_text('template = "technical"\n')
    assert cfg.discover(explicit=str(other)).template == "technical"


def test_use_config_false_ignores_everything(project):
    (project / "docfix.toml").write_text('template = "formal"\n')
    assert cfg.discover(use_config=False).template is None


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data, needle",
    [
        ({"nope": 1}, "unknown key"),
        ({"template": 3}, "template"),
        ({"exclude": "notalist"}, "exclude"),
        ({"cv": "yes"}, "cv"),
        ({"rules": "notatable"}, "rules"),
        ({"rules": {"x": "loud"}}, "must be true, false"),
        ({"options": {"x": "notatable"}}, "must be a table"),
    ],
)
def test_malformed_config_is_rejected(data, needle):
    with pytest.raises(cfg.ConfigError, match=needle):
        cfg.from_dict(data)


def test_bad_toml_is_reported_clearly(tmp_path):
    path = tmp_path / "docfix.toml"
    path.write_text("template = [unclosed\n")
    with pytest.raises(cfg.ConfigError, match="not valid TOML"):
        cfg.load(str(path))


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(cfg.ConfigError, match="could not read"):
        cfg.load(str(tmp_path / "nope.toml"))


# --------------------------------------------------------------------------
# Rule control
# --------------------------------------------------------------------------


def test_a_disabled_rule_produces_nothing(project):
    path = str(project / "docs" / "a.md")
    assert "quotes-mixed" in {i.rule for i in docfix.detect(path)}

    disabled = cfg.from_dict({"rules": {"quotes-mixed": False}})
    assert "quotes-mixed" not in {i.rule for i in docfix.detect(path, config=disabled)}


def test_severity_override_changes_only_that_rule(project):
    path = str(project / "docs" / "a.md")
    louder = cfg.from_dict({"rules": {"whitespace-trailing": "error"}})
    issues = {i.rule: i.severity for i in docfix.detect(path, config=louder)}
    assert issues["whitespace-trailing"] == "error"
    assert issues["list-mixed-markers"] == "warning"


def test_an_option_changes_behaviour():
    """Proof the option reaches the rule, not just the config object."""
    from docfix import cv
    from docfix.adapters import markdown as md

    doc = md.read(
        "# Me\n\nme@x.test\n\n## Experience\n\n### R — 2020\n\n"
        "- a short bullet\n\n## Education\n\n### D — 2016\n"
    )
    assert not [i for i in cv.check(doc) if i.rule == "cv-bullet-too-long"]

    tuned = cfg.from_dict({"options": {"cv-bullet-too-long": {"max_length": 5}}})
    assert [i for i in cv.check(doc, tuned) if i.rule == "cv-bullet-too-long"]


def test_the_default_config_changes_nothing():
    issues = [docfix.Issue("r", "m")]
    assert cfg.Config().apply(issues) == issues


def test_every_emitted_rule_id_is_declared(project):
    """Guards against a rule id drifting away from its declaration, which is
    what `docfix rules` and every config file address it by."""
    from docfix.cv import CV_RULES
    from docfix.detect.rules import SOURCE_RULES, STRUCTURE_RULES

    declared = {i for r in STRUCTURE_RULES + SOURCE_RULES + CV_RULES for i in r.rule_ids}
    emitted = {i.rule for i in docfix.detect(str(project / "docs" / "a.md"), cv=True)}
    assert emitted <= declared, f"undeclared rule id(s): {sorted(emitted - declared)}"


def test_every_rule_declares_at_least_one_id():
    from docfix.cv import CV_RULES
    from docfix.detect.rules import SOURCE_RULES, STRUCTURE_RULES

    for rule in STRUCTURE_RULES + SOURCE_RULES + CV_RULES:
        assert getattr(rule, "rule_ids", ()), f"{rule.__name__} declares no rule id"


# --------------------------------------------------------------------------
# Batch
# --------------------------------------------------------------------------


def test_check_walks_a_directory(project, capsys):
    assert main(["check", "."]) == EXIT_ISSUES
    out = capsys.readouterr().out
    assert "docs/a.md" in out and "docs/b.md" in out
    assert "3 file(s)" in out


def test_exclude_skips_matching_paths(project, capsys):
    (project / "docfix.toml").write_text('exclude = ["vendor/**"]\n')
    main(["check", "."])
    out = capsys.readouterr().out
    assert "vendor" not in out
    assert "2 file(s)" in out


def test_several_paths_can_be_given(project, capsys):
    main(["check", "docs/a.md", "docs/b.md"])
    assert "2 file(s)" in capsys.readouterr().out


def test_unsupported_extensions_are_skipped_when_walking(project, capsys):
    (project / "docs" / "notes.txt").write_text("not a document\n")
    main(["check", "."])
    assert "notes.txt" not in capsys.readouterr().out


def test_docfix_output_is_not_reformatted(project, capsys):
    (project / "docs" / "a.formatted.md").write_text("# already done\n")
    main(["check", "."])
    assert "a.formatted.md" not in capsys.readouterr().out


def test_format_writes_every_file_in_a_directory(project):
    assert main(["format", "docs", "-q"]) == EXIT_OK
    assert (project / "docs" / "a.formatted.md").exists()
    assert (project / "docs" / "b.formatted.md").exists()


def test_out_dir_collects_the_results(project):
    assert main(["format", "docs", "--out-dir", "built", "-q"]) == EXIT_OK
    assert sorted(os.listdir(project / "built")) == ["a.md", "b.md"]
    assert (project / "docs" / "a.md").read_text() == MESSY, "source untouched"


def test_single_file_flags_are_refused_for_many(project, capsys):
    assert main(["format", "docs", "-o", "one.md"]) == EXIT_ERROR
    assert "single file" in capsys.readouterr().err


def test_nothing_to_do_is_not_an_error(project, capsys):
    (project / "empty").mkdir()
    assert main(["check", "empty"]) == EXIT_OK


# --------------------------------------------------------------------------
# --diff
# --------------------------------------------------------------------------


def test_diff_writes_nothing_and_reports_changes(project, capsys):
    assert main(["format", "docs", "--diff"]) == EXIT_ISSUES
    out = capsys.readouterr().out
    assert "-#  A  " in out and "+# A" in out
    assert "1 of 2 file(s) would change" in out
    assert not (project / "docs" / "a.formatted.md").exists()


def test_diff_exits_zero_when_nothing_would_change(project, capsys):
    assert main(["format", "docs/b.md", "--diff"]) == EXIT_OK
    assert "0 of 1" in capsys.readouterr().out


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------


def test_cli_reads_the_config(project, capsys):
    (project / "docfix.toml").write_text('[rules]\n"quotes-mixed" = false\n')
    main(["check", "docs/a.md"])
    assert "quotes-mixed" not in capsys.readouterr().out


def test_no_config_flag_ignores_it(project, capsys):
    (project / "docfix.toml").write_text('[rules]\n"quotes-mixed" = false\n')
    main(["check", "docs/a.md", "--no-config"])
    assert "quotes-mixed" in capsys.readouterr().out


def test_config_supplies_the_default_template(project, capsys):
    (project / "docfix.toml").write_text('template = "friendly"\n')
    main(["format", "docs/b.md", "-q"])
    assert "friendly" in capsys.readouterr().out


def test_an_explicit_template_beats_the_config(project, capsys):
    (project / "docfix.toml").write_text('template = "friendly"\n')
    main(["format", "docs/b.md", "-t", "formal", "-q"])
    assert "formal" in capsys.readouterr().out


def test_bad_config_exits_2(project, capsys):
    (project / "docfix.toml").write_text("nonsense = 1\n")
    assert main(["check", "docs/a.md"]) == EXIT_ERROR
    assert "unknown key" in capsys.readouterr().err


def test_rules_command_lists_rule_ids_not_function_names(capsys):
    assert main(["rules", "--no-config"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "heading-skip" in out and "cv-weak-opener" in out
    assert "check_heading_levels" not in out


def test_rules_command_shows_what_the_config_does(project, capsys):
    (project / "docfix.toml").write_text('[rules]\n"quotes-mixed" = false\n')
    main(["rules"])
    assert "disabled by config: quotes-mixed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["check", "docs/b.md"], EXIT_OK),
        (["check", "docs/a.md"], EXIT_ISSUES),
        (["check", "missing.md"], EXIT_ERROR),
    ],
)
def test_exit_code_contract(project, argv, expected):
    """CI and pre-commit depend on these, so they are pinned."""
    assert main(argv) == expected


# --------------------------------------------------------------------------
# Regressions found by the push-checkpoint review
# --------------------------------------------------------------------------


def test_out_dir_mirrors_the_tree_rather_than_flattening(project):
    """Joining --out-dir with the basename alone silently destroyed one of two
    same-named files in different directories."""
    (project / "docs" / "sub").mkdir()
    (project / "docs" / "sub" / "a.md").write_text("# nested\n")

    assert main(["format", "docs", "--out-dir", "built", "-q"]) == EXIT_OK
    assert (project / "built" / "a.md").exists()
    assert (project / "built" / "sub" / "a.md").exists()
    assert "nested" in (project / "built" / "sub" / "a.md").read_text()


def test_a_second_run_does_not_reformat_its_own_output(project):
    assert main(["format", "docs", "--out-dir", "built", "-q"]) == EXIT_OK
    assert main(["format", "docs", "--out-dir", "built", "-q"]) == EXIT_OK
    assert sorted(os.listdir(project / "built")) == ["a.md", "b.md"]


def test_one_unreadable_file_does_not_abort_the_batch(project, capsys):
    """A CI gate must not report 'error' having examined a fraction of the tree."""
    (project / "docs" / "broken.pdf").write_bytes(b"not a pdf at all")

    code = main(["check", "docs"])
    captured = capsys.readouterr()

    assert code == EXIT_ERROR
    assert "broken.pdf" in captured.err
    # Everything else was still checked, including the file sorted after it.
    assert "docs/a.md" in captured.out and "docs/b.md" in captured.out


def test_a_bad_option_value_names_the_option_and_the_file(project):
    (project / "docfix.toml").write_text(
        '[options]\n"cv-bullet-too-long" = { max_length = "long" }\n'
    )
    found = cfg.discover()
    with pytest.raises(cfg.ConfigError, match="max_length"):
        found.int_option("cv-bullet-too-long", "max_length", 220)


def test_a_non_scalar_option_is_rejected_at_load():
    with pytest.raises(cfg.ConfigError, match="single value"):
        cfg.from_dict({"options": {"r": {"x": [1, 2]}}})


def test_font_rule_ids_are_listed_by_the_rules_command(capsys):
    """They are configurable, so they must be discoverable."""
    main(["rules", "--no-config"])
    out = capsys.readouterr().out
    assert "font-coverage" in out and "font-unavailable" in out
