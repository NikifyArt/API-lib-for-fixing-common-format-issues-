## What this changes

<!-- One or two sentences. What was wrong or missing, and what does this do about it? -->

## Why

<!-- The reasoning, if it is not obvious from the above. Link an issue if there is one. -->

## How it was verified

<!-- Not "tests pass" -- what did you actually check?
     For a bug fix: the regression test that fails without this change.
     For an adapter: idempotence and round-trip stability.
     For anything touching PDF output: what you read back out of the file. -->

- [ ] `python -m pytest` passes
- [ ] `python -m ruff check .` is clean
- [ ] Tests added that fail without this change

## Invariants

These are the promises docfix makes. Tick them, or say which one this changes
and why that was the right call.

- [ ] The source file is never modified
- [ ] Formatting is still idempotent
- [ ] No semantic problem became auto-fixable (cosmetic ones are fixed, semantic
      ones are only reported)

## Documentation

- [ ] `CHANGELOG.md` updated under Unreleased, if this is user-facing
- [ ] `CLAUDE.md` updated if this is structural — a new adapter, rule family,
      test convention or CI job — and any statement it made false is deleted
- [ ] `docs/EXTENDING.md` updated if this changes the extension surface
