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


def cmd_format(args) -> int:
    result = docfix.format_file(args.file, template=args.template, output=args.output)
    print(f"{result.source_path} -> {result.output_path}  [template: {result.template}]")
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
    fmt.set_defaults(func=cmd_format)

    check = subparsers.add_parser(
        "check", help="report issues without writing anything (exit 1 if any are found)"
    )
    check.add_argument("file")
    check.set_defaults(func=cmd_check)

    templates = subparsers.add_parser("templates", help="list the bundled templates")
    templates.set_defaults(func=cmd_templates)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (TemplateError, adapters.UnsupportedFormatError, ValueError) as exc:
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
