#!/usr/bin/env python3
"""Report what an ADK project contains, what is missing, and what to do next.

Standard library only: it never imports `google.adk`, so it works before the
project's environment does. Run it before proposing anything.

    discover.py <path> [--format json|md] [--focus layout,env,evalsets,...]

Exit codes: 0 nothing wrong, 1 errors found, 2 the target is unusable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.discover import ALL_FOCUS, discover, suggest_next_skills  # noqa: E402
from adk_skills.report import EXIT_USAGE, exit_code_for  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent directory or project root")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format",
                        help="shorthand for --format json")
    parser.add_argument("--focus", default=",".join(ALL_FOCUS),
                        help=f"comma-separated subset of: {', '.join(ALL_FOCUS)}")
    parser.add_argument("--no-probe", action="store_true",
                        help="skip probing for an interpreter with google.adk (faster)")
    parser.add_argument("--skill", default="adk", help="name to record in the report")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    focus = [item.strip() for item in args.focus.split(",") if item.strip()]
    unknown = [item for item in focus if item not in ALL_FOCUS]
    if unknown:
        print(f"error: unknown focus {', '.join(unknown)}; choose from {', '.join(ALL_FOCUS)}",
              file=sys.stderr)
        return EXIT_USAGE

    discovery, report = discover(target, focus=focus, probe_runner=not args.no_probe, skill=args.skill)
    for option in suggest_next_skills(discovery):
        report.add_option(option)

    print(report.render(args.format))
    code = exit_code_for(report)
    if code == 0 and args.strict and report.count("warn"):
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
