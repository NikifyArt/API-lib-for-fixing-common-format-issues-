# CLAUDE.md

Guidance for Claude Code and other AI assistants working in this repository.

## ⚠️ Read this first: the repository is a scaffold

As of the latest update to this file, **this repo contains no source code**. The
complete tracked contents are:

```
LICENSE      BSD 3-Clause, Copyright (c) 2026, Nik
CLAUDE.md    this file
```

There is no build system, no package manifest, no tests, no CI, no `.gitignore`,
and no chosen implementation language. Do not assume otherwise, and **do not
describe planned structure below as if it already exists**. If you are reading
this and the tree now has code in it, this file is stale — update it as part of
your change (see [Keeping this file honest](#keeping-this-file-honest)).

## What this project is meant to be

From the repository description, which is currently the only statement of intent:

> A library of pre-built document templates — font families, formatting style
> presets, and optional color schemes (formal, friendly, …) — applied
> automatically to documents. "Prettier, but for `.md`, `.docx`, CVs, and
> `.pdf`" rather than for source code.

Reading that into concrete terms, the intended product is:

- **A template library.** Named, reusable presets bundling a font stack, a set of
  formatting rules (headings, spacing, margins, lists, tables), and an optional
  color scheme.
- **Presets with a tone.** Schemes are named by register — `formal`, `friendly`,
  and similar — not by raw color values.
- **A formatter/fixer.** Something that takes an existing document and normalizes
  it against a chosen template, the way Prettier normalizes source files.
- **An API-first library**, per the repo name — a programmatic surface, with any
  CLI or editor extension layered on top rather than being the product itself.

Target formats named so far: `.md`, `.docx`, `.pdf`, and CVs/résumés.

## Open decisions — ask, do not pick unilaterally

None of the following have been decided. Each one shapes everything after it, so
if a task requires an answer, **ask the user rather than choosing silently**:

1. **Implementation language and runtime.** Unset. The plausible candidates pull
   in very different ecosystems: TypeScript/Node (best `.docx`/`.md` tooling and
   VS Code extension story), Python (best `.pdf` and document-conversion
   tooling), or Rust (single-binary CLI). Nothing in the repo favors one yet.
2. **Package/distribution target.** npm, PyPI, a CLI binary, a hosted API, or a
   VS Code extension — the description gestures at several.
3. **Whether "CV" is a format or a template category.** The description lists
   `.cv` alongside real extensions, but there is no such file format. It most
   likely means *CV/résumé documents*, which are ordinary `.docx`/`.pdf` files
   with a specialized template. Confirm before designing a "CV format" handler.
4. **Read-write scope per format.** These formats are not equally tractable, and
   the difference is large enough to drive scope:
   - `.md` — plain text; full parse-and-rewrite is straightforward.
   - `.docx` — a zipped OOXML bundle; styles are addressable, so real
     restyling is achievable.
   - `.pdf` — a fixed-layout output format. Re-styling an existing PDF in place
     is a fundamentally harder problem than the other two, and is usually solved
     by *generating* a PDF from a source document instead. Do not quietly promise
     in-place PDF restyling; raise the tradeoff.
5. **Whether the tool ever rewrites files in place**, and if so, what the
   safety story is (backups, dry-run, diff preview). Document formatters that
   overwrite user documents are destructive by default — this needs an explicit
   decision, not an accident.

## Git workflow

- **Default branch:** `main`.
- **Working branches:** AI-assisted work goes on `claude/<topic>-<suffix>`
  branches (e.g. the current `claude/claude-md-docs-9iaaj6`). Never commit
  directly to `main`, and never push to a branch other than the one assigned for
  the task.
- **Pushing:** `git push -u origin <branch-name>`. On network failure, retry up
  to four times with exponential backoff (2s, 4s, 8s, 16s).
- **Pull requests:** do not open one unless the user explicitly asks.
- **Commit messages:** history is one commit (`Initial commit`), so no convention
  is established. Use clear, imperative subject lines. If the project later
  adopts Conventional Commits or similar, record it here.
- **Repo name gotcha:** the name ends with a **trailing hyphen** —
  `API-lib-for-fixing-common-format-issues-`. It is part of the real name, and
  it is easy to drop when hand-writing clone URLs, API paths, or scripts. Copy
  it, don't retype it.

## Conventions for adding the first code

When the toolchain question above is answered and real code lands, these are the
expectations to meet — and to replace with specifics once they are real:

- **Add a `.gitignore` in the same change as the first dependency manifest.**
  There is none today, so build output and dependency directories would
  otherwise be committed.
- **Add tests alongside the first module, not later.** A formatter's whole value
  is deterministic, repeatable output; golden-file tests (input document →
  expected formatted output) fit this domain especially well.
- **Do not commit binary fixture documents casually.** `.docx` and `.pdf`
  fixtures are opaque to diffs and inflate the repository permanently. Prefer
  generating fixtures from text sources at test time; if a real binary fixture
  is genuinely needed, keep it small and explain in a comment why it exists.
- **Keep templates as data, not code.** Font stacks, spacing rules, and color
  schemes should live in declarative files (JSON/YAML/TOML) that the library
  loads, so adding a preset never requires touching program logic. This follows
  directly from the project being "a library of pre-built templates".
- **Separate the three layers** — template definitions, per-format adapters
  (`.md`, `.docx`, `.pdf`), and the public API — so a new output format is a new
  adapter rather than a rewrite.
- **Record the real commands here** (install, build, test, lint) as soon as they
  exist. That is the single most useful thing this file can offer an assistant,
  and it currently cannot offer it.

## Working style in this repo

- The repo's near-empty state means most tasks are greenfield. Confirm scope
  before generating a large amount of structure; a wrong guess about language or
  packaging is expensive to unwind.
- Prefer small, reviewable commits over one large scaffold drop.
- Documents are user data. Anything that writes to a path the user supplied
  deserves confirmation, a dry-run mode, or both.

## Keeping this file honest

This file describes a repository that is expected to change substantially. When
you add anything structural — a language, a manifest, a test runner, a CI
workflow, a directory layout — update the corresponding section in the same
commit, and delete any statement here that your change has made false. In
particular, the "repository is a scaffold" section at the top must be rewritten
the moment real source lands. A CLAUDE.md that describes a repository that no
longer exists is worse than no CLAUDE.md at all.
