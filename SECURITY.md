# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue.

Use GitHub's private vulnerability reporting on this repository:
**Security → Report a vulnerability**. That opens a private thread visible only
to the maintainers.

Include what you can — affected version, a reproduction (the smallest input file
that triggers it), and what an attacker gets. A malformed document that
reproduces the problem is worth more than a description of it.

Expect an acknowledgement within a week. If a fix is warranted we will agree a
disclosure timeline with you before publishing.

## Supported versions

docfix is pre-1.0 and only the latest release is supported. Fixes land on `main`
and go out in the next release rather than being backported.

## What docfix actually does with your input

This section exists so you can judge the risk yourself rather than take a
promise. docfix processes files that may be untrusted, and it is worth being
clear about where that input goes.

**It parses untrusted binary formats through third-party libraries.**
`.pdf` goes through `pdfplumber`/`pdfminer.six`, `.docx` through `python-docx`
(and so through `zipfile` and an XML parser), and font files through docfix's
own `fonts/sfnt.py`. A malicious document is therefore first of all a question
about those parsers. Keep them updated; docfix pins only lower bounds.

**It reads font binaries itself.** `sfnt.py` parses tables from files found on
your system or in the pinned cache. It is written to fail closed — a table that
does not parse makes the font unusable rather than trusted — but it reads
attacker-influenced bytes if an attacker can place a font where docfix looks.

**It writes only to a new path.** `format_file` refuses to write over its input.
There is no in-place mode. Output paths come from you or are derived from the
input name; docfix does not follow anything inside a document to decide where to
write.

**It executes no document content.** There is no macro, script or formula
evaluation anywhere. Documents are data.

**`docfix fonts install` makes network requests**, and only then. Every URL in
`docfix/fonts/pinned.yaml` pins an immutable commit SHA, and every file is
checked against a recorded SHA-256. A mismatch deletes the file and fails —
never a warning. No other command touches the network.

**Plugins are code you install.** A `docfix.plugins` entry point is imported and
called. That is the same trust boundary as any Python dependency: installing a
docfix plugin is installing code. docfix contains a plugin that *fails*, but it
cannot contain one that is hostile.

**Config files are parsed, not executed.** `docfix.toml` and `[tool.docfix]` go
through `tomllib`/`tomli`. Templates are YAML loaded with a safe loader — no
arbitrary object construction.

## Repository hygiene

docfix ships no credentials, and there are none in the working tree or in the
git history. That is checkable rather than asserted:

```bash
# Every path that has ever existed, including ones later deleted. Read it:
# there should be no .env, key, or credential file among them. At the time of
# writing all 69 paths still exist -- nothing has ever been added and removed.
git log --all --pretty=format: --name-only --diff-filter=A | sort -u

# Any credential shape in any historical blob. This one must print nothing.
git rev-list --all --objects | awk '{print $1}' | sort -u | while read -r sha; do
  [ "$(git cat-file -t "$sha")" = blob ] || continue
  git cat-file -p "$sha" | grep -aoE '(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'
done
```

The second prints nothing. Test fixtures use reserved-by-standard values on purpose
— `.test` addresses (RFC 6761) and the Ofcom fictional phone range — so no
fixture can ever resemble a real contact.

`.gitignore` covers credentials, key material and per-developer tool state as a
guard against future accidents. The one file it must never start ignoring is
`.claude/settings.json`, which registers a hook the project depends on; the
per-developer `.claude/settings.local.json` is ignored instead.

**Releases carry no token.** PyPI publishing uses Trusted Publishing (OIDC), so
there is no long-lived secret in this repository or in its GitHub Actions
secrets to leak. Do not add one — see `docs/RELEASING.md`.

## Out of scope

- Vulnerabilities in `pdfplumber`, `reportlab`, `python-docx`, `markdown-it-py`
  or `PyYAML` themselves. Report those upstream; tell us too if docfix's usage
  makes them reachable in a way the library does not intend.
- A hostile plugin you installed deliberately.
- Denial of service from a legitimately enormous document.
- Font licence violations. Those matter to us — see the licensing rules in
  `CLAUDE.md` and `docs/EXTENDING.md` — but they are a compliance issue, not a
  security one, and a public issue is the right place.
