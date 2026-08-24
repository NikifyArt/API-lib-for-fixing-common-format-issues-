# Extending docfix

docfix is meant to be adapted. This is how, in order of how much you have to
change to get it.

Nothing here requires you to fork, and that is deliberate: the friction in a
long-lived fork is not the fork, it is the second year of it, when every
upstream pull conflicts in the file you edited. So every extension point below
is *additive* — you register something, you do not edit a list.

| You want | You write | Do you fork? |
| --- | --- | --- |
| A different look | a YAML template | no |
| An in-house rule | a function + `register_rule()` | no |
| A new file format | an adapter + `register_adapter()` | no |
| To change how a rule behaves | `docfix.toml` | no |
| To change docfix's own logic | a fork | yes |

---

## 1. A template — no code at all

Templates are data. A template is a YAML file, and `-t` accepts a path, so it
does not have to live inside the package:

```bash
docfix format report.md -t ./house-style.yaml
```

Start by copying one of `docfix/templates/presets/*.yaml`. The loader validates
what it reads and names the bad key when it rejects something.

```yaml
name: house-style
fonts:
  body:    {family: "Lato, sans-serif", size: 11}
  heading: {family: "Lato, sans-serif", size: 16}
  mono:    {family: "IBM Plex Mono, monospace", size: 9}
spacing:
  line_height: 1.4
  paragraph_after: 10
markdown:
  bullet_marker: "-"
colors:
  text: "#1a1a1a"
  heading: "#000000"
```

If you want reproducible PDFs, name only families from `docfix/fonts/pinned.yaml`
and run `docfix fonts install`. See [FONTS.md](#fonts-and-licensing) below.

## 2. A rule

A rule is a function taking `(doc, config)` — or `(text, config)` for a source
rule — and returning a list of `Issue`. It must declare the ids it emits:

```python
import docfix
from docfix.detect.rules import Issue, emits

@emits("house-no-weasel-words")
def check_weasel_words(doc, config):
    """Flag hedging language the style guide bans."""
    banned = config.option("house-no-weasel-words", "words", ["basically", "simply"])
    issues = []
    for block in doc.blocks:
        for run in getattr(block, "runs", []):
            for word in banned:
                if word in run.text.lower():
                    issues.append(
                        Issue("house-no-weasel-words", f"weasel word: {word}", "warning")
                    )
    return issues

docfix.register_rule(check_weasel_words, family="structure")
```

Three families:

| Family | Signature | Runs on |
| --- | --- | --- |
| `structure` | `(doc, config)` | the IR, so every format |
| `source` | `(text, config)` | raw text; binary formats skip it |
| `cv` | `(doc, config)` | only when the document reads as a CV |

**`@emits` is not ceremony.** A config file addresses rules by id, so a rule
that skips it could never be disabled, given a severity, or listed by
`docfix rules`. Registration rejects an undeclared rule for that reason.

Your rule is then configurable exactly like a built-in one:

```toml
[rules]
"house-no-weasel-words" = "error"

[options]
"house-no-weasel-words" = { words = ["basically", "simply", "just"] }
```

### Should it fix, or only report?

docfix repairs cosmetic problems and only reports semantic ones. Whitespace and
marker inconsistency are safe to change; heading levels, quote style and missing
alt text are not, because fixing them changes what the document says. Follow the
same line in your own rules — an auto-fix that alters meaning is the one bug
users will not forgive.

## 3. A file format

An adapter converts one format to and from the IR. It holds **no formatting
rules** — detection, fixing and templates already work on the IR, so an adapter
that parses your format gets all nineteen rules for free.

```python
import docfix
from docfix.adapters import Adapter
from docfix.ir import Document, Heading, Paragraph, Run

def read_path(path: str) -> Document:
    ...
    return Document(blocks=[...])

def write_path(doc: Document, template, path: str) -> None:
    ...

RTF = Adapter(
    name="rtf",
    extensions=(".rtf",),
    read_path=read_path,
    write_path=write_path,
    # Raw text for the source-level rules, or omit for a binary format.
    source_text=lambda path: open(path, encoding="utf-8").read(),
)

docfix.register_adapter(RTF)
```

That is the whole contract. `docfix format notes.rtf`, `docfix check notes.rtf`,
every template, every rule, and conversion in both directions
(`notes.rtf → notes.pdf`) all work immediately, because the reader comes from
the input path and the writer from the output path.

Claiming an extension docfix already handles **overrides** the built-in — that
is how you substitute your own PDF reader without touching ours:

```python
docfix.register_adapter(MY_BETTER_PDF, replace=False)  # .pdf now goes to yours
```

### What to test

Two properties matter more than any single case, and the built-in adapters are
tested for both across every template:

- **Idempotence** — `format(format(x)) == format(x)`.
- **Round-trip stability** — `read(write(doc))` gives back the same block
  structure.

Also assert the source file is untouched. If your format is fixed-layout like
PDF, say plainly that the round trip is lossy rather than implying it is not.

## 4. Shipping it as a package — no fork at all

Everything above works from your own application code. To distribute it, declare
an entry point in **your** `pyproject.toml`:

```toml
[project.entry-points."docfix.plugins"]
acme = "acme_docfix:register"
```

```python
# acme_docfix.py
import docfix

def register():
    docfix.register_adapter(RTF)
    docfix.register_rule(check_weasel_words)
```

`register()` is called once, lazily, the first time docfix looks for an adapter
or a rule. `pip install acme-docfix` is then the entire installation story for
your users, and you never carry a patch against docfix.

A plugin that raises is contained: the error is recorded, docfix keeps working,
and `docfix rules` lists what failed. Your bug must not stop someone formatting
a document.

## 5. When you do fork

Some changes really do need one — different invariants, a different IR, removing
a guarantee we keep. Then:

- The licence is BSD-3-Clause. You may ship it commercially, closed-source,
  modified. Keep the copyright notice and the disclaimer (clause 1 and 2); do
  not use the original author's name to endorse your product (clause 3).
- Add your extensions through the registration API anyway, even inside a fork.
  Your diff then stays out of `adapters/__init__.py`, `detect/rules.py` and
  `cv.py`, which are the files upstream keeps changing — so you can keep
  merging from us instead of diverging.
- `CLAUDE.md` documents the traps that cost real debugging time. Read the
  section for whatever you are touching before you touch it.

---

## Things that will bite you

These are not hypothetical; each one cost someone a debugging session.

- **A rule must not depend on normalization order.** `run_all()` runs on the
  un-normalized document, so a rule that only becomes visible after `normalize()`
  will silently never fire.
- **Changing a bullet marker mid-list starts a new list in CommonMark.** Mixed
  `*`/`+`/`-` arrives as several adjacent `ListBlock`s, not one list.
- **`Run.raw` means "emit verbatim".** It carries inline images. Skip raw runs
  when escaping or rewriting markers.
- **reportlab performs no font fallback**, and a missing glyph is emitted as
  `\x00` with no warning. If you write a PDF path, segment text by what each
  font actually covers.
- **reportlab styles a bullet separately from its text** — set `bulletFontName`
  and `bulletFontSize` on any style you pass `bulletText`, or markers render in
  base-14 Helvetica at the wrong size.

## Fonts and licensing

If your extension embeds fonts, two separate checks apply and neither may be
weakened:

1. **`fsType`** decides whether a font may be embedded at all. Restricted
   License Embedding is refused for everyone, including a font the user names
   explicitly. An open licence does not override it.
2. **The catalogue** (`docfix/fonts/catalog.yaml`) decides whether a font may be
   chosen *automatically*. Only a recognised open licence qualifies.

Licences are read from what the font itself declares (name IDs 13/14), never
inferred from a family name. Adding one means adding a signature to the
catalogue and **verifying its terms against the font's own distribution** — never
asserting them from memory.

docfix ships no font binaries. `docfix/fonts/pinned.yaml` holds SHA-pinned URLs
and checksums, so `docfix fonts install` fetches them and verifies the bytes.
Keep it that way in a fork and you inherit the same clean redistribution story.

## Getting help

- `docfix rules` — every rule, its id, and whether your config disables it.
- `docfix templates` — the bundled templates.
- `docfix fonts` — what fonts resolved, and from where.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — if you would rather send the change
  upstream than carry it.
