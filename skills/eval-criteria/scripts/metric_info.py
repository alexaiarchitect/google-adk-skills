#!/usr/bin/env python3
"""Look up ADK's evaluation metrics: what they need, cost, and when to use them.

    metric_info.py                 # the whole catalog as a table
    metric_info.py hallucinations_v1
    metric_info.py --tier pr       # only metrics cheap enough to gate every change
    metric_info.py --user-sim      # only metrics that work with simulated users
    metric_info.py --json

Exit codes: 0 found, 2 unknown metric name.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.metrics import (  # noqa: E402
    DEFAULT_CRITERIA, JUDGE_DEFAULTS, MATCH_TYPES, METRIC_CATALOG, suggest,
)
from adk_skills.report import EXIT_OK, EXIT_USAGE  # noqa: E402


def describe(name: str) -> str:
    spec = METRIC_CATALOG[name]
    lines = [
        f"# {spec.name}",
        "",
        spec.summary,
        "",
        f"- cost: **{spec.cost}**" + {
            "free": " (deterministic, no model calls)",
            "judge": " (a judge model scores each invocation, times num_samples)",
            "vertex": " (Vertex AI Gen AI Evaluation Service: needs project and location)",
        }[spec.cost],
        f"- criterion class: `{spec.criterion_class}`",
        f"- value range: {spec.value_range[0]:g} to {spec.value_range[1]:g}, higher is better",
        f"- suggested threshold: {spec.default_threshold:g}",
        f"- needs a reference in the eval case: {'yes, ' + spec.reference_kind if spec.reference_based else 'no'}",
        f"- needs rubrics: {'yes' if spec.requires_rubrics else 'no'}"
        + (f" (per-case rubrics must have type `{spec.rubric_type}`)" if spec.rubric_type else ""),
        f"- works with user simulation: {'yes' if spec.user_sim_compatible else 'no'}",
        f"- CI tier: {spec.ci_tier}",
    ]
    if spec.extra_fields:
        lines.append(f"- criterion fields: {', '.join(f'`{f}`' for f in spec.extra_fields)}")
    if spec.name == "tool_trajectory_avg_score":
        lines.append(f"- match types: {', '.join(MATCH_TYPES)}")
    if spec.when:
        lines.extend(["", "**When to use it.** " + spec.when])
    return "\n".join(lines)


def table(names: list[str]) -> str:
    header = "| metric | cost | reference | rubrics | user sim | range | threshold | tier |"
    lines = [header, "|---|---|---|---|---|---|---|---|"]
    for name in names:
        spec = METRIC_CATALOG[name]
        lines.append(
            f"| `{spec.name}` | {spec.cost} | {spec.reference_kind or 'no'} | "
            f"{'required' if spec.requires_rubrics else 'no'} | "
            f"{'yes' if spec.user_sim_compatible else 'no'} | "
            f"{spec.value_range[0]:g}-{spec.value_range[1]:g} | "
            f"{spec.default_threshold:g} | {spec.ci_tier} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", nargs="?", help="a metric name; omit for the whole catalog")
    parser.add_argument("--tier", choices=("pr", "nightly"))
    parser.add_argument("--user-sim", action="store_true", help="only simulation-compatible metrics")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.name:
        if args.name not in METRIC_CATALOG:
            hint = suggest(args.name)
            print(f"error: unknown metric {args.name!r}" + (f"; did you mean {hint!r}?" if hint else ""),
                  file=sys.stderr)
            return EXIT_USAGE
        if args.json:
            print(json.dumps(METRIC_CATALOG[args.name].to_dict(), indent=2))
        else:
            print(describe(args.name))
        return EXIT_OK

    names = list(METRIC_CATALOG)
    if args.tier:
        names = [n for n in names if METRIC_CATALOG[n].ci_tier == args.tier]
    if args.user_sim:
        names = [n for n in names if METRIC_CATALOG[n].user_sim_compatible]

    if args.json:
        print(json.dumps({
            "metrics": [METRIC_CATALOG[n].to_dict() for n in names],
            "defaults_when_no_config": DEFAULT_CRITERIA,
            "judge_defaults": JUDGE_DEFAULTS,
        }, indent=2))
    else:
        print(table(names))
        print()
        print("With no config at all, ADK uses: "
              + ", ".join(f"`{k}` {v:g}" for k, v in DEFAULT_CRITERIA.items()) + ".")
        print("Unset judge options default to "
              f"`{JUDGE_DEFAULTS['judge_model']}` with num_samples={JUDGE_DEFAULTS['num_samples']}; "
              "always set them explicitly.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
