#!/usr/bin/env python3
"""Build and lint rubrics for the rubric-based metrics.

A rubric is one sentence a judge answers yes or no. Two shapes:

* **positive** - a property the response must have, stated so that a reader of
  the transcript could settle it: "names the specific limit that was exceeded",
  not "is helpful".
* **restraint** - the action that must not happen, and when: "does NOT call
  `submit_claim` when the cost centre is frozen". Trajectory matching proves
  what the agent did; only this proves what it refused to do.

    gen_rubrics.py --kind restraint --tool submit_claim \
        --condition "the cost centre is frozen" --agent-dir app
    gen_rubrics.py --kind positive --spec '[{"id": "cites_limit", "text": "..."}]'
    gen_rubrics.py --lint existing_rubrics.json --agent-dir app

Exit codes: 0 clean, 1 lint findings, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.metrics import METRIC_CATALOG  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.rubrics import (  # noqa: E402
    build_positive_rubric, build_restraint_rubric, dedupe_ids, lint_all, suggest_rubric_id,
)
from adk_skills.templating import dumps_json  # noqa: E402
from adk_skills.tools_ast import extract_module  # noqa: E402

RUBRIC_TYPE_BY_KIND = {
    "positive": "FINAL_RESPONSE_QUALITY",
    "restraint": "TOOL_USE_QUALITY",
    "trajectory": "TRAJECTORY_QUALITY",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kind", choices=("positive", "restraint", "trajectory"), default="positive")
    parser.add_argument("--tool", help="restraint only: the tool that must not be called")
    parser.add_argument("--condition", help="restraint only: when it must not be called")
    parser.add_argument("--spec", help='JSON list of {"id"?, "text"} or {"tool", "condition"}')
    parser.add_argument("--lint", help="lint an existing rubrics JSON file instead of generating")
    parser.add_argument("--agent-dir", help="agent, so tool names in rubrics can be checked")
    parser.add_argument("--no-type", action="store_true",
                        help="omit `type` (correct for criterion-level rubrics, which are not filtered)")
    parser.add_argument("--out", help="write to this file instead of stdout")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    known_tools: set[str] | None = None
    if args.agent_dir and Path(args.agent_dir).exists():
        known_tools = extract_module(args.agent_dir).bound_tool_names or None

    rubrics: list[dict[str, Any]] = []

    if args.lint:
        try:
            loaded = json.loads(Path(args.lint).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        rubrics = loaded if isinstance(loaded, list) else loaded.get("rubrics", [])
    elif args.spec:
        try:
            entries = json.loads(args.spec)
        except json.JSONDecodeError as exc:
            print(f"error: --spec is not valid JSON: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if not isinstance(entries, list):
            print("error: --spec must be a JSON list", file=sys.stderr)
            return EXIT_USAGE
        for entry in entries:
            if not isinstance(entry, dict):
                print("error: each --spec entry must be an object", file=sys.stderr)
                return EXIT_USAGE
            rubric_type = None if args.no_type else RUBRIC_TYPE_BY_KIND[args.kind]
            if entry.get("tool") and entry.get("condition"):
                rubrics.append(build_restraint_rubric(
                    entry.get("id") or f"no_{entry['tool']}_when_"
                    + suggest_rubric_id(entry["condition"]),
                    entry["tool"], entry["condition"],
                    rubric_type=None if args.no_type else "TOOL_USE_QUALITY",
                ))
            elif entry.get("text"):
                rubrics.append(build_positive_rubric(
                    entry.get("id") or suggest_rubric_id(entry["text"]),
                    entry["text"], rubric_type=rubric_type,
                ))
            else:
                print("error: each entry needs `text`, or `tool` and `condition`", file=sys.stderr)
                return EXIT_USAGE
    elif args.kind == "restraint" and args.tool and args.condition:
        rubrics.append(build_restraint_rubric(
            f"no_{args.tool}_when_{suggest_rubric_id(args.condition)}",
            args.tool, args.condition,
            rubric_type=None if args.no_type else "TOOL_USE_QUALITY",
        ))
    else:
        print("error: pass --spec, --lint, or (--kind restraint --tool X --condition Y)",
              file=sys.stderr)
        return EXIT_USAGE

    rubrics = dedupe_ids(rubrics)
    report = GapReport(subject=args.lint or "(generated)", skill="eval-criteria")
    report.extend(lint_all(rubrics, known_tools=known_tools, path=args.lint))
    report.discovered = {
        "rubrics": len(rubrics),
        "kind": args.kind,
        "metric": {
            "positive": "rubric_based_final_response_quality_v1",
            "restraint": "rubric_based_tool_use_quality_v1",
            "trajectory": "rubric_based_multi_turn_trajectory_quality_v1",
        }[args.kind],
        "known_tools": sorted(known_tools) if known_tools else None,
    }
    metric = METRIC_CATALOG[report.discovered["metric"]]
    report.discovered["suggested_threshold"] = (
        1.0 if args.kind == "restraint" else metric.default_threshold
    )

    rendered = dumps_json(rubrics)
    if args.json:
        print(json.dumps({"rubrics": rubrics, "lint": report.to_dict()}, indent=2))
    else:
        if not args.lint:
            print(rendered)
        if report.findings:
            print(report.to_md())
        else:
            print(f"{len(rubrics)} rubric(s), no lint findings.")
        if args.kind == "restraint":
            print("Use with rubric_based_tool_use_quality_v1 at threshold 1.0: "
                  "a restraint guarantee is all or nothing.")

    if args.out and not args.lint:
        Path(args.out).write_text(rendered, encoding="utf-8")
        print(f"wrote {args.out}")

    return EXIT_FINDINGS if report.findings else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
