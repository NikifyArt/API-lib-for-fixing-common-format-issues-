# docfix

Find and fix common formatting problems in documents. Prettier, but for `.md`,
`.docx`, `.pdf`, and CVs.

`docfix` reads a document, reports the formatting problems it finds, repairs the
safe ones, applies a named template, and writes the result to a **new file**.
The source is never modified.

> **Status: alpha.** Markdown and PDF are implemented end to end. DOCX and the
> CV template layer are planned — see [Roadmap](#roadmap).

## Install

```bash
pip install -e ".[dev]"          # Markdown only
pip install -e ".[dev,pdf]"      # adds PDF support
```

## Use it

```bash
docfix check notes.md                      # report problems, write nothing
docfix format notes.md --template formal   # write notes.formatted.md
docfix scan report.pdf                     # what would a PDF conversion cost?
docfix templates                           # list the bundled presets
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
| 3 | DOCX adapter (read + write) | planned |
| 4 | CV/résumé template layer | planned |

Fonts in generated PDFs are honoured by *category* — serif, sans, or mono —
rather than by exact face, since embedding arbitrary fonts would mean shipping
font files. The PDF base-14 render everywhere.

## Development

```bash
python -m pytest       # 155 tests, or 191 with the PDF extras
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
