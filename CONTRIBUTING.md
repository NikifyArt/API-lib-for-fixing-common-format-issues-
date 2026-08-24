# Contributing to docfix

Bug reports, adapters, rules and templates are all welcome. So is a fork — the
licence is BSD-3-Clause and you owe nothing but the copyright notice.

**Before you write code to extend docfix, read
[docs/EXTENDING.md](docs/EXTENDING.md).** Adding a format, a rule or a template
does not require changing docfix at all, and doing it through the registration
API keeps your work mergeable and your fork rebaseable.

## Getting set up

```bash
pip install -e ".[dev]"       # pytest + ruff
pip install -e ".[dev,all]"   # adds pdfplumber, reportlab, python-docx
python -m pytest              # ~9s
python -m ruff check .        # must be clean
```

The extras are optional on purpose: docfix must work as a Markdown-only install,
and CI tests it that way. If your change makes the base install require
`reportlab`, that is a bug.

## The three invariants

These are the promises docfix makes. A change that breaks one needs an explicit
decision, not a pull request that quietly relaxes it.

1. **The source file is never modified.** Output always goes to a new path.
   `format_file` refuses to write over its input.
2. **Templates are data, not code.** Adding a preset means adding a YAML file
   and nothing else. If a feature needs logic changes to add a preset, the
   design is wrong.
3. **Cosmetic problems are fixed; semantic ones are only reported.** Whitespace
   and marker inconsistency get repaired. Heading levels, mixed quotes and
   missing alt text are reported and left alone — fixing them would change what
   the document says.

## Architecture in one line

```
input → Reader(fmt) → Document IR → Detect → Fix → ApplyTemplate → Writer(fmt) → new file
```

Detection, fixing and template application run on the IR **only**. Adapters hold
conversion logic and no formatting rules. A rule written once works for every
format; a new format is one adapter rather than a rewrite. Do not put formatting
rules in an adapter.

## What a good change looks like

- **Tests for the behaviour, not the implementation.** For an adapter, the two
  that matter are idempotence (`format(format(x)) == format(x)`) and round-trip
  stability (`read(write(doc))` gives back the same block structure). Both are
  enforced across every template × sample combination.
- **A regression test that fails without your fix.** If you cannot make it fail,
  you have not found the bug yet.
- **Every rule declares its ids** with `@emits(...)`, and has a one-line
  docstring — that docstring is what `docfix rules` shows.
- **Assert the contract, not the machine.** A test that depends on which fonts a
  runner happens to have will pass locally and fail in CI. The correct assertion
  is "this character renders exactly **or** is reported as unrenderable", never
  "this machine has this glyph".
- **No binary fixtures without a reason.** Generate `.docx`/`.pdf` from text at
  test time. They are opaque to diffs and inflate the repo permanently. If you
  genuinely need one, keep it small and comment why it exists.

## Documentation is part of the change

`CLAUDE.md` records the traps that cost real debugging time, and it has a rule:
when you add anything structural — an adapter, a rule family, a test convention,
a CI job — update the matching section **in the same commit**, and delete any
statement your change made false. A file describing a repository that no longer
exists is worse than none.

User-facing changes also go in `CHANGELOG.md` under `Unreleased`.

## Pull requests

- Branch from `main`. Never commit directly to it.
- Run `python -m pytest` and `python -m ruff check .` before pushing.
- CI runs 3 Python versions × 3 dependency shapes, plus lint, a packaging job
  that builds a wheel and installs *that* rather than the source tree, and the
  invariants driven through the CLI. All of it must be green.
- One logical change per PR. A fix and a refactor in one diff is two reviews
  pretending to be one.

## Reporting a bug

Open an issue, or add an entry to [docs/BUG-REPORTS.md](docs/BUG-REPORTS.md) if
you are working in the repo. A good report has what happened, what you expected,
and a reproduction — the smallest input that shows it. For a formatting problem,
the input document matters more than the description of it.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

## Licence

By contributing you agree your work is licensed under the BSD-3-Clause licence
in [LICENSE](LICENSE).
