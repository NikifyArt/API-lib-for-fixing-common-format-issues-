"""Extension registration: adapters, rules, and entry-point plugins.

The point of this layer is that an adopter never edits a core file, so these
tests register from outside exactly the way a fork or a plugin package would.

Entry-point discovery is exercised against a real `.dist-info` directory on
`sys.path` rather than a patched `importlib.metadata`. Mocking the discovery
would test the mock: what can actually break is the metadata format and the
lookup, and only real metadata exercises those.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

import docfix
from docfix import adapters, plugins
from docfix import config as config_module
from docfix.detect.rules import Issue, emits, source_rules, structure_rules
from docfix.ir import Document, Paragraph, Run


@pytest.fixture(autouse=True)
def clean_registry():
    """Every test starts from an empty registry and leaves one behind."""
    plugins.reset()
    yield
    plugins.reset()


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def make_adapter(name="rtf", extensions=(".rtf",), **kwargs):
    """An adapter with the minimum shape, as a third party would write one."""
    written = kwargs.pop("written", [])

    def read_path(path):
        return Document(blocks=[Paragraph(runs=[Run(f"read {path}")])])

    def write_path(doc, template, path):
        written.append(path)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("written by the plugin\n")

    return adapters.Adapter(
        name=name,
        extensions=extensions,
        read_path=kwargs.pop("read_path", read_path),
        write_path=kwargs.pop("write_path", write_path),
        **kwargs,
    )


@emits("house-style-banned-word")
def check_banned_word(doc, config):
    """Report an in-house banned word, as a company's own rule would."""
    issues = []
    for block in doc.blocks:
        for run in getattr(block, "runs", []):
            if "synergy" in run.text.lower():
                issues.append(Issue("house-style-banned-word", "banned word", "warning"))
    return issues


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------


def test_a_registered_adapter_handles_its_extension():
    docfix.register_adapter(make_adapter())
    assert ".rtf" in adapters.supported_extensions()
    assert adapters.for_path("notes.rtf").name == "rtf"


def test_registering_does_not_disturb_the_built_ins():
    docfix.register_adapter(make_adapter())
    assert adapters.for_path("notes.md").name == "markdown"
    assert {a.name for a in adapters.adapters()} >= {"markdown", "pdf", "docx"}


def test_a_registered_adapter_overrides_a_built_in_extension():
    """A fork substituting its own reader for a format docfix already handles
    must win, or the override is useless."""
    docfix.register_adapter(make_adapter(name="my-markdown", extensions=(".md",)))
    assert adapters.for_path("notes.md").name == "my-markdown"


def test_unregistering_restores_the_built_in():
    docfix.register_adapter(make_adapter(name="my-markdown", extensions=(".md",)))
    assert docfix.unregister_adapter("my-markdown") is True
    assert adapters.for_path("notes.md").name == "markdown"
    assert docfix.unregister_adapter("my-markdown") is False


def test_a_duplicate_name_is_refused_unless_replacing():
    docfix.register_adapter(make_adapter())
    with pytest.raises(plugins.PluginError, match="already registered"):
        docfix.register_adapter(make_adapter())
    docfix.register_adapter(make_adapter(extensions=(".rtf", ".rtfd")), replace=True)
    assert ".rtfd" in adapters.supported_extensions()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"extensions": ()}, "claims no extensions"),
        ({"extensions": ("rtf",)}, "beginning with a dot"),
        ({"name": ""}, "non-empty string"),
        ({"read_path": "not callable"}, "not callable"),
    ],
)
def test_a_malformed_adapter_is_refused_with_a_useful_message(kwargs, message):
    with pytest.raises(plugins.PluginError, match=message):
        docfix.register_adapter(make_adapter(**kwargs))


def test_an_object_of_the_wrong_shape_is_refused():
    with pytest.raises(plugins.PluginError, match="missing 'name'"):
        docfix.register_adapter(object())


def test_a_registered_adapter_actually_formats_a_file(tmp_path):
    """The whole point: a format docfix has never heard of, end to end."""
    docfix.register_adapter(make_adapter())
    source = tmp_path / "notes.rtf"
    source.write_text("anything", encoding="utf-8")

    result = docfix.format_file(str(source), output=str(tmp_path / "out.rtf"))

    assert (tmp_path / "out.rtf").read_text(encoding="utf-8") == "written by the plugin\n"
    assert source.read_text(encoding="utf-8") == "anything", "source must be untouched"
    assert isinstance(result.issues, list)


def test_a_plugin_adapter_can_convert_into_a_built_in_format(tmp_path):
    """Reader from the plugin, writer from docfix -- what the shared IR is for."""
    docfix.register_adapter(make_adapter())
    source = tmp_path / "notes.rtf"
    source.write_text("anything", encoding="utf-8")

    docfix.format_file(str(source), output=str(tmp_path / "out.md"))
    assert "read " in (tmp_path / "out.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def test_a_registered_rule_runs(tmp_path):
    docfix.register_rule(check_banned_word, family="structure")
    assert check_banned_word in structure_rules()

    doc = tmp_path / "notes.md"
    doc.write_text("# Title\n\nWe must leverage synergy here.\n", encoding="utf-8")
    ids = {issue.rule for issue in docfix.detect(str(doc))}
    assert "house-style-banned-word" in ids


def test_a_registered_rule_is_configurable_like_any_other(tmp_path):
    """A third-party rule that could not be disabled would be a second-class
    one; the config post-filter has to reach it."""
    docfix.register_rule(check_banned_word, family="structure")
    config_file = tmp_path / "docfix.toml"
    config_file.write_text(
        textwrap.dedent(
            """
            [rules]
            "house-style-banned-word" = false
            """
        ),
        encoding="utf-8",
    )
    config = config_module.load(str(config_file))

    doc = tmp_path / "notes.md"
    doc.write_text("# Title\n\nPure synergy.\n", encoding="utf-8")
    assert "house-style-banned-word" in {
        i.rule for i in docfix.detect(str(doc))
    }, "the rule must fire before the config turns it off"
    ids = {issue.rule for issue in docfix.detect(str(doc), config=config)}
    assert "house-style-banned-word" not in ids


def test_a_rule_without_declared_ids_is_refused():
    """Undeclared ids cannot be addressed by a config file, so a rule that
    skips @emits could never be turned off."""

    def undeclared(doc, config):
        return []

    with pytest.raises(plugins.PluginError, match="declares no rule ids"):
        docfix.register_rule(undeclared)


def test_a_non_callable_rule_is_refused():
    with pytest.raises(plugins.PluginError, match="must be callable"):
        docfix.register_rule("not a rule")


def test_an_unknown_family_is_refused():
    with pytest.raises(plugins.PluginError, match="unknown rule family"):
        docfix.register_rule(check_banned_word, family="nonsense")


def test_registering_the_same_rule_twice_does_not_double_it():
    docfix.register_rule(check_banned_word)
    docfix.register_rule(check_banned_word)
    assert structure_rules().count(check_banned_word) == 1


def test_unregistering_a_rule():
    docfix.register_rule(check_banned_word)
    assert docfix.unregister_rule(check_banned_word) is True
    assert check_banned_word not in structure_rules()
    assert docfix.unregister_rule(check_banned_word) is False


def test_a_source_rule_registers_into_its_own_family(tmp_path):
    @emits("no-tabs-ever")
    def check_no_tabs(text, config):
        return [Issue("no-tabs-ever", "tab", "warning")] if "\t" in text else []

    docfix.register_rule(check_no_tabs, family="source")
    assert check_no_tabs in source_rules()
    assert check_no_tabs not in structure_rules()

    doc = tmp_path / "notes.md"
    doc.write_text("# T\n\n\tindented\n", encoding="utf-8")
    assert "no-tabs-ever" in {i.rule for i in docfix.detect(str(doc))}


def test_a_cv_rule_only_runs_for_a_cv(tmp_path):
    @emits("cv-house-rule")
    def check_cv_house_rule(doc, config):
        return [Issue("cv-house-rule", "fires", "info")]

    docfix.register_rule(check_cv_house_rule, family="cv")

    not_a_cv = tmp_path / "readme.md"
    not_a_cv.write_text("# README\n\nA project.\n", encoding="utf-8")
    assert "cv-house-rule" not in {i.rule for i in docfix.detect(str(not_a_cv))}

    a_cv = tmp_path / "cv.md"
    a_cv.write_text(
        "# Ada Lovelace\n\nada@example.com\n\n"
        "## Experience\n\n### Analyst\n\n2020-2024\n\n"
        "## Education\n\n### Maths\n\n2016-2020\n",
        encoding="utf-8",
    )
    assert "cv-house-rule" in {i.rule for i in docfix.detect(str(a_cv))}


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def install_plugin_distribution(tmp_path, module_source, entry_points_txt):
    """Put a real importable distribution on sys.path.

    A `.dist-info` directory with `entry_points.txt` is what pip installs and
    what `importlib.metadata` reads, so this exercises the same discovery a
    published plugin would go through -- without pip or a venv.
    """
    (tmp_path / "acme_docfix.py").write_text(module_source, encoding="utf-8")
    dist_info = tmp_path / "acme_docfix-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: acme-docfix\nVersion: 1.0\n", encoding="utf-8"
    )
    (dist_info / "entry_points.txt").write_text(entry_points_txt, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    return dist_info


@pytest.fixture
def plugin_path(tmp_path):
    original = list(sys.path)
    yield tmp_path
    sys.path[:] = original
    sys.modules.pop("acme_docfix", None)


def test_an_entry_point_plugin_is_discovered_without_any_source_change(plugin_path):
    """The headline case: a separate pip package extends docfix, no fork."""
    install_plugin_distribution(
        plugin_path,
        textwrap.dedent(
            """
            import docfix
            from docfix.adapters import Adapter
            from docfix.detect.rules import Issue, emits
            from docfix.ir import Document, Paragraph, Run

            @emits("acme-rule")
            def acme_rule(doc, config):
                return [Issue("acme-rule", "from the plugin", "info")]

            ACME = Adapter(
                name="acme",
                extensions=(".acme",),
                read_path=lambda path: Document(blocks=[Paragraph(runs=[Run("hi")])]),
                write_path=lambda doc, template, path: None,
            )

            def register():
                docfix.register_adapter(ACME)
                docfix.register_rule(acme_rule)
            """
        ),
        "[docfix.plugins]\nacme = acme_docfix:register\n",
    )

    assert ".acme" in adapters.supported_extensions()
    assert adapters.for_path("x.acme").name == "acme"
    notes = plugin_path / "notes.md"
    notes.write_text("# T\n\ntext\n", encoding="utf-8")
    assert "acme-rule" in {i.rule for i in docfix.detect(str(notes))}
    assert plugins.plugin_errors() == ()


def test_a_broken_plugin_is_contained_and_reported(plugin_path):
    """A third party's bug must not stop anyone formatting a document."""
    install_plugin_distribution(
        plugin_path,
        "def register():\n    raise RuntimeError('the plugin is broken')\n",
        "[docfix.plugins]\nacme = acme_docfix:register\n",
    )

    assert adapters.for_path("notes.md").name == "markdown", "docfix must still work"
    errors = plugins.plugin_errors()
    assert len(errors) == 1
    assert "acme" in errors[0] and "the plugin is broken" in errors[0]


def test_a_plugin_that_will_not_import_is_contained(plugin_path):
    install_plugin_distribution(
        plugin_path,
        "import a_module_that_does_not_exist\n",
        "[docfix.plugins]\nacme = acme_docfix:register\n",
    )
    assert adapters.for_path("notes.md").name == "markdown"
    assert any("failed to import" in error for error in plugins.plugin_errors())


def test_plugins_load_once_even_across_many_lookups(plugin_path):
    install_plugin_distribution(
        plugin_path,
        textwrap.dedent(
            """
            CALLS = []

            def register():
                CALLS.append(1)
            """
        ),
        "[docfix.plugins]\nacme = acme_docfix:register\n",
    )

    for _ in range(5):
        adapters.supported_extensions()
        plugins.registered_rules("structure")

    import acme_docfix

    assert acme_docfix.CALLS == [1], "a plugin must run exactly once"


def test_reset_re_arms_discovery(plugin_path):
    install_plugin_distribution(
        plugin_path,
        "import docfix\nCALLS = []\n\ndef register():\n    CALLS.append(1)\n",
        "[docfix.plugins]\nacme = acme_docfix:register\n",
    )
    plugins.load_plugins()
    plugins.reset()
    plugins.load_plugins()

    import acme_docfix

    assert acme_docfix.CALLS == [1, 1]


# --------------------------------------------------------------------------
# Deprecated aliases
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("docfix.adapters", "ADAPTERS"),
        ("docfix.detect.rules", "STRUCTURE_RULES"),
        ("docfix.detect.rules", "SOURCE_RULES"),
        ("docfix.cv", "CV_RULES"),
    ],
)
def test_the_old_tuple_names_still_work_and_warn(module, name):
    """They were public, so they keep working -- as live views, not stale
    snapshots, or a caller would silently miss registered extensions."""
    import importlib

    target = importlib.import_module(module)
    with pytest.deprecated_call():
        value = getattr(target, name)
    assert isinstance(value, tuple) and value


def test_the_deprecated_alias_includes_registered_extensions():
    docfix.register_adapter(make_adapter())
    with pytest.deprecated_call():
        assert "rtf" in {a.name for a in adapters.ADAPTERS}


def test_an_unknown_module_attribute_still_raises_attribute_error():
    with pytest.raises(AttributeError):
        _ = adapters.no_such_thing


# --------------------------------------------------------------------------
# Findings from the phase-9 checkpoint review
# --------------------------------------------------------------------------


def test_an_adapter_missing_source_text_is_refused_not_crashed_on(tmp_path):
    """The pipeline dereferences source_text and coverage_issues, so accepting
    a duck-typed object without them traded a clear message for an
    AttributeError the CLI does not catch."""
    from types import SimpleNamespace

    partial = SimpleNamespace(
        name="rtf",
        extensions=(".rtf",),
        read_path=lambda path: Document(blocks=[]),
        write_path=lambda doc, template, path: None,
    )
    with pytest.raises(plugins.PluginError, match="source_text"):
        docfix.register_adapter(partial)


def test_an_upper_case_extension_is_refused():
    """for_path lowercases the file's extension, so '.RTF' would be advertised
    by supported_extensions() and never match anything."""
    with pytest.raises(plugins.PluginError, match="lower case"):
        docfix.register_adapter(make_adapter(extensions=(".RTF",)))


def test_unregistering_before_discovery_still_removes_a_plugin(plugin_path):
    """Unregistering before anything triggered lazy loading was a silent no-op,
    and the plugin then registered itself on the next lookup."""
    install_plugin_distribution(
        plugin_path,
        textwrap.dedent(
            """
            import docfix
            from docfix.adapters import Adapter
            from docfix.ir import Document

            ACME = Adapter(
                name="acme",
                extensions=(".acme",),
                read_path=lambda path: Document(blocks=[]),
                write_path=lambda doc, template, path: None,
            )

            def register():
                docfix.register_adapter(ACME)
            """
        ),
        "[docfix.plugins]\nacme = acme_docfix:register\n",
    )

    assert docfix.unregister_adapter("acme") is True, "the plugin was not there to remove"
    assert ".acme" not in adapters.supported_extensions(), "it came back"


def test_a_forced_reload_does_not_duplicate_errors(plugin_path):
    install_plugin_distribution(
        plugin_path,
        "def register():\n    raise RuntimeError('broken')\n",
        "[docfix.plugins]\nacme = acme_docfix:register\n",
    )
    plugins.load_plugins()
    plugins.load_plugins(force=True)
    plugins.load_plugins(force=True)
    assert len(plugins.plugin_errors()) == 1, plugins.plugin_errors()
