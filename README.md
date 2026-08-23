# docfix

Find and fix common formatting problems in documents. Prettier, but for `.md`,
`.docx`, `.pdf`, and CVs.

`docfix` reads a document, reports the formatting problems it finds, repairs the
safe ones, applies a named template, and writes the result to a **new file**.
The source is never modified.

> **Status: alpha.** Markdown, PDF and DOCX are implemented end to end, with
> a CV/résumé layer on top — see [Roadmap](#roadmap).

## Install

```bash
pip install -e ".[dev]"          # Markdown only
pip install -e ".[dev,pdf]"      # adds PDF support
pip install -e ".[dev,docx]"     # adds Word support
pip install -e ".[dev,all]"      # everything
```

## Use it

```bash
docfix check notes.md                      # report problems, write nothing
docfix format notes.md --template formal   # write notes.formatted.md
docfix scan report.pdf                     # what would a PDF conversion cost?
docfix templates                           # list the bundled presets
docfix check docs/                         # a whole directory
docfix format docs/ --diff                 # what would change, without writing
docfix check resume.md --cv                # also apply the résumé conventions
docfix rules                               # every rule, and what your config does
docfix fonts                               # which fonts are available, and their licences
docfix fonts install                       # pinned fonts, for identical output anywhere
```

```python
import docfix

result = docfix.format_file("notes.md", template="formal")
result.output_path      # 'notes.formatted.md'
result.fixed            # issues repaired automatically
result.remaining        # issues that need a human decision

docfix.detect("notes.md")   # report only, writes nothing
```

`check` exits `1` when it finds issues and `0` when the file is clean, so it
drops straight into CI or a pre-commit hook.

## What it fixes

Repairs applied automatically:

| Rule | Problem |
| --- | --- |
| `list-mixed-markers` | A list that switches between `-`, `*`, and `+` |
| `whitespace-trailing` | Trailing spaces at end of line |
| `whitespace-blank-run` | More than one blank line in a row |
| `whitespace-tab-indent` | Tab-indented lines |
| `paragraph-empty` | Paragraphs with no content |

Reported but never rewritten, because fixing them would change what the document
*says* rather than how it looks:

| Rule | Problem |
| --- | --- |
| `heading-skip` | Heading levels jump (h2 → h4) |
| `heading-multiple-h1` | More than one level-1 heading |
| `heading-empty` | A heading with no text |
| `image-missing-alt` | An image with no alt text |
| `date-inconsistent` | Mixed date formats — matters most on a CV |
| `quotes-mixed` | Straight and curly quotes in one document |
| `table-ragged-row` | A row with the wrong number of cells |

## Configuration

Drop a `docfix.toml` at the root of a project — or a `[tool.docfix]` table in
`pyproject.toml` — and `docfix check .` needs no flags:

```toml
template = "technical"
exclude = ["vendor/**", "**/CHANGELOG.md"]

[rules]
"quotes-mixed" = false        # off entirely
"heading-skip" = "error"      # louder

[options]
"cv-bullet-too-long" = { max_length = 180 }
```

The nearest config wins, searching upward from the working directory.
`--config PATH` overrides the search; `--no-config` ignores it. An explicit
flag always beats the file — `-t formal` wins over `template = "technical"`.

`docfix rules` prints every rule id with a one-line summary, marks the ones
your config has disabled, and shows any severity overrides and options. Those
ids are what the `[rules]` and `[options]` tables address.

### Working on many files

`format` and `check` take any mix of files and directories. Directories are
walked, keeping only extensions an adapter handles and skipping anything
`exclude` matches (and anything `docfix` itself produced).

```bash
docfix check .                          # exit 1 if anything is reported
docfix format docs/ --out-dir built/    # results collected, sources untouched
docfix format docs/ --diff              # preview; writes nothing
```

`-o` and `--keep-intermediate` are single-file only — use `--out-dir` for a
batch.

### Exit codes

Stable, so CI and pre-commit hooks can rely on them:

| Code | Meaning |
| --- | --- |
| `0` | Clean — nothing reported, nothing would change |
| `1` | Issues found (`check`), or changes needed (`format --diff`) |
| `2` | Error — file missing, bad config, unknown template, unsupported format |

## Templates

A template is a YAML file — fonts, spacing, colors, and Markdown marker
preferences. Adding one never requires touching code.

Bundled presets: `formal`, `friendly`, `technical`, `minimal`.

```yaml
name: formal
markdown:
  bullet_marker: "-"
  emphasis_marker: "*"
  wrap_width: 0          # 0 = never reflow prose
fonts:
  body: {family: "Georgia, serif", size: 11}
spacing: {line_height: 1.5, paragraph_after: 12}
colors: {text: "#1a1a1a", accent: "#0b3d5c"}
```

Pass a path to use your own: `docfix format notes.md -t ./house-style.yaml`.

## CVs and résumés

A CV is a template category, not a file format — it arrives as Markdown, DOCX
or PDF like anything else. On top of the usual formatting checks, `docfix`
applies the conventions a résumé is judged by:

```bash
docfix check resume.md                   # CV rules apply automatically
docfix format resume.md -o cv.pdf -t cv-classic
```

Detection is conservative: two or more recognised sections, one of them
Experience or Education. A README with a "Skills" heading is not a CV. Force
the rules with `--cv`, or turn them off with `--no-cv`.

| Rule | Problem |
| --- | --- |
| `cv-missing-contact` | No email, phone or link near the top |
| `cv-not-reverse-chronological` | Entries not listed most-recent first |
| `cv-missing-section` | No Experience or Education section |
| `cv-summary-placement` | Summary sits after the experience section |
| `cv-bullet-punctuation` | Some bullets end with a full stop, some don't |
| `cv-first-person` | Bullets using "I", "my" |
| `cv-weak-opener` | Bullets opening "Responsible for", "Worked on" |
| `cv-bullet-too-long` | A bullet that has stopped being scannable |

**These only ever report.** Reverse-chronological order and phrasing are
matters of judgement, so `docfix` will not rewrite them — the same rule that
governs headings and quotes elsewhere.

Three presets, tighter than the general ones because a CV has to fit the page:
`cv-classic` (serif, conservative), `cv-modern` (sans with a colour accent),
`cv-compact` (smallest, for a long career).

## Working with Word documents

DOCX is structured — styles say what a paragraph *is* — so extraction is
faithful rather than inferred, and a round trip preserves headings, both list
kinds, tables, inline formatting and hyperlinks.

```bash
docfix format report.docx -t formal   # writes report.formatted.docx
docfix format report.docx -o out.md   # or convert to Markdown
docfix format notes.md -o out.docx    # or the other way
```

Unlike PDF there is no glyph-coverage problem: DOCX stores text as XML, so any
character survives, and a font name is a *request* the reader substitutes if it
lacks it. Nothing is embedded, so the font licence rules do not apply here.

## Working with PDFs

PDF is fixed-layout. `docfix` **cannot restyle a PDF in place** — it extracts the
content, formats it, and generates a new PDF. That loses things, so it tells you
what before it does anything.

```bash
$ docfix scan report.pdf
report.pdf: 12 pages, 3 with conversion risks

  tables                   2/12 pages
  charts / drawings        1/12 pages

  page    4  high: 2 table(s); structure is approximated from ruling lines
  page    7  medium: 68 vector shapes (a chart, diagram, or table rules) will be lost
  page   11  high: looks like 2 columns; extracted reading order may be wrong
```

`docfix format` runs that scan first and asks before converting anything it
would damage. Unattended (no terminal), it refuses rather than guessing — pass
`--yes` to accept the losses.

What the scan looks for, page by page: pages with no text layer (scanned
images), multi-column layouts, tables, embedded images, charts and diagrams,
rotated text, annotations and form fields, and undecodable characters from fonts
with a broken character map.

### Checking the extraction

`--keep-intermediate` writes what was extracted as Markdown, so you can read it
— and correct it — before it becomes a PDF:

```bash
docfix format report.pdf --keep-intermediate   # also writes report.extracted.md
docfix format report.extracted.md -t formal    # fix it up, then convert that
```

This is the reliable path for an important document: extract, eyeball the
Markdown, fix anything the extractor got wrong, then format from there.

What survives extraction: headings (recovered from font size), paragraphs
(including lines rejoined and hyphenation repaired), bulleted and numbered
lists, and tables. Running headers, footers, and page numbers are stripped.
What does not: figures, exact layout, and multi-column reading order.

## Fonts and character coverage

Generated PDFs use real fonts from your system, so text outside Latin-1 renders
correctly. This matters more than it sounds: with only the PDF base-14 fonts,
`Zażółć gęślą jaźń` comes out as `Zanónn gnnln jann`, `→ ★ ≈ ✓` as `fi H » 3`,
and Cyrillic or CJK as empty boxes — with no error and no warning.

> **UTF-8 is not the limitation.** UTF-8 already encodes all of Unicode, and
> `docfix` reads and writes it throughout. The limit is which glyphs the *font*
> contains — which is what the font pool addresses.

Covered today: Latin and Latin Extended, Cyrillic, Greek, Hebrew, Arabic,
symbols and arrows, and CJK (Japanese, Chinese simplified and traditional, and
Korean). Colour emoji is not supported — emoji fonts store bitmaps rather than
outlines, which the PDF generator cannot render.

Nothing needs configuring. Each template names a stack of families and a
fallback chain, and every character is set in the first font that can actually
render it. Anything nothing can render is **reported**, never silently dropped:

```
$ docfix check notes.md
  [error] font-coverage (document): 1 character(s) cannot be rendered by any
          available font and will be missing from the PDF: 🎉
```

CJK works with no font file at all, using the CID collections built into the
PDF generator. The reader supplies those glyphs, so such PDFs are not fully
self-contained — pass `--embed-cjk` when they need to be:

```bash
docfix format notes.md -o out.pdf --embed-cjk
```

That embeds an installed, open-licensed CJK font instead, so the file renders
identically anywhere. It costs roughly 10 KB and needs a suitable font on the
machine; without one it falls back to the CID collections and says so, so asking
for it can never make CJK worse.

### Reproducible output

By default `docfix` uses whatever fonts a machine has, so the same CV can
render differently on a laptop and in CI. `docfix fonts install` fixes that:

```bash
docfix fonts install                       # ~6 MB, once
docfix format cv.md -o cv.pdf -t reproducible
```

It downloads a small pinned set — Lato, IBM Plex Serif, IBM Plex Mono and Noto
Sans, all SIL OFL — into a cache the font pool searches *before* the system,
so those families always win. Every URL pins a **commit SHA**, and every file
is checked against a SHA-256 recorded in `docfix/fonts/pinned.yaml`; a mismatch
is a hard failure and the file is discarded. The licence text is stored beside
each font.

The repository still ships no font binaries — only the manifest.

The `reproducible` preset names only pinned families. Any template that names
something else still works, and `docfix` tells you when output depended on a
machine-specific font:

```
[info] font-not-pinned (document): output used font(s) installed on this
       machine rather than the pinned set, so it may render differently
       elsewhere: Liberation Serif; run `docfix fonts install`
```

`docfix fonts --reproducible` reports whether the pinned set is present and
exits 1 if not, so it works as a CI gate. `docfix fonts install --check`
verifies an existing cache without downloading.

### Licensing

`docfix` **ships no font files**, so it redistributes nothing. It uses fonts
already installed on your machine, under two independent rules:

- **A font is never embedded if its own `fsType` forbids it.** That flag lives
  in the font file and is where a vendor states their embedding terms.
- **A font is only chosen automatically if it declares a recognised open
  licence** — OFL, Apache 2.0, the Ubuntu Font Licence, the Bitstream Vera
  terms, public domain, or GPL *with* the font embedding exception. Plain GPL
  without that exception is excluded, since it is unclear whether embedding
  would place your document under the GPL.

Licences are read from what each font declares about itself, not guessed from
its name, so any correctly-licensed font you install is recognised. A font you
name explicitly is still used even if its licence is unrecognised — you may own
it — but it is never picked for you.

```
$ docfix fonts
family                     cat    styles licence              auto
DejaVu Sans                sans   2      Bitstream-Vera       yes
Liberation Serif           serif  4      OFL-1.1              yes
FreeSerif                  serif  4      GPL-font-exception   yes
IPAGothic                  sans   1      unknown              no
```

`docfix fonts --family "Liberation Serif"` shows full detail, including the
embedding permission and the file backing each style.

## How it works

Every format is read into one intermediate representation. Detection, fixing,
and template application run on that IR alone, so a rule is written once and
works for every format, and adding a format means adding one adapter.

```
input → Reader → Document IR → Detect → Fix → ApplyTemplate → Writer → new file
```

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

## Development

```bash
python -m pytest       # 371 tests, or 435 with all extras
python -m ruff check . # lint
```

### Reporting a bug

Found something wrong? Open an issue using the
[bug report template](.github/ISSUE_TEMPLATE/bug_report.md), or run
`/report-bug` in Claude Code to capture it into
[`docs/BUG-REPORTS.md`](docs/BUG-REPORTS.md).

Every field is optional. A one-line report beats no report.

### Automated quality gates

Working in this repo with Claude Code triggers two checks automatically after a
push:

| Gate | Fires when | Does |
| --- | --- | --- |
| `push-checkpoint` | every 10th push | scoped code review, then tests and lint |
| `verify` | a push over 100 changed lines | clean-room install, full suite, lint, packaging, real CLI run, invariant checks |

Both are driven by `.claude/hooks/push-counter.py`. Thresholds are constants at
the top of that file.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
