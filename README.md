# docfix

Find and fix common formatting problems in documents. Prettier, but for `.md`,
`.docx`, `.pdf`, and CVs.

`docfix` reads a document, reports the formatting problems it finds, repairs the
safe ones, applies a named template, and writes the result to a **new file**.
The source is never modified.

> **Status: alpha.** Markdown is implemented end to end. DOCX, PDF, and the CV
> template layer are planned — see [Roadmap](#roadmap).

## Install

```bash
pip install -e ".[dev]"
```

## Use it

```bash
docfix check notes.md                      # report problems, write nothing
docfix format notes.md --template formal   # write notes.formatted.md
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
| 2 | DOCX adapter (read + write) | planned |
| 3 | PDF writer, then PDF text extraction | planned |
| 4 | CV/résumé template layer | planned |

**A note on PDF.** PDF is a fixed-layout format and is not symmetrical with the
others. Generating a PDF from the IR is clean; reading one back recovers text
and rough structure but is lossy for complex layouts. `docfix` will not claim a
faithful in-place PDF restyle.

## Development

```bash
python -m pytest      # test suite
python -m ruff check . # lint
```

## License

BSD 3-Clause. See [LICENSE](LICENSE).
