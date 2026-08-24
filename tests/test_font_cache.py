"""The pinned font cache: manifest, install, verification, and reproducibility.

The install path is exercised offline with `file://` URLs in a temporary
manifest, so these tests need no network. One test does hit the real manifest
and skips when the network is unavailable.
"""

import hashlib
import os
import shutil

import pytest

import docfix
from docfix.fonts import cache
from docfix.fonts.registry import load_catalog

pytestmark = pytest.mark.usefixtures("isolated_cache")


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Never touch the developer's real font cache.

    The pool is memoised process-wide, so it is reset around every test or one
    test's cache would leak into the next.
    """
    from docfix.fonts.registry import reset_pool

    monkeypatch.setenv(cache.CACHE_ENV, str(tmp_path / "cache"))
    reset_pool()
    yield tmp_path / "cache"
    reset_pool()


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------


def test_manifest_loads():
    families = cache.load_manifest()
    assert families
    assert {f.family for f in families} == cache.pinned_families()


def test_every_pinned_face_has_a_real_checksum():
    for family in cache.load_manifest():
        assert family.faces, f"{family.family} pins no faces"
        for face in family.faces:
            assert len(face.sha256) == 64, f"{face.filename}: not a sha256"
            assert int(face.sha256, 16) >= 0, f"{face.filename}: not hex"
            assert face.size > 0


def test_every_pinned_url_is_immutable():
    """A branch or tag can move; a commit SHA cannot. The checksum is the
    backstop, but pinning the URL means it should never even be reached."""
    for family in cache.load_manifest():
        for face in family.faces:
            assert "/main/" not in face.url, f"{face.filename}: pinned to a branch"
            assert "/master/" not in face.url


def test_every_pinned_family_declares_an_open_licence():
    """docfix must not ship a manifest pointing at anything it could not
    auto-select from a user's own disk."""
    known = {entry.id for entry in load_catalog().licenses if entry.open}
    for family in cache.load_manifest():
        assert family.licence in known, f"{family.family}: {family.licence} is not open"


# --------------------------------------------------------------------------
# Cache location
# --------------------------------------------------------------------------


def test_env_var_overrides_the_cache_location(tmp_path, monkeypatch):
    monkeypatch.setenv(cache.CACHE_ENV, str(tmp_path / "elsewhere"))
    assert cache.cache_dir() == str(tmp_path / "elsewhere")


def test_default_cache_location_is_under_a_cache_root(monkeypatch):
    monkeypatch.delenv(cache.CACHE_ENV, raising=False)
    assert "docfix" in cache.cache_dir()


# --------------------------------------------------------------------------
# Install and verification, offline
# --------------------------------------------------------------------------


def _local_manifest(tmp_path, payload=b"a font file"):
    """A manifest whose URLs are file:// so install needs no network."""
    source = tmp_path / "source.ttf"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    manifest = tmp_path / "pinned.yaml"
    manifest.write_text(
        "families:\n"
        "  - family: Test Family\n"
        "    category: sans\n"
        "    licence: OFL-1.1\n"
        '    licence_url: ""\n'
        "    faces:\n"
        "      - style: regular\n"
        f'        url: "file://{source}"\n'
        f'        sha256: "{digest}"\n'
        f"        size: {len(payload)}\n"
    )
    return str(manifest), digest


def test_install_downloads_and_verifies(tmp_path, isolated_cache):
    manifest, _ = _local_manifest(tmp_path)
    installed, skipped = cache.install(path=manifest)

    assert installed == ["TestFamily-regular.ttf"]
    assert not skipped
    assert not cache.verify(path=manifest)
    assert cache.is_installed(path=manifest)


def test_a_second_install_skips_what_is_already_correct(tmp_path):
    manifest, _ = _local_manifest(tmp_path)
    cache.install(path=manifest)
    installed, skipped = cache.install(path=manifest)
    assert not installed and skipped == ["TestFamily-regular.ttf"]


def test_force_redownloads(tmp_path):
    manifest, _ = _local_manifest(tmp_path)
    cache.install(path=manifest)
    installed, skipped = cache.install(path=manifest, force=True)
    assert installed and not skipped


def test_verify_reports_a_missing_file(tmp_path):
    manifest, _ = _local_manifest(tmp_path)
    problems = cache.verify(path=manifest)
    assert len(problems) == 1 and "not installed" in problems[0]


def test_verify_detects_a_corrupted_file(tmp_path, isolated_cache):
    manifest, _ = _local_manifest(tmp_path)
    cache.install(path=manifest)

    target = os.path.join(str(isolated_cache), "TestFamily-regular.ttf")
    with open(target, "wb") as handle:
        handle.write(b"tampered")

    problems = cache.verify(path=manifest)
    assert len(problems) == 1 and "checksum mismatch" in problems[0]
    assert not cache.is_installed(path=manifest)


def test_a_checksum_mismatch_on_download_is_fatal_and_discards_the_file(tmp_path, isolated_cache):
    """A partial or tampered download must never land in the cache."""
    source = tmp_path / "source.ttf"
    source.write_bytes(b"the real bytes")
    manifest = tmp_path / "pinned.yaml"
    manifest.write_text(
        "families:\n"
        "  - family: Wrong\n    category: sans\n    licence: OFL-1.1\n"
        '    licence_url: ""\n    faces:\n      - style: regular\n'
        f'        url: "file://{source}"\n'
        f'        sha256: "{"0" * 64}"\n        size: 14\n'
    )

    with pytest.raises(cache.CacheError, match="checksum mismatch"):
        cache.install(path=str(manifest))
    assert not os.path.exists(os.path.join(str(isolated_cache), "Wrong-regular.ttf"))


def test_an_unreachable_url_is_reported_not_crashed(tmp_path):
    manifest = tmp_path / "pinned.yaml"
    manifest.write_text(
        "families:\n"
        "  - family: Gone\n    category: sans\n    licence: OFL-1.1\n"
        '    licence_url: ""\n    faces:\n      - style: regular\n'
        f'        url: "file://{tmp_path}/does-not-exist.ttf"\n'
        f'        sha256: "{"0" * 64}"\n        size: 1\n'
    )
    with pytest.raises(cache.CacheError, match="could not download"):
        cache.install(path=str(manifest))


# --------------------------------------------------------------------------
# The pool prefers pinned fonts
# --------------------------------------------------------------------------


def _real_cache_dir() -> str:
    """Where `docfix fonts install` actually writes, ignoring test isolation.

    The fixtures below point DOCFIX_FONT_CACHE at a temporary directory, so the
    real location has to be computed with that override removed.
    """
    saved = os.environ.pop(cache.CACHE_ENV, None)
    try:
        return cache.cache_dir()
    finally:
        if saved is not None:
            os.environ[cache.CACHE_ENV] = saved


def _install_real(isolated_cache):
    """Populate the isolated cache from a real installed one, or skip.

    Sourced from wherever `docfix fonts install` writes -- which is what CI runs
    -- rather than a hand-made path. Pointing this at a fixed /tmp directory
    meant every test below skipped in CI while the workflow claimed to exercise
    the pinned path, and /tmp is world-writable besides.
    """
    shared = os.environ.get("DOCFIX_TEST_FONT_CACHE") or _real_cache_dir()
    if not os.path.isdir(shared) or cache.verify(shared):
        pytest.skip(
            "no verified pinned cache available; run `docfix fonts install` "
            "(CI does this in the coverage and PDF jobs)"
        )
    shutil.copytree(shared, str(isolated_cache), dirs_exist_ok=True)


def test_pool_marks_pinned_families(isolated_cache):
    from docfix.fonts.registry import build_pool

    _install_real(isolated_cache)
    pool = build_pool()
    pinned = {f.name for f in pool.pinned_families()}
    assert pinned, "the cache should have supplied families"
    assert pinned <= cache.pinned_families()
    assert all(f.pinned is False for f in pool.unpinned_families())


def test_a_pinned_family_beats_a_system_one_of_the_same_name(isolated_cache):
    """The cache is searched first, so output does not depend on the machine."""
    from docfix.fonts.registry import build_pool

    _install_real(isolated_cache)
    pool = build_pool()
    for family in pool.pinned_families():
        regular = family.regular
        assert regular and str(isolated_cache) in regular.path


def test_disabling_the_cache_falls_back_to_system_fonts(isolated_cache):
    from docfix.fonts.registry import build_pool

    _install_real(isolated_cache)
    assert not build_pool(use_cache=False).pinned_families()


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def test_the_reproducible_preset_names_only_pinned_families():
    template = docfix.load("reproducible")
    named = set()
    for role in ("body", "heading", "mono"):
        named |= {
            part.strip().strip("'\"")
            for part in template.fonts[role]["family"].split(",")
        }
    named |= set(template.fonts.get("fallback") or [])
    assert named <= cache.pinned_families(), f"unpinned: {named - cache.pinned_families()}"


def test_unpinned_fonts_are_reported(isolated_cache):
    pytest.importorskip("reportlab")
    from docfix.adapters import pdf as pdf_adapter
    from docfix.ir import Document, Paragraph, Run

    _install_real(isolated_cache)
    doc = Document(blocks=[Paragraph(runs=[Run("Hello world")])])

    # `formal` names system families, so its output is machine-dependent.
    reported = {i.rule for i in pdf_adapter.coverage_issues(doc, docfix.load("formal"))}
    assert "font-not-pinned" in reported

    # `reproducible` names only pinned ones.
    clean = {i.rule for i in pdf_adapter.coverage_issues(doc, docfix.load("reproducible"))}
    assert "font-not-pinned" not in clean


def test_font_not_pinned_is_listed_by_the_rules_command(capsys):
    from docfix.cli import main

    main(["rules", "--no-config"])
    assert "font-not-pinned" in capsys.readouterr().out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_check_reports_a_missing_cache(capsys):
    from docfix.cli import EXIT_ISSUES, main

    assert main(["fonts", "install", "--check"]) == EXIT_ISSUES
    assert "not installed" in capsys.readouterr().err


def test_cli_reproducible_fails_when_the_cache_is_empty(capsys):
    from docfix.cli import EXIT_ISSUES, main

    assert main(["fonts", "--reproducible"]) == EXIT_ISSUES
    assert "MISSING" in capsys.readouterr().out


def test_cli_reproducible_passes_with_the_cache(isolated_cache, capsys):
    from docfix.cli import EXIT_OK, main

    _install_real(isolated_cache)
    assert main(["fonts", "--reproducible"]) == EXIT_OK
    assert "reproducible" in capsys.readouterr().out


def test_cli_fonts_listing_shows_the_source(isolated_cache, capsys):
    from docfix.cli import main

    _install_real(isolated_cache)
    main(["fonts"])
    out = capsys.readouterr().out
    assert "pinned" in out and "system" in out


@pytest.mark.parametrize("family", sorted(cache.pinned_families()))
def test_the_real_manifest_installs(tmp_path, family):
    """Hits the network; skipped when it is unavailable."""
    import urllib.error
    import urllib.request

    face = next(
        f for fam in cache.load_manifest() if fam.family == family for f in fam.faces
    )
    try:
        with urllib.request.urlopen(face.url, timeout=20) as response:
            payload = response.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        pytest.skip(f"network unavailable: {exc}")

    assert hashlib.sha256(payload).hexdigest() == face.sha256, (
        f"{face.filename}: the pinned checksum no longer matches what the URL serves"
    )
