# CLAUDE.md

Guidance for Claude Code and other AI assistants working in this repository.

## What this project is

`docfix` — a library that finds and fixes common formatting problems in
documents. Prettier, but for `.md`, `.docx`, `.pdf`, and CVs.

It reads a document, reports the formatting problems it finds, repairs the safe
ones, applies a named template, and writes the result to a **new file**.

Three invariants hold everywhere in this codebase:

1. **The source file is never modified.** Output always goes to a new path
   (`notes.md` → `notes.formatted.md`). `format_file` refuses to write over its
   input. Do not add an in-place mode without an explicit decision from the user.
2. **Templates are data, not code.** Adding a preset means adding a YAML file to
   `docfix/templates/presets/` and nothing else. If a feature requires touching
   logic to add a preset, the design is wrong.
3. **Cosmetic problems are fixed; semantic ones are only reported.** Whitespace
   and marker inconsistency get repaired. Heading levels, mixed quotes, and
   missing alt text are reported and left alone — fixing them would change what
   the document says.

## Architecture

Every format converts into one intermediate representation. Detection, fixing,
and template application run on the IR **only**; adapters hold conversion logic
and no formatting rules.

```
input → Reader(fmt) → Document IR → Detect → Fix → ApplyTemplate → Writer(fmt) → new file
```

This is the load-bearing decision. A rule written once works for every format,
and a new format is one adapter rather than a rewrite. Do not put formatting
rules in an adapter.

```
docfix/
  __init__.py            public API: format_file(), format_text(), detect(),
                         list_templates(), register_adapter(), register_rule()
  plugins.py             the extension registry + `docfix.plugins` entry points
  py.typed               PEP 561 marker (DATA; must be in package-data)
  ir.py                  Document/Block/Run dataclasses; walk(), iter_runs(),
                         iter_block_sequences()
  config.py              docfix.toml / [tool.docfix]: rule control, options, exclude
  templates/
    loader.py            YAML → Template, with validation
    presets/*.yaml       formal, friendly, technical, minimal,
                         cv-classic, cv-modern, cv-compact
  detect/rules.py        structure_rules() (on the IR) + source_rules() (raw text)
  cv.py                  CV section/entry model + cv_rules() (report-only)
  fix/normalize.py       safe repairs only
  fonts/
    sfnt.py              standalone TTF reader: fsType, cmap, names, outline kind
    catalog.yaml         licence signatures + known families (DATA)
    pinned.yaml          the pinned font set: SHA-pinned URLs + checksums (DATA)
    cache.py             download, checksum, licence capture; the pinned cache
    discover.py          find font files; group faces into families
    registry.py          pool assembly, licence gate, reportlab registration
    coverage.py          per-span font choice; unrenderable reporting
  adapters/
    __init__.py          adapters(): registered first, then built-in;
                         for_path() dispatches on extension
    markdown.py          read + write (markdown-it-py in, canonical Markdown out)
    pdf.py               scan() risk report, read (pdfplumber), write (reportlab)
    docx.py              read + write (python-docx); styles carry the structure
  cli.py                 format / check / scan / fonts / rules / templates
tests/                   486 tests, 553 with all extras installed
```

### Things that will bite you

- **Two rule families.** Structure rules take a `Document`; source rules take raw
  text. Source rules exist because the parser dissolves trailing whitespace and
  blank-line runs before the IR ever sees them. Binary formats pass `source=None`
  and simply skip them.
- **Detection must not depend on normalization order.** `run_all()` is called on
  the un-normalized document in `format_file`, so a rule that only becomes
  visible after `normalize()` will silently never fire. `check_list_markers`
  handles both shapes deliberately — see the next point.
- **Changing a bullet marker mid-list starts a *new* list in CommonMark.** So
  `*`/`+`/`-` mixing arrives as several adjacent `ListBlock`s, not one list with
  mixed markers. `merge_adjacent_lists` fixes it; the detection rule checks both
  the adjacent-lists shape and the merged shape.
- **GFM normalizes table rows to the header width.** A ragged row cannot reach
  the IR through Markdown, so `check_table_shape` is exercised by building the IR
  directly. It guards the DOCX adapter and hand-built documents.
- **Fenced code is exempt from source rules** and is never reformatted. When
  scanning lines, do not filter fence blocks out before counting blank runs — the
  blanks on either side would look adjacent and report a run that is not there.
- **`Run.raw`** means "emit verbatim". It carries inline images, which the IR does
  not model structurally. Skip raw runs when escaping or rewriting markers.

### PDF specifics

- **PDF is never edited in place.** It is extracted to the IR and a new PDF is
  generated. Do not add an in-place path; it is not achievable for fixed layout.
- **`scan()` runs before any conversion.** The CLI shows the per-page risk report
  and asks. Unattended, it refuses rather than guessing — `--yes` overrides. Do
  not make a lossy conversion silent.
- **Extract tables first, then lines.** `_page_content` pulls tables out and
  excludes their bounding boxes from the line flow. Without that, table cells
  extract as loose words and get absorbed into the preceding paragraph or list
  item. This was a real bug, not a hypothetical.
- **Bullets often do not decode.** Symbol and dingbat fonts frequently lack a
  ToUnicode map, so a bullet arrives as `(cid:127)`. `BULLET_MARKER` matches the
  cid form deliberately; without it the bullets merge into one paragraph.
- **reportlab styles a bullet separately from its text.** `ParagraphStyle`
  defaults `bulletFontName` to Helvetica and `bulletFontSize` to 10, ignoring
  `fontName`/`fontSize`, so every list marker drew in a base-14 face at the
  wrong size until `_styles` named both. Set them on any new style that passes
  `bulletText`. It is not only cosmetic: Helvetica is never embedded, so the
  markers were machine-dependent, and `font-not-pinned` cannot see them —
  the bullet never goes through span resolution.
- **`"sans-serif"` contains `"serif"`.** Check sans before serif in `_base_font`
  or every sans stack maps to Times.
- **The running-header band is 15% of page height**, not 8%. A typical page has
  a 1-inch margin (~8.5% of A4), so a tighter band sits above the header and
  catches nothing.
- **PDF passes `source=None`**, so source-level rules are skipped — there is no
  raw text to scan.
- **Fonts come from the pool, not the base-14.** See the font section below.
- **`_require` catches `BaseException`, not `ImportError`.** A broken native
  dependency surfaces as a pyo3 panic, and a raw Rust traceback tells the user
  nothing about what to install. `tests/test_pdf.py` skips on the same basis —
  `pytest.importorskip` does not catch a panic and collection would fail.

### The extension layer

docfix is meant to be forked and adapted, so **every extension point is
additive**. Nothing about adding a format, a rule or a template requires editing
a core file — that is the whole design, not a convenience.

- **Why it matters.** An adopter whose diff lands in `adapters/__init__.py` or
  `detect/rules.py` conflicts with upstream on every pull, because those are the
  files we keep changing. Registration keeps their diff out of ours, so their
  fork rebases indefinitely. Do not add a hardcoded list of anything extensible.
- **`plugins.py` holds the registries.** It deliberately does not import
  `Adapter`, and validates duck-typed instead — `adapters/__init__.py` imports
  *it*, so the other direction would be a cycle.
- **Registered adapters are consulted before built-ins**, so claiming `.pdf`
  overrides ours. That is the point: a fork substitutes its own reader without
  touching the built-in one.
- **A rule without `@emits` is refused.** A config file addresses rules by id,
  so an undeclared rule could never be disabled or listed by `docfix rules` —
  it would be a second-class rule, and silently so.
- **Entry points load lazily and exactly once.** `load_plugins()` sets its
  memo *before* dispatching, because a plugin that imports docfix would
  otherwise re-enter and run every plugin twice.
- **A failing plugin is contained, never fatal.** It is recorded and reported by
  `docfix rules`. A third party's bug must not stop anyone formatting a
  document — hence `BaseException`, which also catches native-import panics.
- **The old tuple names are live views, not snapshots.** `ADAPTERS`,
  `STRUCTURE_RULES`, `SOURCE_RULES` and `CV_RULES` still work via module
  `__getattr__`, warn, and *include* registered extensions. A stale snapshot
  would silently miss them, which is worse than either keeping or removing them.
- **Shipping `py.typed` makes our type errors *their* errors.** The marker
  tells a consumer's mypy to read docfix's own source, so anything unclean here
  surfaces in their build — 24 errors, the first time it was tried. That is why
  the packaging job type-checks from the consumer's side, in both dependency
  shapes: the optional adapters are only imported in one of them. Keep
  `# type: ignore[...]` on the import line itself (ruff's isort will wrap a long
  one onto the next line, where mypy cannot see it) and before any `# noqa`,
  which otherwise swallows it.
- **`py.typed` is package-data.** Without the entry it is missing from the
  wheel, and a consumer's mypy silently treats docfix as untyped — invisible in
  an editable install, exactly like `catalog.yaml` was.

`docs/EXTENDING.md` is the adopter-facing version of this. Its examples are
executable; keep them that way.

### Configuration

- **Every rule declares the ids it emits**, via `@emits(...)` in
  `detect/rules.py`. One function often emits several — `check_heading_levels`
  reports both `heading-skip` and `heading-multiple-h1` — and the *ids*, not
  the function names, are what a config file addresses. A test asserts no
  emitted id is undeclared.
- **Disable and severity are a post-filter on Issues**, in `Config.apply`, not
  a skip of rule functions. That is the right granularity given the above, and
  it works for all rules with no signature churn.
- **An option may be a scalar or a list of scalars**, not a nested table.
  Third-party rules routinely need a list (banned words, ignore patterns);
  a nested table has no rule shape that wants it, and allowing one would make a
  typo look like configuration.
- **Options do need to reach the rule**, so every rule takes `(doc, config)`
  (or `(text, config)` for source rules). Uniform on purpose — a decorator or
  module-level state would be cleverer and worse to test.
- **`docfix rules` is the discovery surface.** A rule without a docstring shows
  a blank summary there, so give every new rule a one-liner.
- **`tomli` is a real dependency below 3.11**, marked in `pyproject.toml`.
  Without it `docfix.config` fails to import on 3.10, which CI tests.
- **Exclude globs use `fnmatch` with `normpath` first.** Walking `.` yields
  `./vendor/x.md`, and the leading `./` otherwise stops `vendor/**` matching.

### CV layer

- **A CV is a template category, not a format.** It arrives as `.md`, `.docx`
  or `.pdf` like anything else, so there is no CV adapter and never should be.
- **Every CV rule is report-only.** Reverse-chronological order, first-person
  phrasing and weak openers are matters of judgement — rewriting them would
  change what the document says. No `auto_fixable=True` in `cv.py`, and a test
  asserts it.
- **Detection is deliberately conservative**: two or more recognised sections,
  one of them experience or education. A README with a "Skills" heading is not
  a CV, and running résumé rules over one is noise. `cv=None` auto-detects,
  `True`/`False` force it either way.
- **CV rules are a third rule family**, alongside STRUCTURE_RULES and
  SOURCE_RULES, but they are *conditional* — `_cv_issues()` in `__init__.py`
  decides whether they run at all.
- **The section level is the shallowest heading below the name**, so h2 in a
  document titled with an h1. `_section_level` falls back to the shallowest
  present for CVs that skip the title.
- **An ongoing entry outranks every finished one** in `Entry.sort_key`, so
  "2010–Present" sorts above "2022–2024" rather than being treated as undated.

### DOCX specifics

- **Styles carry the structure.** `Heading N`, `List Bullet`, `List Number`,
  `Quote` are what identify a block on the way back in. Writing a blockquote as
  an indented `Normal` paragraph loses it — use the `Quote` style.
- **Iterate `body.iterchildren()`, not `.paragraphs`.** Only the element walk
  preserves document order when tables are interleaved with paragraphs.
- **No coverage problem, and no licence question.** DOCX stores text as XML, so
  any character survives; a font name is a request, not an embedding. The
  adapter therefore declares `coverage_issues=None`.
- **Word needs `w:eastAsia` set** on a style's `rFonts` or CJK falls back to the
  reader's default instead of the chosen family.
- **python-docx has no hyperlink API.** Reading follows `w:hyperlink` children;
  writing builds the element and relationship by hand.
- **Word's bullet *and* numbered buttons both produce `List Paragraph`.**
  Whether it is ordered lives in `w:numPr`, not the style, so the style alone
  cannot decide. It is read as a bullet — the commoner case — because the
  alternative silently renumbers a bulleted list, and inventing content is
  worse than under-reading it.
- **Everything the IR models survives the round trip** — headings (empty ones
  included), both list kinds *with their nesting*, code blocks with their
  language, thematic breaks, tables, inline marks and hyperlinks. Getting there
  meant giving each one a style the reader can key off, which is the general
  rule: **if the writer emits no style, the reader cannot tell what it was.**
  - Nesting rides in Word's `List Bullet 2`/`3`; `_nest_items` rebuilds the
    tree, since DOCX stores no tree. Beyond level 3 it draws at 3, because
    falling back to 1 would read back flat.
  - Code uses a created `Code` paragraph style, with the language in the name
    (`Code python`) — DOCX has nowhere else to keep it. One paragraph per line,
    so code paragraphs are collected *before* the empty-paragraph skip or a
    blank line inside a block is lost.
  - A thematic break is a bottom border, not a row of dashes. Dashes are
    literal content: they read back as a paragraph and a second pass keeps them.
  - An empty heading is structure, not spacing, and `check_empty_headings`
    cannot report one the reader dropped.
  The remaining loss is the `w:numPr` ambiguity above. See `docs/BUG-REPORTS.md`
  (BUG-002/003/004).

### Fonts

The base-14 fonts **silently corrupt** anything outside Latin-1 — `Zażółć`
became `Zanónn`, `→ ★ ≈ ✓` became `fi H » 3` — with no exception and no
warning. The font pool exists to fix that, and these are the traps in it:

- **reportlab performs no font fallback.** A missing glyph emits `\x00`
  silently. That is why text is split into spans and each span names a font
  that actually covers it. Never assume a single `fontName` is enough.
- **Segment by coverage, not by script.** Python exposes no Unicode Script
  property; asking each font's cmap what it covers is correct by construction
  and needs no range tables. The one exception is CID fonts, which have no file
  to read, so `SCRIPT_RANGES` describes them by block.
- **A `<font face>` tag overrides the family mapping**, so a span inside `<b>`
  loses its weight. `_span_markup` names the *bold face itself* via
  `FontOption.name_for()`. The base-14 path is the exception — there reportlab
  maps `<b>` itself, which is what `FontOption.use_tags` marks.
- **`loadable` requires `glyf` *and* `loca`**, not just a TrueType signature.
  Colour emoji fonts have the signature but store bitmaps; reportlab fails on
  them with "missing location table".
- **`--embed-cjk` swaps CID for a real font.** The default relies on the
  reader's own glyphs; the flag embeds an installed open-licensed CJK font
  ahead of the CID entries in the chain, so it wins. It never relaxes the
  licence gate, and falls back to CID (reporting
  `font-embed-cjk-unavailable`) when nothing qualifies. The flag is carried on
  a *copy* of the template — presets are shared objects and must not be
  mutated per call.
- **No single CID collection covers CJK.** Measured by rendering and extracting
  back: `HeiseiKakuGo-W5` covers Han (simplified and traditional) and kana but
  **not Hangul**; `HYSMyeongJo-Medium` covers Hangul but not simplified
  Chinese. Both are in the chain by default. `MSung-Light` did not survive a
  round trip and is deliberately unused.
- **A font stack is a list of alternatives.** Only report `font-unavailable`
  when *none* of a stack's families resolved — otherwise every machine without
  Georgia gets a warning about a document that renders fine.
- **`fsType` reads are bounded by the OS/2 table's declared length.** Reading
  past a short table takes the value from whatever table follows, and fsType is
  the gate deciding whether a font may be embedded at all — a value invented
  from adjacent bytes could grant a permission the vendor never gave. Too short
  means `None` ("not stated"), never a guess. See BUG-007.
- **`fsType` is parsed by `sfnt.py`, not reportlab.** reportlab does not expose
  it, and the gate must work on files reportlab refuses to load.

### The pinned cache (reproducible output)

- **The repo still ships no fonts.** `pinned.yaml` holds URLs and checksums,
  not binaries. That is what keeps the licence story simple: docfix
  redistributes nothing.
- **Every URL pins a commit SHA**, never a branch or tag, so the bytes cannot
  change under us. A test asserts no `/main/` or `/master/` in any URL.
- **A checksum mismatch is fatal and the file is discarded.** A partial or
  tampered download must never land in the cache — knowing exactly which bytes
  rendered a document is the whole point.
- **The cache is searched before the system**, so a pinned family beats a
  same-named system one.
- **`font-not-pinned` reports fonts actually *used*, not merely available.**
  The catalogue's fallback chain puts system families in every chain, so
  reporting availability would fire on every document. `_span_markup` records
  what each span was set in.
- **`install()` resets the memoised pool.** Without it a caller that installs
  fonts and then formats in the same process still sees the old set.
- **Adding a font means verifying it first** — TrueType outlines, `fsType`
  permitting embedding, and an open licence the font itself declares — then
  recording the real SHA-256. Never write a checksum you have not computed.

### Licensing rules — do not weaken these

`docfix` ships no fonts, so it redistributes nothing. Two separate checks:

1. **`fsType`** decides whether a font may be embedded *at all*. Restricted
   License Embedding is refused for everyone, including fonts the user names
   explicitly. An open licence does not override it.
2. **The catalogue** decides whether a font may be chosen *automatically*. Only
   a recognised open licence qualifies.

Licences are read from what the font declares (name IDs 13/14), never inferred
from a family name — there is a test asserting an impostor named
"Liberation Sans" that declares nothing stays `unknown`. Plain GPL without the
font embedding exception is `open: false` deliberately. Adding a licence means
adding a signature to `catalog.yaml`, and its terms must be **verified against
the font's own distribution**, never asserted from memory.

## Commands

```bash
pip install -e ".[dev]"       # pytest + ruff
pip install -e ".[dev,all]"   # adds pdfplumber, reportlab, python-docx
python -m pytest          # 486 tests (553 with all extras), ~9s
python -m ruff check .    # lint; must be clean
python -m docfix.cli --help
```

## Testing conventions

The two properties that matter most for a formatter, both enforced across every
template × sample combination:

- **Idempotence** — `format(format(x)) == format(x)`. A second pass must change
  nothing.
- **Round-trip stability** — `read(write(doc))` yields the same block structure.

Add both when you add an adapter. Also assert the source file is untouched.

Two more, added in phase 8:

- **`docs/EXTENDING.md`'s examples are pinned by `tests/test_extending_docs.py`.**
  They are the first thing an adopter runs, so every API call the doc makes is
  executed in the shape the doc makes it. The snippets elide bodies with `...`,
  so the file cannot be exec'd verbatim — the test carries the same calls
  instead. Change the extension API and that test tells you which examples
  went stale. It also caught the doc teaching a `source_text=lambda ...` that
  fails the project's own lint.
- **Malformed input gets tested with malformed input.** `tests/test_sfnt_robustness.py`
  builds corrupt fonts byte by byte rather than asserting that the parser is
  careful. That is how BUG-007 was found, and the interesting part of such a
  fixture is precisely which byte is wrong — which a committed binary hides.

Do not commit binary `.docx`/`.pdf` fixtures casually — they are opaque to diffs
and inflate the repo permanently. Generate them from text at test time; if a real
binary fixture is genuinely needed, keep it small and comment why it exists.

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | IR, templates, Markdown, detect/fix, CLI | **done** |
| 2 | PDF: risk scan, extraction, generation | **done** |
| 3 | Font pool, script coverage, licence gating | **done** |
| 4 | DOCX adapter (read + write) | **done** |
| 5 | CV/résumé template layer | **done** |
| 6 | Config file, rule control, batch, `--diff` | **done** |
| 7 | Reproducible font output | **done** |
| 8 | Extension API, plugins, `py.typed`, contributor docs | **done** |
| 9 | DOCX round-trip fidelity: nesting, code, rules, empty headings | **done** |

To add a format **that ships with docfix**: write the adapter with
`read_path`/`write_path` (plus `source_text` if the format is text), add it to
`BUILTIN_ADAPTERS` in `adapters/__init__.py`, and add its optional dependency to
`pyproject.toml`. Detection, fixing, and templates need no changes.

To add one **from outside** — which is what an adopter should do, and needs no
fork — call `docfix.register_adapter()` or ship a `docfix.plugins` entry point.
See `docs/EXTENDING.md`.

**PDF is not symmetrical with the others.** It is fixed-layout: generating from
the IR is clean, reading back recovers text and rough structure but is lossy.
Never promise a faithful in-place PDF restyle. The honest workflow, and the one
the CLI nudges toward, is `--keep-intermediate`: extract to Markdown, let the
user check and correct it, then format from there.

## CI

`.github/workflows/ci.yml` runs on pushes to `main` and to `claude/**`, and on
pull requests. Four jobs:

| Job | What it protects |
| --- | --- |
| `test` | 3 Python versions × 3 dependency shapes (`dev`, `dev,pdf`, `dev,all`) — the extras are optional, so the package must work without them |
| `coverage` | `pytest --cov` in the `dev,all` shape, gated at 90%. A floor to ratchet up, never down |
| `lint` | `ruff` |
| `packaging` | builds a wheel, asserts the runtime YAML and `py.typed` are *inside* it, type-checks it from a **consumer's** side in both dependency shapes, then installs **that wheel** and runs it |
| `invariants` | the promises, driven through the CLI: source never modified, idempotent, overwrite refused, non-Latin text survives PDF generation |

Two things that are easy to get wrong here:

- **An editable install cannot prove packaging.** `pip install -e` resolves data
  files from the source tree, so a missing `package-data` entry is invisible in
  development. This already shipped once, with `docfix/fonts/catalog.yaml`.
- **A bare runner has almost no fonts.** The PDF jobs install
  `fonts-dejavu-core` and `fonts-liberation`, or PDF output would only ever
  exercise the degraded path. Tests must assert the *contract* — a character
  renders exactly **or** is reported unrenderable — never that a given machine
  has a given glyph.

## Quality gates (skills + hook)

Two gates fire automatically after a successful push, and either or both can
fire on the same push. A third skill captures bug reports on demand.

| Skill | Fires when | Does |
| --- | --- | --- |
| `push-checkpoint` | every 10th successful push | scoped `code-review` since the last checkpoint, then `pytest` + `ruff` |
| `verify` | a push moves **more than 100** changed lines | clean-room install, full suite, lint, packaging check, real CLI run, invariant checks |
| `report-bug` | the user reports something broken, or `verify` step 9 finds something | records a structured entry in `docs/BUG-REPORTS.md`, optionally files an issue |

`.claude/hooks/push-counter.py` is the trigger for both automatic gates. It is a
`PostToolUse` hook, because **a skill cannot fire on an event** — skills are
model-invoked. If a gate never triggers, debug the hook, not the skill.

How it measures a push: it reads the remote-tracking ref's reflog
(`<upstream>@{1}..<upstream>`) to find what the push actually moved, then counts
insertions plus deletions. On a branch's first push there is no previous value,
so it falls back to the merge base with the default branch. Any git command that
fails makes it skip silently — a quality gate is never worth breaking a push over.

Pushes that deliberately do **not** count: rejected, interrupted, `--dry-run`,
and "everything up-to-date".

Tuning: `CHECKPOINT_EVERY` and `LARGE_PUSH_LINES` are constants at the top of the
hook. The threshold is strictly *greater than* — exactly 100 lines does not fire.

`.claude/state/` holds `push-count`, `last-checkpoint`, and `last-verified`. It
is gitignored, and **must stay that way**: if the counter file entered the repo,
every push's measured size would be inflated by the counter's own diff.

The hook has its own tests in `tests/test_hooks.py`, including real git repos
with real remotes, since the reflog logic cannot be tested any other way.

## Git workflow

- **Default branch:** `main`. Feature work goes on `claude/<topic>-<suffix>`
  branches; never commit directly to `main`.
- **Pushing:** `git push -u origin <branch>`; retry network failures with
  exponential backoff (2s, 4s, 8s, 16s).
- **Pull requests:** do not open one unless the user explicitly asks.
- **Repo name gotcha:** the name ends with a **trailing hyphen** —
  `API-lib-for-fixing-common-format-issues-`. It is part of the real name and is
  easy to drop when hand-writing clone URLs or scripts. Copy it, don't retype it.

## Keeping this file honest

When you add anything structural — an adapter, a rule family, a test convention,
a CI workflow — update the matching section here in the same commit, and delete
any statement this change has made false. A CLAUDE.md describing a repository
that no longer exists is worse than none at all.
