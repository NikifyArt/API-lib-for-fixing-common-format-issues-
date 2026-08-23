"""Command line interface.

    docfix format notes.md --template formal
    docfix check notes.md
    docfix templates
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import os
import sys
import tempfile

import docfix
from docfix import adapters
from docfix.config import ConfigError, discover
from docfix.templates import TemplateError

EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_ERROR = 2

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

def _reason(exc: Exception) -> str:
    """A one-line explanation of why one file could not be handled.

    Deliberately broad at the call sites: a parser deep inside an optional
    dependency raises its own exception type -- pdfplumber's PdfminerException
    is neither an OSError nor a ValueError -- and one unreadable file must not
    abort a batch. The type is included so a genuine bug is still diagnosable.
    """
    if isinstance(exc, FileNotFoundError):
        return f"no such file: {exc.filename}"
    if isinstance(exc, (TemplateError, ConfigError, adapters.UnsupportedFormatError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _print_issues(issues, stream) -> None:
    for issue in sorted(
        issues, key=lambda i: (SEVERITY_ORDER.get(i.severity, 3), i.line or 0, i.rule)
    ):
        print(f"  {issue}", file=stream)


def _summary(issues) -> str:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    if not counts:
        return "no issues found"
    return ", ".join(f"{counts[s]} {s}" for s in ("error", "warning", "info") if s in counts)


# --------------------------------------------------------------------------
# Target expansion
# --------------------------------------------------------------------------


def _excluded(path: str, patterns: list[str]) -> bool:
    """Whether a path matches an exclude glob.

    fnmatch's `*` crosses separators, unlike a shell's, so `vendor/**` and
    `**/NOTES.md` both behave as expected once the `**/` prefix is also tried
    against the bare name.
    """
    # normpath first: walking "." yields "./vendor/x.md", and the leading "./"
    # would stop a "vendor/**" pattern from matching.
    posix = os.path.normpath(path).replace(os.sep, "/")
    name = os.path.basename(posix)
    for pattern in patterns:
        bare = pattern[3:] if pattern.startswith("**/") else pattern
        candidates = (pattern, bare, f"*/{bare}")
        if any(fnmatch.fnmatch(posix, c) for c in candidates) or fnmatch.fnmatch(name, bare):
            return True
    return False


def _targets(
    paths: list[str], exclude: list[str], skip: str | None = None
) -> list[tuple[str, str]]:
    """Every formattable file named by the arguments, as (path, base) pairs.

    `base` is the directory the file was found under, so `--out-dir` can mirror
    the tree rather than flatten it. A directory is walked; a file is taken as
    given, so naming one explicitly works even for an extension no adapter
    claims -- the error then comes from the adapter, which says something useful.

    `skip` excludes an output directory, so running the same command twice does
    not pick up its own results.
    """
    supported = set(adapters.supported_extensions())
    found: dict[str, str] = {}
    skip_real = os.path.realpath(skip) if skip else None

    for entry in paths:
        if os.path.isdir(entry):
            for root, dirs, files in os.walk(entry):
                if skip_real and os.path.realpath(root).startswith(skip_real):
                    dirs[:] = []
                    continue
                dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
                for name in sorted(files):
                    candidate = os.path.join(root, name)
                    if os.path.splitext(name)[1].lower() not in supported:
                        continue
                    # Skip what docfix itself produced.
                    if f".{docfix.OUTPUT_INFIX}." in name:
                        continue
                    if _excluded(candidate, exclude):
                        continue
                    found.setdefault(candidate, entry)
        elif not _excluded(entry, exclude):
            found.setdefault(entry, os.path.dirname(entry) or ".")

    return sorted(found.items())


def _destination(source: str, base: str, args) -> str | None:
    """Where one file's output goes, or None for the default alongside it.

    Mirrors the source tree under --out-dir: flattening to the basename would
    silently overwrite `a/README.md` with `b/README.md`.
    """
    if getattr(args, "out_dir", None):
        relative = os.path.relpath(source, base)
        if relative.startswith(".."):
            relative = os.path.basename(source)
        target = os.path.join(args.out_dir, relative)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        return target
    return args.output


def _confirm_lossy_conversion(args) -> bool:
    """Show what a PDF conversion would damage and ask before doing it.

    PDF is fixed-layout: `docfix` cannot edit one in place, it extracts the
    content and generates a new file. That is exactly the situation where the
    user should see what is about to be lost before it happens.
    """
    from docfix.adapters import pdf

    if os.path.splitext(args.file)[1].lower() not in pdf.EXTENSIONS:
        return True

    try:
        report = docfix.scan(args.file)
    except pdf.MissingDependencyError:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed scan must not block the user
        print(f"docfix: could not scan {args.file}: {exc}", file=sys.stderr)
        return True

    if not report.needs_confirmation():
        return True

    print(report.report(), file=sys.stderr)
    print(file=sys.stderr)
    print(
        "docfix cannot restyle a PDF in place. It extracts the text and builds a\n"
        "new PDF, so the items above will not survive. The original file is not\n"
        "touched either way.",
        file=sys.stderr,
    )
    if report.blocking_pages:
        print(
            f"\n{len(report.blocking_pages)} page(s) have no text layer at all -- "
            "the converted output will be missing that content entirely.",
            file=sys.stderr,
        )
    print(
        "\nTip: --keep-intermediate writes the extracted Markdown so you can check\n"
        "and correct it before converting.",
        file=sys.stderr,
    )

    if args.yes:
        return True

    if not sys.stdin.isatty():
        print(
            "\ndocfix: refusing to convert unattended. Re-run with --yes to accept "
            "the losses above.",
            file=sys.stderr,
        )
        return False

    try:
        answer = input("\nConvert anyway? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False
    return answer in ("y", "yes")


def cmd_scan(args) -> int:
    report = docfix.scan(args.file)
    print(report.report(max_pages=args.max_pages))
    if report.blocking_pages:
        return EXIT_ISSUES
    return EXIT_OK


def _diff_one(path: str, args, config) -> tuple[bool, str]:
    """Format into a scratch file and describe what would change.

    Binary formats cannot be diffed meaningfully, so they report that they
    would be rewritten rather than pretending to show a change.
    """
    suffix = os.path.splitext(path)[1]
    with tempfile.TemporaryDirectory() as scratch:
        target = os.path.join(scratch, f"preview{suffix}")
        docfix.format_file(
            path,
            template=args.template,
            output=target,
            embed_cjk=args.embed_cjk,
            cv=_cv_flag(args),
            config=config,
        )
        try:
            with open(path, encoding="utf-8") as handle:
                before = handle.read().splitlines(keepends=True)
            with open(target, encoding="utf-8") as handle:
                after = handle.read().splitlines(keepends=True)
        except (UnicodeDecodeError, OSError):
            with open(path, "rb") as handle:
                original = handle.read()
            with open(target, "rb") as handle:
                formatted = handle.read()
            changed = original != formatted
            return changed, (f"would rewrite {path}\n" if changed else "")

    if before == after:
        return False, ""
    diff = difflib.unified_diff(before, after, fromfile=path, tofile=f"{path} (formatted)")
    return True, "".join(diff)


def cmd_format(args) -> int:
    config = _config(args)
    files = _targets(args.file, config.exclude, skip=args.out_dir)
    if not files:
        print("docfix: nothing to format", file=sys.stderr)
        return EXIT_OK

    if (args.output or args.keep_intermediate) and len(files) > 1:
        print(
            "docfix: -o and --keep-intermediate take a single file; "
            "use --out-dir for several",
            file=sys.stderr,
        )
        return EXIT_ERROR

    if args.diff:
        changed = failed = 0
        for path, _base in files:
            try:
                differs, text = _diff_one(path, args, config)
            except Exception as exc:  # noqa: BLE001 - see _reason
                failed += 1
                print(f"docfix: {path}: {_reason(exc)}", file=sys.stderr)
                continue
            if differs:
                changed += 1
                sys.stdout.write(text)
        print(f"\n{changed} of {len(files)} file(s) would change")
        if failed:
            return EXIT_ERROR
        return EXIT_ISSUES if changed else EXIT_OK

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)

    status = EXIT_OK
    for path, base in files:
        single = argparse.Namespace(**vars(args))
        single.file = path
        if not _confirm_lossy_conversion(single):
            status = EXIT_ERROR
            continue
        try:
            _format_one(path, base, args, config)
        except Exception as exc:  # noqa: BLE001 - see _reason
            status = EXIT_ERROR
            print(f"docfix: {path}: {_reason(exc)}", file=sys.stderr)
    return status


def _format_one(path: str, base: str, args, config) -> None:
    result = docfix.format_file(
        path,
        template=args.template,
        output=_destination(path, base, args),
        keep_intermediate=args.keep_intermediate,
        embed_cjk=args.embed_cjk,
        cv=_cv_flag(args),
        config=config,
    )
    print(f"{result.source_path} -> {result.output_path}  [template: {result.template}]")
    if result.intermediate_path:
        print(f"  extracted Markdown kept at {result.intermediate_path}")
    print(f"  {len(result.fixed)} fixed automatically, {len(result.remaining)} to review")
    if result.remaining and not args.quiet:
        print("Needs a look:")
        _print_issues(result.remaining, sys.stdout)


def _config(args):
    """The project config for this run, or the defaults."""
    return discover(
        explicit=getattr(args, "config", None),
        use_config=not getattr(args, "no_config", False),
    )


def _cv_flag(args) -> bool | None:
    """--cv forces the résumé rules on, --no-cv off, neither auto-detects."""
    if getattr(args, "cv", False):
        return True
    if getattr(args, "no_cv", False):
        return False
    return None


def cmd_check(args) -> int:
    config = _config(args)
    files = _targets(args.file, config.exclude)
    if not files:
        print("docfix: nothing to check", file=sys.stderr)
        return EXIT_OK

    total = 0
    failed = 0
    for path, _base in files:
        try:
            issues = docfix.detect(path, cv=_cv_flag(args), config=config)
        except Exception as exc:  # noqa: BLE001 - see _reason
            # One unreadable file must not stop the batch, or a CI gate reports
            # an error while having examined only part of the tree.
            failed += 1
            print(f"docfix: {path}: {_reason(exc)}", file=sys.stderr)
            continue
        total += len(issues)
        print(f"{path}: {_summary(issues)}")
        if issues:
            _print_issues(issues, sys.stdout)

    if len(files) > 1:
        print(f"\n{len(files)} file(s), {total} issue(s)")
    if failed:
        return EXIT_ERROR
    return EXIT_ISSUES if total else EXIT_OK


def cmd_fonts(args) -> int:
    """Show the font pool: what is installed, what may be used, and why."""
    from docfix.fonts import describe_fs_type, pool

    available = pool()

    if args.family:
        family = available.get(args.family)
        if family is None:
            print(f"docfix: no font family named {args.family!r}", file=sys.stderr)
            return EXIT_ERROR
        face = family.regular
        print(f"{family.name}")
        print(f"  category    {family.category}")
        print(f"  styles      {', '.join(family.styles)}")
        print(f"  licence     {family.license.name} ({family.license.id})")
        print(f"  embedding   {describe_fs_type(face.fs_type if face else None)}")
        print(f"  usable      {family.usable}")
        print(f"  auto-select {family.auto_selectable}")
        print(f"  glyphs      {len(family.coverage())}")
        for style in family.styles:
            print(f"  {style:11} {family.faces[style].path}")
        return EXIT_OK

    families = available.usable_families()
    if not families:
        print("No usable fonts found. docfix will fall back to the PDF base-14,")
        print("which covers Latin-1 only; anything beyond it will be reported.")
        return EXIT_OK

    print(f"{'family':26} {'cat':6} {'styles':4} {'licence':20} auto")
    for family in families:
        print(
            f"{family.name:26} {family.category:6} {len(family.styles):<4} "
            f"{family.license.id:20} {'yes' if family.auto_selectable else 'no'}"
        )

    auto = sum(1 for f in families if f.auto_selectable)
    print(f"\n{len(families)} usable, {auto} auto-selectable (open licence).")
    if auto < len(families):
        print(
            "Fonts without a recognised open licence are never chosen automatically; "
            "name one explicitly to use it."
        )
    if available.skipped:
        print(f"{len(available.skipped)} file(s) skipped -- use --skipped to see why.")
    if args.skipped:
        for path, reason in available.skipped:
            print(f"  {os.path.basename(path):34} {reason}")
    return EXIT_OK


def cmd_rules(args) -> int:
    """The rule inventory, which is what a user needs before configuring one."""
    from docfix.cv import CV_RULES
    from docfix.detect.rules import SOURCE_RULES, STRUCTURE_RULES

    config = _config(args)
    families = [
        ("structure", STRUCTURE_RULES, "runs on the document structure, any format"),
        ("source", SOURCE_RULES, "runs on raw text; binary formats skip these"),
        ("cv", CV_RULES, "runs only when the document is a CV; report-only"),
    ]

    if config.source:
        print(f"config: {config.source}\n")

    for name, rules, note in families:
        print(f"{name} rules -- {note}")
        for rule in rules:
            summary = (rule.__doc__ or "").strip().split("\n")[0]
            for rule_id in getattr(rule, "rule_ids", ()):
                marker = " " if config.enabled(rule_id) else "-"
                print(f"  {marker} {rule_id:28} {summary}")
                summary = ""
        print()

    from docfix.adapters.pdf import COVERAGE_RULE_IDS

    print("font rules -- reported when writing a PDF")
    for rule_id, summary in COVERAGE_RULE_IDS:
        marker = " " if config.enabled(rule_id) else "-"
        print(f"  {marker} {rule_id:28} {summary}")
    print()

    disabled = [name for name, setting in config.rules.items() if setting is False]
    overridden = {n: s for n, s in config.rules.items() if isinstance(s, str)}
    if disabled:
        print(f"disabled by config: {', '.join(sorted(disabled))}")
    if overridden:
        print("severity overrides: " + ", ".join(f"{n}={v}" for n, v in sorted(overridden.items())))
    if config.options:
        print("options set: " + ", ".join(sorted(config.options)))
    if not config.source:
        print("No config file found. See `docfix rules --help` for where one may live.")
    return EXIT_OK


def cmd_templates(args) -> int:
    for name in docfix.list_templates():
        template = docfix.load(name)
        print(f"{name:12} {template.description}")
    return EXIT_OK


def _add_config_flags(parser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="use this config file instead of searching for docfix.toml "
        "or a [tool.docfix] table in pyproject.toml",
    )
    group.add_argument(
        "--no-config",
        dest="no_config",
        action="store_true",
        help="ignore any config file",
    )


def _add_cv_flags(parser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--cv",
        action="store_true",
        help="apply the r\u00e9sum\u00e9 rules (reverse-chronological order, contact "
        "details, bullet phrasing); they only report, never rewrite",
    )
    group.add_argument(
        "--no-cv",
        dest="no_cv",
        action="store_true",
        help="skip the r\u00e9sum\u00e9 rules even if the document looks like a CV",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docfix",
        description="Find and fix common formatting problems in documents.",
    )
    parser.add_argument("--version", action="version", version=f"docfix {docfix.__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fmt = subparsers.add_parser(
        "format", help="normalize a document and write it to a new file"
    )
    fmt.add_argument("file", nargs="+", metavar="PATH", help="files or directories")
    fmt.add_argument(
        "-t",
        "--template",
        default=None,
        help="preset name or path to a YAML template "
        f"(default: the config's template, else {docfix.DEFAULT_TEMPLATE})",
    )
    fmt.add_argument(
        "-o",
        "--output",
        default=None,
        help="output path (default: NAME.formatted.EXT; the source is never modified)",
    )
    fmt.add_argument(
        "--out-dir",
        default=None,
        metavar="DIR",
        help="write every result into this directory instead of alongside its source",
    )
    fmt.add_argument(
        "--diff",
        action="store_true",
        help="show what would change and write nothing (exit 1 if anything would)",
    )
    fmt.add_argument("-q", "--quiet", action="store_true", help="suppress the issue list")
    fmt.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="accept a lossy PDF conversion without being asked",
    )
    fmt.add_argument(
        "--embed-cjk",
        action="store_true",
        help="embed an installed CJK font instead of relying on the reader's own, "
        "making the PDF self-contained (falls back to the built-in CID fonts)",
    )
    fmt.add_argument(
        "--keep-intermediate",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help="also write the extracted content as Markdown, so it can be checked "
        "or hand-corrected (default: NAME.extracted.md)",
    )
    _add_cv_flags(fmt)
    _add_config_flags(fmt)
    fmt.set_defaults(func=cmd_format)

    check = subparsers.add_parser(
        "check", help="report issues without writing anything (exit 1 if any are found)"
    )
    check.add_argument("file", nargs="+", metavar="PATH", help="files or directories")
    _add_cv_flags(check)
    _add_config_flags(check)
    check.set_defaults(func=cmd_check)

    scan = subparsers.add_parser(
        "scan",
        help="inspect a PDF page by page and report what conversion would put at risk",
    )
    scan.add_argument("file")
    scan.add_argument(
        "--max-pages",
        type=int,
        default=12,
        help="how many risky pages to list individually (default: 12)",
    )
    scan.set_defaults(func=cmd_scan)

    fonts = subparsers.add_parser(
        "fonts", help="list the fonts available for PDF output, with their licences"
    )
    fonts.add_argument(
        "--family", default=None, help="show full detail for one family"
    )
    fonts.add_argument(
        "--skipped", action="store_true", help="also list font files that cannot be used"
    )
    fonts.set_defaults(func=cmd_fonts)

    rules = subparsers.add_parser(
        "rules", help="list every rule, and what the config does to them"
    )
    _add_config_flags(rules)
    rules.set_defaults(func=cmd_rules)

    templates = subparsers.add_parser("templates", help="list the bundled templates")
    templates.set_defaults(func=cmd_templates)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (
        TemplateError,
        ConfigError,
        adapters.UnsupportedFormatError,
        ImportError,
        ValueError,
    ) as exc:
        print(f"docfix: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except FileNotFoundError as exc:
        print(f"docfix: no such file: {exc.filename}", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        # Something downstream closed the pipe -- `docfix templates | head`.
        # Point stdout at devnull so the interpreter does not print a second
        # error while flushing on exit.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
