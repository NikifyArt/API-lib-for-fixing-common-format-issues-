import pytest

from docfix.templates import Template, TemplateError, from_dict, list_presets, load


def test_all_presets_load():
    names = list_presets()
    assert set(names) == {"formal", "friendly", "minimal", "technical"}
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
    ],
)
def test_invalid_templates_are_rejected(data, message):
    with pytest.raises(TemplateError, match=message):
        from_dict(data)


def test_missing_name_is_rejected():
    with pytest.raises(TemplateError, match="name"):
        from_dict({"markdown": {}})


def test_malformed_yaml_is_reported_clearly(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: x\n  bad: [unclosed\n")
    with pytest.raises(TemplateError, match="not valid YAML"):
        load(str(path))
