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
  cli.py                 format / check / templates
tests/                   137 tests
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

## Commands

```bash
pip install -e ".[dev]"   # install with pytest + ruff
python -m pytest          # 137 tests, ~0.5s
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
| 2 | DOCX adapter (read + write) | planned |
| 3 | PDF writer, then PDF text extraction | planned |
| 4 | CV/résumé template layer | planned |

To add a format: write the adapter with `read_path`/`write_path` (plus
`source_text` if the format is text), register it in `adapters/__init__.py`, and
add its optional dependency to `pyproject.toml`. Detection, fixing, and templates
need no changes.

**PDF is not symmetrical with the others.** It is fixed-layout: generating from
the IR is clean, reading back recovers text and rough structure but is lossy.
Do not promise a faithful in-place PDF restyle.

## The push-checkpoint skill

Every 10 successful pushes, a quality checkpoint runs a scoped code review plus a
test/lint pass.

- `.claude/hooks/push-counter.py` — a `PostToolUse` hook that counts successful
  `git push` calls in `.claude/state/push-count` and asks for the skill on every
  10th. Rejected, interrupted, dry-run, and "everything up-to-date" pushes do not
  count.
- `.claude/skills/push-checkpoint/SKILL.md` — the workflow. Scopes the review to
  the range since `.claude/state/last-checkpoint`, then runs `pytest` and `ruff`.

A skill cannot fire on an event — skills are model-invoked — which is why the
cadence lives in the hook. If the checkpoint never triggers, debug the hook, not
the skill. `.claude/state/` is gitignored; the count is per-checkout.

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
