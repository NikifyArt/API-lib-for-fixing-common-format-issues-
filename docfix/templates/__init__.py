"""Template loading. Presets live in `presets/` as YAML data files."""

from docfix.templates.loader import (
    MarkdownStyle,
    Template,
    TemplateError,
    from_dict,
    list_presets,
    load,
)

__all__ = [
    "MarkdownStyle",
    "Template",
    "TemplateError",
    "from_dict",
    "list_presets",
    "load",
]
