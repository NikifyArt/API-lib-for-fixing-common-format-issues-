"""Command line interface.

    docfix format notes.md --template formal
    docfix check notes.md
    docfix templates
"""

from __future__ import annotations

import argparse
import os
import sys

import docfix
from docfix import adapters
from docfix.templates import TemplateError

EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_ERROR = 2

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


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


def cmd_format(args) -> int:
    if not _confirm_lossy_conversion(args):
        return EXIT_ERROR

    result = docfix.format_file(
        args.file,
        template=args.template,
        output=args.output,
        keep_intermediate=args.keep_intermediate,
    )
    print(f"{result.source_path} -> {result.output_path}  [template: {result.template}]")
    if result.intermediate_path:
        print(f"  extracted Markdown kept at {result.intermediate_path}")
    print(f"  {len(result.fixed)} fixed automatically, {len(result.remaining)} to review")
    if result.remaining and not args.quiet:
        print("\nNeeds a look:")
        _print_issues(result.remaining, sys.stdout)
    return EXIT_OK


def cmd_check(args) -> int:
    issues = docfix.detect(args.file)
    print(f"{args.file}: {_summary(issues)}")
    if issues:
        _print_issues(issues, sys.stdout)
        return EXIT_ISSUES
    return EXIT_OK


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


def cmd_templates(args) -> int:
    for name in docfix.list_templates():
        template = docfix.load(name)
        print(f"{name:12} {template.description}")
    return EXIT_OK


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
    fmt.add_argument("file")
    fmt.add_argument(
        "-t",
        "--template",
        default=docfix.DEFAULT_TEMPLATE,
        help=f"preset name or path to a YAML template (default: {docfix.DEFAULT_TEMPLATE})",
    )
    fmt.add_argument(
        "-o",
        "--output",
        default=None,
        help="output path (default: NAME.formatted.EXT; the source is never modified)",
    )
    fmt.add_argument("-q", "--quiet", action="store_true", help="suppress the issue list")
    fmt.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="accept a lossy PDF conversion without being asked",
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
    fmt.set_defaults(func=cmd_format)

    check = subparsers.add_parser(
        "check", help="report issues without writing anything (exit 1 if any are found)"
    )
    check.add_argument("file")
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

    templates = subparsers.add_parser("templates", help="list the bundled templates")
    templates.set_defaults(func=cmd_templates)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (TemplateError, adapters.UnsupportedFormatError, ImportError, ValueError) as exc:
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
