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
  templates/
    loader.py            YAML → Template, with validation
    presets/*.yaml       formal, friendly, technical, minimal
  detect/rules.py        STRUCTURE_RULES (on the IR) + SOURCE_RULES (on raw text)
  fix/normalize.py       safe repairs only
  adapters/
    __init__.py          Adapter registry; for_path() dispatches on extension
    markdown.py          read + write (markdown-it-py in, canonical Markdown out)
    pdf.py               scan() risk report, read (pdfplumber), write (reportlab)
  cli.py                 format / check / templates
tests/                   155 tests, 191 with the PDF extras installed
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
- **Fonts map by category, not face.** serif/sans/mono onto the PDF base-14.
  Embedding real faces would mean shipping font files.
- **`_require` catches `BaseException`, not `ImportError`.** A broken native
  dependency surfaces as a pyo3 panic, and a raw Rust traceback tells the user
  nothing about what to install. `tests/test_pdf.py` skips on the same basis —
  `pytest.importorskip` does not catch a panic and collection would fail.

## Commands

```bash
pip install -e ".[dev]"       # pytest + ruff
pip install -e ".[dev,pdf]"   # adds pdfplumber + reportlab
python -m pytest          # 155 tests (191 with PDF extras), ~3s
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
| 3 | DOCX adapter (read + write) | planned |
| 4 | CV/résumé template layer | planned |

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
