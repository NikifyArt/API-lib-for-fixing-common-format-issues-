# Changelog

Notable changes to `docfix`. Format follows [Keep a Changelog][kac], and this
project uses [Semantic Versioning][semver].

docfix is pre-1.0: the public API may still change, and a breaking change will
be called out under **Changed** with a migration note rather than slipped in.

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html

## Unreleased

Nothing yet.

## 0.1.0 — 2026-08-24

First release. Nine phases of work, summarised below newest first.

**What you get:** `docfix format` and `docfix check` over Markdown, Word and
PDF; eight templates including three for CVs; nineteen rules that repair
cosmetic problems and only report semantic ones; a config file; reproducible
PDF output from a pinned, checksummed font set; and an extension API that lets
you add a format, a rule or a template without forking.

```bash
pip install docfix          # Markdown
pip install "docfix[all]"   # adds PDF and Word
```

### Fixed — DOCX round trip (phase 9)

Three round-trip losses that changed what a document *said*, rather than how it
looked — the one thing docfix promises not to do.

- **Nested lists were flattened.** Depth now rides in Word's `List Bullet 2`
  and `List Bullet 3` styles and is rebuilt into a tree on read. Levels beyond
  3 draw at 3, because falling back to level 1 read back as flat.
- **Code blocks came back as prose**, which then had every prose rule applied
  to them. They are written with a created `Code` paragraph style, and the
  fence's language rides in the style name (`Code python`) since DOCX has
  nowhere else to keep it. Blank lines inside a block survive, and two adjacent
  blocks in different languages no longer merge.
- **A thematic break came back as a row of dash characters** — literal content
  a second pass would have kept. It is now a bottom border, which is how Word
  actually draws a rule.
- **An empty heading vanished.** It is structure, not spacing, and
  `check_empty_headings` could never report one the reader had already dropped.

Closes BUG-002, BUG-003 and BUG-004.

### Added — extensibility (phase 8)

- **Registration API.** `register_adapter()`, `register_rule()`,
  `unregister_adapter()`, `unregister_rule()` on the top-level `docfix`
  namespace. Adding a format or a rule no longer means editing a list inside
  docfix, so a fork's diff stays out of the files upstream keeps changing.
- **Plugin entry points.** A separate distribution declaring a
  `docfix.plugins` entry point extends docfix with no source change and no
  fork. Plugins load lazily and exactly once; one that raises is contained,
  recorded, and reported by `docfix rules` rather than breaking anything.
- **A registered adapter overrides a built-in extension**, so a fork can
  substitute its own reader for a format docfix already handles.
- **`py.typed`.** docfix has been fully type-hinted throughout; the marker means
  a consumer's mypy or pyright now actually sees those hints instead of treating
  every import as `Any`.
- **List-valued rule options** in `docfix.toml` — third-party rules routinely
  need one (banned words, ignore patterns). Nested tables are still refused.
- **`docs/EXTENDING.md`**, `CONTRIBUTING.md`, `SECURITY.md`, and this file.
- `docfix rules` marks plugin-provided rules and reports plugins that failed
  to load.
- A CI coverage gate at 90%, and a packaging step that type-checks the built
  wheel from a consumer's side in both dependency shapes.

### Deprecated

- `docfix.adapters.ADAPTERS`, `docfix.detect.rules.STRUCTURE_RULES` and
  `SOURCE_RULES`, and `docfix.cv.CV_RULES`. They still work and now return a
  live view including registered extensions rather than a stale snapshot, but
  they warn. Use `adapters()`, `structure_rules()`, `source_rules()` and
  `cv_rules()`.

### Added — reproducible fonts (phase 7)

- `docfix fonts install` fetches a pinned set of open-licensed families into a
  user cache, verifying a SHA-256 per file and saving each licence alongside it.
  The repo ships no font binaries.
- Cached fonts take precedence over system ones, so the same document renders
  identically on any machine that has run the install.
- `docfix fonts --reproducible` names any family being served from the system;
  `docfix fonts install --check` verifies a cache without downloading.
- A `font-not-pinned` issue when output used a machine font, and a
  `reproducible` template naming only pinned families.

### Added — configuration (phase 6)

- `docfix.toml` or `[tool.docfix]` in `pyproject.toml`, found by searching
  upward. `--config` overrides the search, `--no-config` ignores it.
- Per-rule disable and severity overrides, per-rule options, and `exclude`
  globs.
- Batch processing over directories, `--out-dir`, and `--diff` (writes nothing,
  exits 1 if anything would change, so it works as a CI check).
- `docfix rules`, the inventory of every rule and its id.
- A documented exit-code contract, pinned by a test.

### Added — CV layer (phase 5)

- A section/entry model and eight résumé rules, all report-only, plus the
  `cv-classic`, `cv-modern` and `cv-compact` templates.
- CV detection is conservative on purpose: two or more recognised sections, one
  of them experience or education. `--cv`/`--no-cv` force it either way.

### Added — DOCX (phase 4)

- Read and write Word documents; styles carry the structure.
- Headings, both list kinds, tables, inline marks and hyperlinks survive a round
  trip. Code blocks, thematic breaks and nested lists do not — see
  `docs/BUG-REPORTS.md`.

### Added — fonts (phase 3)

- A font pool with coverage-based span segmentation, because reportlab performs
  no fallback and emits a missing glyph as `\x00` silently.
- Licence gating: `fsType` decides whether a font may be embedded at all, and a
  catalogue of recognised open licences decides whether one may be chosen
  automatically. Licences are read from what the font declares, never inferred
  from its name.
- `--embed-cjk` embeds an installed open-licensed CJK font ahead of the
  unembedded CID entries.

### Added — PDF (phase 2)

- A per-page risk scan that runs before any conversion; unattended, docfix
  refuses rather than guessing, and `--yes` overrides.
- Extraction to the IR and generation of a new PDF. PDF is never edited in
  place — it is fixed-layout, and that is not achievable.
- `--keep-intermediate` extracts to Markdown so you can check and correct it
  before formatting.

### Added — core (phase 1)

- The document IR, the template loader and presets, the Markdown adapter,
  detection and fixing, and the CLI.

### Fixed

- **BUG-007:** `fsType` was read past the end of a short OS/2 table, taking the
  value from whatever table followed. `fsType` is the gate deciding whether a
  font may be embedded at all, so an invented value could have granted a
  permission the vendor never gave.
- Shipping `py.typed` initially pushed 24 type errors from docfix's internals
  into consumers' builds. The package now type-checks clean from the outside,
  and CI keeps it that way.
- **BUG-006:** list markers rendered in base-14 Helvetica at the wrong size
  while their text used the document font, because reportlab styles a bullet
  separately. This also defeated reproducible output for any document with a
  list, since Helvetica is never embedded.
- **BUG-001:** cross-format output wrote the wrong format silently —
  `format_file` resolved one adapter from the input path and used it for both
  reading and writing. The writer now comes from the output path.
- Word's bullet and numbered buttons both produce the `List Paragraph` style, so
  matching that style as ordered turned every Word bullet list into `1. 2. 3.`
  on the way out.
- `--out-dir` joined the output directory with the basename alone, so `a/README`
  and `b/README` silently overwrote each other.
- Batch mode had no per-file isolation: one unreadable file aborted the whole
  run.
