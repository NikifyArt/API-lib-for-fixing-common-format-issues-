"""Loading and validating templates.

Templates are data, not code: a preset is a YAML file, and adding one never
requires touching program logic. Presets ship in `presets/`; users may also
pass a path to their own YAML file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

PRESET_DIR = os.path.join(os.path.dirname(__file__), "presets")

VALID_HEADING_STYLES = {"atx", "setext"}
VALID_BULLET_MARKERS = {"-", "*", "+"}


class TemplateError(ValueError):
    """Raised when a template file is missing, malformed, or invalid."""


@dataclass
class MarkdownStyle:
    """Presentation choices that apply to Markdown output."""

    bullet_marker: str = "-"
    emphasis_marker: str = "*"
    strong_marker: str = "**"
    heading_style: str = "atx"
    thematic_break: str = "---"
    code_fence: str = "```"
    # 0 means "never wrap"; reflowing prose is opt-in.
    wrap_width: int = 0


@dataclass
class Template:
    """A named formatting preset."""

    name: str
    description: str = ""
    markdown: MarkdownStyle = field(default_factory=MarkdownStyle)
    # Consumed by the DOCX and PDF writers; Markdown has no notion of them.
    fonts: dict[str, Any] = field(default_factory=dict)
    spacing: dict[str, Any] = field(default_factory=dict)
    colors: dict[str, Any] = field(default_factory=dict)


def _validate(style: MarkdownStyle, name: str) -> None:
    if style.heading_style not in VALID_HEADING_STYLES:
        raise TemplateError(
            f"template {name!r}: heading_style must be one of "
            f"{sorted(VALID_HEADING_STYLES)}, got {style.heading_style!r}"
        )
    if style.bullet_marker not in VALID_BULLET_MARKERS:
        raise TemplateError(
            f"template {name!r}: bullet_marker must be one of "
            f"{sorted(VALID_BULLET_MARKERS)}, got {style.bullet_marker!r}"
        )
    if style.wrap_width < 0:
        raise TemplateError(
            f"template {name!r}: wrap_width must be >= 0, got {style.wrap_width}"
        )


VALID_FONT_ROLES = {"body", "heading", "mono", "fallback", "cjk", "embed_cjk"}


def _validate_fonts(fonts: dict, name: str) -> None:
    """Check the font block's shape; family names are resolved at write time."""
    if not isinstance(fonts, dict):
        raise TemplateError(f"template {name!r}: 'fonts' must be a mapping")

    unknown = set(fonts) - VALID_FONT_ROLES
    if unknown:
        raise TemplateError(
            f"template {name!r}: unknown font keys {sorted(unknown)}; "
            f"valid keys are {sorted(VALID_FONT_ROLES)}"
        )

    fallback = fonts.get("fallback")
    if fallback is not None and (
        not isinstance(fallback, list) or not all(isinstance(f, str) for f in fallback)
    ):
        raise TemplateError(
            f"template {name!r}: 'fonts.fallback' must be a list of family names"
        )

    cjk = fonts.get("cjk")
    if cjk is not None and not isinstance(cjk, str):
        raise TemplateError(f"template {name!r}: 'fonts.cjk' must be a font name")

    embed = fonts.get("embed_cjk")
    if embed is not None and not isinstance(embed, bool):
        raise TemplateError(f"template {name!r}: 'fonts.embed_cjk' must be true or false")

    for role in ("body", "heading", "mono"):
        spec = fonts.get(role)
        if spec is not None and not isinstance(spec, dict):
            raise TemplateError(
                f"template {name!r}: 'fonts.{role}' must be a mapping with a 'family'"
            )


def from_dict(data: dict, name: str | None = None) -> Template:
    if not isinstance(data, dict):
        raise TemplateError(f"template {name!r}: expected a mapping at the top level")

    resolved = name or data.get("name")
    if not resolved:
        raise TemplateError("template is missing a 'name'")

    md_data = data.get("markdown") or {}
    if not isinstance(md_data, dict):
        raise TemplateError(f"template {resolved!r}: 'markdown' must be a mapping")

    known = set(MarkdownStyle.__dataclass_fields__)
    unknown = set(md_data) - known
    if unknown:
        raise TemplateError(
            f"template {resolved!r}: unknown markdown keys {sorted(unknown)}; "
            f"valid keys are {sorted(known)}"
        )

    style = MarkdownStyle(**md_data)
    _validate(style, resolved)

    fonts = data.get("fonts") or {}
    _validate_fonts(fonts, resolved)

    return Template(
        name=resolved,
        description=data.get("description", ""),
        markdown=style,
        fonts=fonts,
        spacing=data.get("spacing") or {},
        colors=data.get("colors") or {},
    )


def load(name_or_path: str) -> Template:
    """Load a template by preset name or by path to a YAML file."""
    path = name_or_path
    if not os.path.exists(path):
        path = os.path.join(PRESET_DIR, f"{name_or_path}.yaml")
    if not os.path.exists(path):
        raise TemplateError(
            f"unknown template {name_or_path!r}; available presets: "
            f"{', '.join(list_presets())}"
        )

    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise TemplateError(f"template {name_or_path!r} is not valid YAML: {exc}") from exc

    fallback = os.path.splitext(os.path.basename(path))[0]
    return from_dict(data, data.get("name") or fallback)


def list_presets() -> list[str]:
    """Names of the bundled presets, sorted."""
    if not os.path.isdir(PRESET_DIR):
        return []
    return sorted(
        os.path.splitext(entry)[0]
        for entry in os.listdir(PRESET_DIR)
        if entry.endswith((".yaml", ".yml"))
    )
