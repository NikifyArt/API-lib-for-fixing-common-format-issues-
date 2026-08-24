"""The patterns `docs/EXTENDING.md` teaches, executed.

CLAUDE.md lists "EXTENDING.md's examples are executable" as a testing
convention, and a convention nothing enforces is just a wish. This runs the
doc's rule, adapter and plugin-registration patterns as written, so an adopter
pasting them gets working code -- the first impression the doc exists to make.

The doc's snippets elide bodies with `...` for readability, so this cannot
exec the file verbatim. What it pins is every API call the doc makes, in the
shape the doc makes it.
"""

from __future__ import annotations

import pytest

import docfix
from docfix import adapters, plugins
from docfix import config as config_module
from docfix.adapters import Adapter
from docfix.detect.rules import Issue, emits
from docfix.ir import Document, Paragraph, Run
from docfix.templates import load


@pytest.fixture(autouse=True)
def clean_registry():
    plugins.reset()
    yield
    plugins.reset()


# --- Section 2: a rule -----------------------------------------------------


@emits("house-no-weasel-words")
def check_weasel_words(doc, config):
    """Flag hedging language the style guide bans."""
    banned = config.option("house-no-weasel-words", "words", ["basically", "simply"])
    issues = []
    for block in doc.blocks:
        for run in getattr(block, "runs", []):
            for word in banned:
                if word in run.text.lower():
                    issues.append(
                        Issue("house-no-weasel-words", f"weasel word: {word}", "warning")
                    )
    return issues


def test_the_documented_rule_works_as_written(tmp_path):
    docfix.register_rule(check_weasel_words, family="structure")
    doc = tmp_path / "notes.md"
    doc.write_text("# T\n\nThis is basically fine.\n", encoding="utf-8")
    assert "house-no-weasel-words" in {i.rule for i in docfix.detect(str(doc))}


def test_the_documented_toml_controls_the_documented_rule(tmp_path):
    """Both blocks the doc shows under "configurable exactly like a built-in"."""
    docfix.register_rule(check_weasel_words, family="structure")
    doc = tmp_path / "notes.md"
    doc.write_text("# T\n\nThis is basically fine.\n", encoding="utf-8")

    severity = tmp_path / "severity.toml"
    severity.write_text('[rules]\n"house-no-weasel-words" = "error"\n', encoding="utf-8")
    issues = docfix.detect(str(doc), config=config_module.load(str(severity)))
    assert [i.severity for i in issues if i.rule == "house-no-weasel-words"] == ["error"]

    # The doc's [options] block, including its list value.
    options = tmp_path / "options.toml"
    options.write_text(
        '[options]\n"house-no-weasel-words" = { words = ["basically", "simply", "just"] }\n',
        encoding="utf-8",
    )
    config = config_module.load(str(options))
    assert config.option("house-no-weasel-words", "words", []) == [
        "basically",
        "simply",
        "just",
    ]


# --- Section 3: an adapter -------------------------------------------------


def read_path(path: str) -> Document:
    return Document(blocks=[Paragraph(runs=[Run("from rtf")])])


def write_path(doc: Document, template, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("rtf out\n")


def source_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


RTF = Adapter(
    name="rtf",
    extensions=(".rtf",),
    read_path=read_path,
    write_path=write_path,
    source_text=source_text,
)


def test_the_documented_adapter_registers_and_runs(tmp_path):
    docfix.register_adapter(RTF)
    assert ".rtf" in adapters.supported_extensions()

    source = tmp_path / "notes.rtf"
    source.write_text("anything", encoding="utf-8")
    docfix.format_file(str(source), output=str(tmp_path / "out.rtf"))
    assert (tmp_path / "out.rtf").read_text(encoding="utf-8") == "rtf out\n"


def test_the_documented_cross_format_claim_holds(tmp_path):
    """The doc promises `notes.rtf -> notes.pdf` works in both directions."""
    docfix.register_adapter(RTF)
    source = tmp_path / "notes.rtf"
    source.write_text("anything", encoding="utf-8")
    docfix.format_file(str(source), output=str(tmp_path / "out.md"))
    assert "from rtf" in (tmp_path / "out.md").read_text(encoding="utf-8")


def test_the_documented_override_claim_holds():
    """"Claiming an extension docfix already handles overrides the built-in"."""
    mine = Adapter(
        name="my-pdf",
        extensions=(".pdf",),
        read_path=read_path,
        write_path=write_path,
    )
    docfix.register_adapter(mine)
    assert adapters.for_path("report.pdf").name == "my-pdf"


# --- Section 4: the plugin entry point -------------------------------------


def test_the_documented_register_hook_shape_works():
    """What a `docfix.plugins` entry point resolves to: a no-argument callable
    that does the registering."""

    def register():
        docfix.register_adapter(RTF)
        docfix.register_rule(check_weasel_words)

    register()
    assert adapters.for_path("x.rtf").name == "rtf"
    assert check_weasel_words in __import__(
        "docfix.detect.rules", fromlist=["structure_rules"]
    ).structure_rules()


# --- Section 1: a template from a path -------------------------------------


def test_a_template_loads_from_any_path(tmp_path):
    """The doc's first claim: `-t ./house-style.yaml` needs no install."""
    template = tmp_path / "house-style.yaml"
    template.write_text(
        "name: house-style\n"
        "fonts:\n"
        '  body: {family: "Lato, sans-serif", size: 11}\n'
        "markdown:\n"
        '  bullet_marker: "-"\n',
        encoding="utf-8",
    )
    assert load(str(template)).name == "house-style"
