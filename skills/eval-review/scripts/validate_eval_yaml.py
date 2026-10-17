#!/usr/bin/env python3
"""Validate the `EVAL.yaml` files of an Agent-Plugins skill layer.

google/adk-recipes ships installable skills under `plugins/<plugin>/skills/`,
each with an `EVAL.yaml`: a list of cases, each a prompt, LLM-judge rubric lines
phrased MUST / MUST NOT / SHOULD, and optional deterministic assertions
(`tool_called`, `price_under`, `no_hallucination`, ...). It is not an ADK
evalset, no ADK command runs it, and no file declares a pass threshold (the
recipes state an 80% target in the skill's prose). This checks the files
statically and never runs anything:

    EVY001  error  the YAML cannot be parsed
    EVY002  error  no `cases` list, or an empty one
    EVY003  error  a case without an `id` (or not a mapping)
    EVY004  error  a case without a `prompt`
    EVY005  error  a duplicate `id`
    EVY006  error  an empty `rubric` and no assertions (warn when assertions exist)
    EVY007  warn   a rubric line without MUST or SHOULD
    EVY008  error  an unknown assertion type (or `assertions` not a list)
    EVY009  error  an assertion missing the field its type needs
    EVY010  info   `tool_called` names a tool the sibling SKILL.md never mentions

    validate_eval_yaml.py [path] [--json] [--strict]

`path` is an EVAL.yaml, a skill directory, a plugin directory or a repository;
every EVAL.yaml under it is checked.

Exit codes: 0 no errors, 1 errors (or warnings with --strict), 2 nothing to check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.eval_yaml import (  # noqa: E402
    find_eval_yamls, load_eval_yaml, plugin_layer, validate_eval_yaml,
)
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".",
                        help="an EVAL.yaml, a skill or plugin directory, or a repository")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE
    files = find_eval_yamls(target)
    if not files:
        print(f"error: no EVAL.yaml under {target}", file=sys.stderr)
        return EXIT_USAGE

    report = GapReport(subject=str(target), skill="eval-review")
    summaries = []
    for path in files:
        loaded = load_eval_yaml(path)
        summaries.append(loaded.summary())
        report.extend(validate_eval_yaml(loaded))
    layer = plugin_layer(target) if target.is_dir() else None
    report.discovered = {
        "eval_yamls": summaries,
        "plugins": layer["plugins"] if layer else [],
        "cases": sum(s["cases"] for s in summaries),
        "rubric_lines": sum(s["rubric_lines"] for s in summaries),
        "assertions": sum(s["assertions"] for s in summaries),
        "threshold_declared": any(s["threshold"] is not None for s in summaries),
        "runs_anything": False,
    }

    print(report.render(args.format))
    if report.has_errors():
        return EXIT_FINDINGS
    if args.strict and report.count("warn"):
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
