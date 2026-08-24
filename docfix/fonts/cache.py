"""The pinned font cache: reproducible PDF output without shipping fonts.

PDF output otherwise depends on whichever fonts a machine happens to have, so
the same CV renders differently on a laptop and in CI. `docfix fonts install`
fetches the set named in `pinned.yaml` into a user cache that the pool prefers,
making output identical anywhere the install has been run.

The repository still ships no font files. It ships a manifest of URLs pinned to
a commit SHA, each with a SHA-256 the download is checked against, and the
licence text is stored beside the font. A checksum mismatch is a hard failure:
the whole point is knowing exactly which bytes rendered a document.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import yaml  # type: ignore[import-untyped]

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "pinned.yaml")

CACHE_ENV = "DOCFIX_FONT_CACHE"
DOWNLOAD_TIMEOUT = 60
CHUNK = 1 << 16


class CacheError(RuntimeError):
    """Raised when the pinned set cannot be installed or verified."""


@dataclass(frozen=True)
class PinnedFace:
    family: str
    style: str
    url: str
    sha256: str
    size: int

    @property
    def filename(self) -> str:
        return f"{self.family.replace(' ', '')}-{self.style}.ttf"


@dataclass
class PinnedFamily:
    family: str
    category: str
    licence: str
    licence_url: str
    faces: list[PinnedFace] = field(default_factory=list)


def cache_dir() -> str:
    """Where the pinned fonts live.

    `DOCFIX_FONT_CACHE` overrides, which is what the tests use.
    """
    override = os.environ.get(CACHE_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))

    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    elif sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "docfix", "fonts")


def load_manifest(path: str = MANIFEST_PATH) -> list[PinnedFamily]:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    families = []
    for entry in data.get("families", []):
        families.append(
            PinnedFamily(
                family=entry["family"],
                category=entry.get("category", "sans"),
                licence=entry.get("licence", "unknown"),
                licence_url=entry.get("licence_url", ""),
                faces=[
                    PinnedFace(
                        family=entry["family"],
                        style=face["style"],
                        url=face["url"],
                        sha256=face["sha256"],
                        size=int(face.get("size", 0)),
                    )
                    for face in entry.get("faces", [])
                ],
            )
        )
    return families


def pinned_families(path: str = MANIFEST_PATH) -> set[str]:
    """Family names the manifest pins, for telling pinned from system fonts."""
    return {family.family for family in load_manifest(path)}


def digest(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _fetch(url: str, destination: str) -> None:
    """Download to a temporary name, then move it into place.

    Writing straight to the destination leaves a truncated file behind when the
    connection drops mid-stream, and nothing re-checks a checksum after install
    -- so a partial font that still parses would be used, and reported as
    pinned. Only a complete download is ever visible under the real name.
    """
    partial = f"{destination}.part"
    try:
        with (
            urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response,
            open(partial, "wb") as handle,
        ):
            while chunk := response.read(CHUNK):
                handle.write(chunk)
        os.replace(partial, destination)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        with contextlib.suppress(OSError):
            os.remove(partial)
        raise CacheError(f"could not download {url}: {exc}") from exc


def face_path(face: PinnedFace, directory: str | None = None) -> str:
    return os.path.join(directory or cache_dir(), face.filename)


def verify(directory: str | None = None, path: str = MANIFEST_PATH) -> list[str]:
    """Problems with the cache as it stands. Empty means it matches the manifest."""
    directory = directory or cache_dir()
    problems: list[str] = []
    for family in load_manifest(path):
        for face in family.faces:
            target = face_path(face, directory)
            if not os.path.exists(target):
                problems.append(f"{face.filename}: not installed")
                continue
            actual = digest(target)
            if actual != face.sha256:
                problems.append(
                    f"{face.filename}: checksum mismatch "
                    f"(expected {face.sha256[:12]}…, got {actual[:12]}…)"
                )
    return problems


def install(
    directory: str | None = None,
    path: str = MANIFEST_PATH,
    force: bool = False,
) -> tuple[list[str], list[str]]:
    """Download the pinned set. Returns (installed, skipped-already-present).

    Verifies the checksum of every file it writes and deletes anything that
    fails, so a partial or tampered download never lands in the cache. The
    licence text is stored beside the fonts.
    """
    directory = directory or cache_dir()
    os.makedirs(directory, exist_ok=True)

    installed: list[str] = []
    skipped: list[str] = []

    for family in load_manifest(path):
        for face in family.faces:
            target = face_path(face, directory)
            if not force and os.path.exists(target) and digest(target) == face.sha256:
                skipped.append(face.filename)
                continue

            _fetch(face.url, target)
            actual = digest(target)
            if actual != face.sha256:
                os.remove(target)
                raise CacheError(
                    f"{face.filename}: checksum mismatch after download "
                    f"(expected {face.sha256}, got {actual}); the file was discarded"
                )
            installed.append(face.filename)

        if family.licence_url:
            licence_file = os.path.join(
                directory, f"{family.family.replace(' ', '')}-LICENCE.txt"
            )
            if force or not os.path.exists(licence_file):
                # A missing licence text must not fail the install; the licence
                # the font itself declares is what the gate actually reads.
                with contextlib.suppress(CacheError):
                    _fetch(family.licence_url, licence_file)

    if installed:
        # The pool is memoised, and it just changed.
        from docfix.fonts.registry import reset_pool

        reset_pool()

    return installed, skipped


def is_installed(directory: str | None = None, path: str = MANIFEST_PATH) -> bool:
    return not verify(directory, path)
