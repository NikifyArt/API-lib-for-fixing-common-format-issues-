# Bug Reports

Bugs and glitches reported against `docfix`, newest first.

This log exists so a report survives the conversation it was made in. It is not
a replacement for GitHub issues — anything that needs tracking or discussion
should also be filed there, and linked from its entry here.

Reports are added by the `report-bug` skill (`/report-bug`), or by hand.

## Format

Each entry uses this shape. Every field except **id**, **date**, **status**, and
**what happened** is optional — a short report is better than no report, and a
blank field is better than a guessed one.

```markdown
### BUG-001 — one-line summary

- **Date:** YYYY-MM-DD
- **Status:** open | fixed | cannot-reproduce | wont-fix
- **Severity:** blocks work | annoying | cosmetic
- **Reproducible:** every time | sometimes | saw it once
- **Issue:** #12 (if filed)
- **Fixed in:** abc1234 (if fixed)

**What happened**

The reporter's own description, kept in their words.

**What was expected**

...

**Reproduction**

​```bash
$ docfix format notes.md
...
​```

**Notes**

Whether it reproduced, what was tried, anything relevant found while looking.
```

## Reports

### BUG-007 — fsType was read past the end of a short OS/2 table

- **Date:** 2026-08-24
- **Status:** fixed
- **Severity:** wrong output (licence gate)
- **Found by:** the phase 8 malformed-font tests

**What happened**

`_read_fs_type` seeked to the OS/2 table's offset and read 12 bytes without
bounding the read by the table's *declared* length. A font whose OS/2 table is
shorter than 10 bytes therefore had its `fsType` read from whatever table
happened to follow it in the file.

**What was expected**

`None` — "the font does not state a restriction" — for a table too short to
contain the field.

**Reproduction**

Build a font with a 4-byte OS/2 table followed by a `name` table:

```python
data = build_font({b"OS/2": b"\x00" * 4, b"name": name_table({1: "Testface"})})
sfnt.read(path).fs_type
# -> 18, read from the name table's bytes
```

**Notes**

`fsType` is the gate that decides whether a font may be embedded **at all** —
Restricted License Embedding is refused for everyone, including a font the user
names explicitly, and an open licence does not override it. So a value invented
from adjacent bytes is the licence gate reading garbage.

In this reproduction the garbage happened to be 18, which sets the restricted
bit and therefore failed *safe*. That is luck, not design: the same adjacent
bytes could as easily have been 0, which reads as "installable, embedding
unrestricted" and would have granted a permission the vendor never gave.

Not reachable from the pinned cache, whose fonts are checksummed, and not from
any well-formed font. It is reachable from a system font directory, which is
attacker-influenced if an attacker can place a file there.

Fixed by returning `None` when the declared table length cannot hold the field,
and reading exactly 10 bytes otherwise.

### BUG-006 — list markers render in base-14 Helvetica, not the document font

- **Date:** 2026-08-23
- **Status:** fixed
- **Severity:** wrong output
- **Found by:** the `verify` gate on the phase 7 push

**What happened**

Every bullet and ordered-list number in a generated PDF was drawn in Helvetica
at 10pt, while the text of the same list item used the document's font at the
template's size. reportlab's `ParagraphStyle` defaults `bulletFontName` to
`Helvetica` and `bulletFontSize` to `10`, independently of `fontName` and
`fontSize`, and `_styles()` never set either.

**What was expected**

A marker belongs to its item. It should use the same font, at the same size,
as the text beside it.

**Reproduction**

```bash
$ printf '# T\n\n1. first\n2. second\n\n- bullet\n' > ord.md
$ docfix format ord.md -t technical -o ord.pdf --yes
```

Extracting the characters back, grouped by font:

```
AAAAAA+NotoSans-Regular: 'Tfirstsecondbullet'
Helvetica:               '1.2.-'
```

**Notes**

Two consequences beyond the visible mismatch:

- It defeated phase 7 for lists. Helvetica is a base-14 font and is never
  embedded, so marker glyphs came from the reader's own substitution — the
  exact machine-dependence the pinned cache exists to remove. The
  `reproducible` preset produced a PDF that was not fully reproducible.
- `font-not-pinned` did not fire for it. The rule reports the families a span
  resolved to, and the bullet never goes through span resolution, so a user
  who asked for reproducible output was told everything was fine.

Not a silent-corruption bug: `templates/loader.py` restricts `bullet_marker`
to `*`, `+`, `-`, all ASCII, so no marker could land outside Helvetica's
encoding. That validation is what kept this cosmetic rather than destructive.

Fixed by naming `bulletFontName`/`bulletFontSize` on the `item` style. Two
regression tests: one on the style contract, one measuring the rendered page.

### BUG-005 — CV rules re-walk the document once per rule

- **Date:** 2026-08-23
- **Status:** open
- **Severity:** annoying
- **Found by:** the `push-checkpoint` review

**What happened**

`cv.check` derives the section model and the bullet list separately in each
rule: `sections(doc)` is walked four times and `_bullets(doc)` four more, so a
CV costs roughly 8× the necessary tree traversal. Noticeable on
`docfix check .` over a directory of CVs.

**Notes**

Not a correctness problem, so it was left rather than reshaping the rule
signatures during a checkpoint. The fix is to build sections and bullets once
in `check()` and hand them to the rules.

### BUG-004 — an empty heading vanishes from a DOCX

- **Date:** 2026-08-23
- **Status:** fixed
- **Severity:** annoying
- **Found by:** the `push-checkpoint` review

**What happened**

`read_path` drops every text-empty paragraph before looking at its style, so an
empty Word heading disappears entirely and the ERROR-severity `heading-empty`
rule can never fire for DOCX. The byte-identical Markdown reports it. Anything
following the empty heading is silently absorbed into the previous section.

**Notes**

The comment at the discard site promises a style check that was never written.
Fix: examine the style before discarding an empty paragraph.

**Resolution**

Fixed in phase 9. An empty paragraph is still skipped as spacing, but a
bottom border now marks a thematic break, and code paragraphs are collected
before the empty-paragraph skip so a blank line inside a code block survives.

### BUG-003 — nested lists are flattened by the DOCX writer

- **Date:** 2026-08-23
- **Status:** fixed
- **Severity:** annoying
- **Reproducible:** every time
- **Found by:** the `push-checkpoint` review

**What happened**

`- a` with a nested `- a1 / - a2` writes the nested items as top-level
paragraphs, so reading back gives one flat four-item list. All hierarchy is
lost.

**Notes**

Word needs an indent level (`w:ind`, or a numbering level) on the nested
paragraphs. Larger than a checkpoint fix, so it is recorded rather than rushed.

**Resolution**

Fixed in phase 9. Depth rides in the style name -- Word's `List Bullet 2`
and `List Bullet 3` -- and `_nest_items` rebuilds the tree on read. Levels
beyond 3 draw at 3 rather than falling back to 1, which read back as flat.

### BUG-002 — code blocks and thematic breaks do not survive a DOCX round trip

- **Date:** 2026-08-23
- **Status:** fixed
- **Severity:** annoying
- **Reproducible:** every time
- **Found by:** the `push-checkpoint` review

**What happened**

The writer emits a `CodeBlock` as ordinary `Normal` paragraphs with a mono
font, but the reader identifies code by style (`HTML Code` / `Code` /
`Macro Text`), so code comes back as loose paragraphs — the fence is gone and a
blank line appears between every code line. A `ThematicBreak` is written as
thirty literal `―` characters, which read back as an ordinary paragraph, so
`md -> docx -> md` turns every horizontal rule into permanent junk text.

**Notes**

Both need the writer to apply a style the reader already recognises. The
adapter docstring and CLAUDE.md have been corrected in the meantime so neither
claims a lossless round trip.

**Resolution**

Fixed in phase 9. Code blocks are written with a created `Code` paragraph
style (the language rides in the name, `Code python`), and a thematic break
is a bottom border rather than a row of dash characters -- which was literal
content a second pass would have kept.

### BUG-001 — cross-format output writes the wrong format, silently

- **Date:** 2026-08-22
- **Status:** fixed
- **Severity:** blocks work
- **Reproducible:** every time
- **Found by:** the `verify` skill, step 5 (exercising the tool for real)

**What happened**

`docfix format notes.md -o out.pdf` reported success and produced `out.pdf`
containing **Markdown text**, not a PDF. Opening it fails with
`PDFSyntaxError: No /Root object! - Is this really a PDF?`

**What was expected**

Either a real PDF, or a clear refusal. Not a file whose extension lies about
its contents, reported as a success.

**Reproduction**

```bash
$ printf '# Title\n\ntext\n' > notes.md
$ docfix format notes.md -o out.pdf
notes.md -> out.pdf  [template: formal]
  0 fixed automatically, 0 to review
$ head -1 out.pdf
# Title
```

**Notes**

Root cause: `format_file()` resolved a single adapter from the *input* path
(`adapters.for_path(path)`) and used it for both reading and writing, so the
output extension was ignored entirely.

Predates the font work; surfaced by verify because that step converts a real
document rather than asserting against a fixture. Unit tests missed it because
every one of them used matching input and output extensions.

The whole point of the shared IR is that any reader can feed any writer, and
the README already implies `md` → `pdf`, so the fix is to resolve the writer
from the output path rather than to refuse the conversion.

**Fixed in:** see the commit referencing BUG-001.
