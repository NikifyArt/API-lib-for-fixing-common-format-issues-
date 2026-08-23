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
  __init__.py            public API: format_file(), format_text(), detect(), list_templates()
  ir.py                  Document/Block/Run dataclasses; walk(), iter_runs(),
                         iter_block_sequences()
  config.py              docfix.toml / [tool.docfix]: rule control, options, exclude
  templates/
    loader.py            YAML → Template, with validation
    presets/*.yaml       formal, friendly, technical, minimal
  detect/rules.py        STRUCTURE_RULES (on the IR) + SOURCE_RULES (on raw text)
  cv.py                  CV section/entry model + CV_RULES (report-only)
  fix/normalize.py       safe repairs only
  fonts/
    sfnt.py              standalone TTF reader: fsType, cmap, names, outline kind
    catalog.yaml         licence signatures + known families (DATA)
    discover.py          find font files; group faces into families
    registry.py          pool assembly, licence gate, reportlab registration
    coverage.py          per-span font choice; unrenderable reporting
  adapters/
    __init__.py          Adapter registry; for_path() dispatches on extension
    markdown.py          read + write (markdown-it-py in, canonical Markdown out)
    pdf.py               scan() risk report, read (pdfplumber), write (reportlab)
    docx.py              read + write (python-docx); styles carry the structure
  cli.py                 format / check / templates
tests/                   304 tests, 396 with all extras installed
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

### Configuration

- **Every rule declares the ids it emits**, via `@emits(...)` in
  `detect/rules.py`. One function often emits several — `check_heading_levels`
  reports both `heading-skip` and `heading-multiple-h1` — and the *ids*, not
  the function names, are what a config file addresses. A test asserts no
  emitted id is undeclared.
- **Disable and severity are a post-filter on Issues**, in `Config.apply`, not
  a skip of rule functions. That is the right granularity given the above, and
  it works for all rules with no signature churn.
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
- **`fsType` is parsed by `sfnt.py`, not reportlab.** reportlab does not expose
  it, and the gate must work on files reportlab refuses to load.

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
python -m pytest          # 304 tests (396 with all extras), ~9s
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
| 7 | Reproducible font output | planned |

To add a format: write the adapter with `read_path`/`write_path` (plus
`source_text` if the format is text), register it in `adapters/__init__.py`, and
add its optional dependency to `pyproject.toml`. Detection, fixing, and templates
need no changes.

**PDF is not symmetrical with the others.** It is fixed-layout: generating from
the IR is clean, reading back recovers text and rough structure but is lossy.
Never promise a faithful in-place PDF restyle. The honest workflow, and the one
the CLI nudges toward, is `--keep-intermediate`: extract to Markdown, let the
user check and correct it, then format from there.

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
