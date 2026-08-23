"""The font pool.

`docfix` ships no font files. It uses what the machine already has, and only
auto-selects fonts whose own declared licence is a recognised open one. A font
whose vendor forbids embedding (`fsType`) is refused outright.

    >>> from docfix import fonts
    >>> fonts.pool().names()[:3]
"""

from docfix.fonts.coverage import (
    FontOption,
    build_chain,
    in_cjk,
    resolve_spans,
)
from docfix.fonts.registry import (
    Catalog,
    Family,
    FontError,
    License,
    Pool,
    load_catalog,
    pool,
    register,
    register_cid,
    reset_registrations,
)
from docfix.fonts.sfnt import FontFileError, FontInfo, describe_fs_type

__all__ = [
    "Catalog",
    "Family",
    "FontError",
    "FontFileError",
    "FontInfo",
    "FontOption",
    "License",
    "Pool",
    "build_chain",
    "describe_fs_type",
    "in_cjk",
    "load_catalog",
    "pool",
    "register",
    "register_cid",
    "reset_registrations",
    "resolve_spans",
]
