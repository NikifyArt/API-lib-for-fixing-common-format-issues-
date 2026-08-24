import pytest

from docfix.templates import Template, TemplateError, from_dict, list_presets, load


def test_all_presets_load():
    """Not an exact set: adding a preset is meant to be a YAML file and nothing
    else, so freezing the list here would make that a two-file change."""
    names = list_presets()
    assert {"formal", "friendly", "minimal", "technical"} <= set(names)
    for name in names:
        template = load(name)
        assert isinstance(template, Template)
        assert template.name == name
        assert template.description, f"{name} should describe when to use it"


def test_presets_carry_style_for_every_writer():
    """Markdown needs markers; the DOCX/PDF writers need fonts, spacing, colors."""
    for name in list_presets():
        template = load(name)
        assert template.markdown.bullet_marker
        assert {"body", "heading", "mono"} <= set(template.fonts)
        assert "line_height" in template.spacing
        assert "text" in template.colors


def test_load_from_path(tmp_path):
    path = tmp_path / "custom.yaml"
    path.write_text("name: custom\nmarkdown:\n  bullet_marker: '*'\n")
    template = load(str(path))
    assert template.name == "custom"
    assert template.markdown.bullet_marker == "*"


def test_unknown_name_lists_the_alternatives():
    with pytest.raises(TemplateError, match="available presets"):
        load("nope")


@pytest.mark.parametrize(
    "data, message",
    [
        ({"name": "x", "markdown": {"heading_style": "wavy"}}, "heading_style"),
        ({"name": "x", "markdown": {"bullet_marker": "@"}}, "bullet_marker"),
        ({"name": "x", "markdown": {"wrap_width": -1}}, "wrap_width"),
        ({"name": "x", "markdown": {"nonsense": 1}}, "unknown markdown keys"),
        # Every rejection an adopter writing their own template can hit. The
        # message has to name what is wrong, since the template is theirs and
        # docfix cannot guess what they meant.
        ({"name": "x", "markdown": "not a mapping"}, "'markdown' must be a mapping"),
        ({"name": "x", "fonts": "not a mapping"}, "'fonts' must be a mapping"),
        ({"name": "x", "fonts": {"bodyy": {}}}, "unknown font keys"),
        ({"name": "x", "fonts": {"fallback": "not a list"}}, "must be a list of family"),
        ({"name": "x", "fonts": {"fallback": [1, 2]}}, "must be a list of family"),
        ({"name": "x", "fonts": {"cjk": ["not a string"]}}, "'fonts.cjk' must be a font"),
        ({"name": "x", "fonts": {"embed_cjk": "yes"}}, "'fonts.embed_cjk' must be true"),
        ({"name": "x", "fonts": {"body": "Lato"}}, "'fonts.body' must be a mapping"),
        ({"name": "x", "fonts": {"heading": ["Lato"]}}, "'fonts.heading' must be a mapping"),
        ({"name": "x", "fonts": {"mono": 12}}, "'fonts.mono' must be a mapping"),
    ],
)
def test_invalid_templates_are_rejected(data, message):
    with pytest.raises(TemplateError, match=message):
        from_dict(data)


def test_a_non_mapping_template_is_rejected():
    with pytest.raises(TemplateError, match="mapping at the top level"):
        from_dict(["not", "a", "mapping"], name="x")


def test_a_file_without_a_name_key_falls_back_to_its_filename(tmp_path):
    """A template loaded by path need not repeat its own name."""
    path = tmp_path / "house-style.yaml"
    path.write_text("markdown:\n  bullet_marker: '-'\n", encoding="utf-8")
    assert load(str(path)).name == "house-style"


def test_missing_name_is_rejected():
    with pytest.raises(TemplateError, match="name"):
        from_dict({"markdown": {}})


def test_malformed_yaml_is_reported_clearly(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: x\n  bad: [unclosed\n")
    with pytest.raises(TemplateError, match="not valid YAML"):
        load(str(path))
