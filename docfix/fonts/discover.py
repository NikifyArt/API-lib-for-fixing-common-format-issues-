"""Finding font files on the machine.

`docfix` ships no fonts. It uses what is already installed, which keeps the
package small and sidesteps redistribution entirely -- the only fonts involved
are ones the user's own system already has a licence for.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator

from docfix.fonts import sfnt

FONT_SUFFIXES = (".ttf", ".ttc", ".otf")

# Where each platform keeps fonts. Missing directories are skipped.
LINUX_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts")
MAC_DIRS = ("/System/Library/Fonts", "/Library/Fonts", "~/Library/Fonts")
WINDOWS_DIRS = ("C:\\Windows\\Fonts", "~\\AppData\\Local\\Microsoft\\Windows\\Fonts")

ENV_PATH = "DOCFIX_FONT_PATH"

MAX_FILES = 4000  # A guard against pathological font directories.


def _platform_dirs() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return MAC_DIRS
    if sys.platform.startswith("win"):
        return WINDOWS_DIRS
    return LINUX_DIRS


def reportlab_font_dir() -> str | None:
    """reportlab bundles Bitstream Vera, so there is always at least one font."""
    try:
        import reportlab
    except Exception:  # noqa: BLE001 - reportlab is an optional extra
        return None
    path = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    return path if os.path.isdir(path) else None


def search_dirs(extra: list[str] | None = None) -> list[str]:
    """Every directory to search, in priority order, deduplicated."""
    candidates: list[str] = list(extra or [])

    from_env = os.environ.get(ENV_PATH, "")
    if from_env:
        candidates.extend(part for part in from_env.split(os.pathsep) if part)

    candidates.extend(_platform_dirs())

    bundled = reportlab_font_dir()
    if bundled:
        candidates.append(bundled)

    seen: set[str] = set()
    resolved: list[str] = []
    for entry in candidates:
        path = os.path.abspath(os.path.expanduser(entry))
        if path not in seen and os.path.isdir(path):
            seen.add(path)
            resolved.append(path)
    return resolved


def font_files(extra: list[str] | None = None) -> Iterator[str]:
    """Every font file found, without parsing any of them."""
    count = 0
    seen: set[str] = set()
    for directory in search_dirs(extra):
        for root, _dirs, files in os.walk(directory):
            for name in sorted(files):
                if not name.lower().endswith(FONT_SUFFIXES):
                    continue
                path = os.path.join(root, name)
                if path in seen:
                    continue
                seen.add(path)
                count += 1
                if count > MAX_FILES:
                    return
                yield path


def scan(extra: list[str] | None = None) -> list[sfnt.FontInfo]:
    """Parse the metadata of every discoverable font.

    Coverage is deliberately not read here -- parsing every cmap on a machine
    with hundreds of fonts is slow, and most will never be consulted. The
    registry loads coverage on demand.
    """
    found: list[sfnt.FontInfo] = []
    for path in font_files(extra):
        try:
            info = sfnt.read(path, with_coverage=False)
        except sfnt.FontFileError:
            continue  # Not a usable font; nothing to report.
        except OSError:
            continue
        found.append(info)
    return found
