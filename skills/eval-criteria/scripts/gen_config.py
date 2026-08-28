#!/usr/bin/env python3
"""Write an ADK eval config, with the choices that are easy to get wrong made
explicitly.

Three tiers:

* ``pr`` - deterministic only. No model calls, runs in seconds, safe to gate
  every change on. Trajectory at 1.0, response matching disabled.
* ``nightly`` - adds the judge metrics: groundedness, and rubric-scored response
  and tool-use quality. Costs model calls per invocation per sample.
* ``custom`` - exactly the metrics you name.

Whatever the tier, the generated config always names `judge_model` and
`num_samples` rather than letting them default, because the library default is
a deprecated model sampled five times.

    gen_config.py --tier pr --out tests/eval/eval_config.json
    gen_config.py --tier custom --metrics "tool_trajectory_avg_score=1.0:IN_ORDER,hallucinations_v1=0.8"
    gen_config.py --tier nightly --rubrics-file rubrics.json --judge-model gemini-flash-latest

Exit codes: 0 written (or printed), 1 the request is contradictory, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.env import default_judge_model, detect_backend  # noqa: E402
from adk_skills.eval_config import LoadedConfig, validate_config  # noqa: E402
from adk_skills.evalset import snake_to_camel  # noqa: E402
from adk_skills.metrics import (  # noqa: E402
    MATCH_TYPES, METRIC_CATALOG, cost_tier, incompatible_with_user_sim, suggest,
)
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.templating import WritePlan, dumps_json  # noqa: E402

PR_TIER = {
    "tool_trajectory_avg_score": {"threshold": 1.0, "match_type": "IN_ORDER"},
    "response_match_score": 0.0,
}
NIGHTLY_EXTRA = ("hallucinations_v1", "rubric_based_final_response_quality_v1")


def parse_metric_spec(text: str) -> tuple[str, float, str | None]:
    """`name=threshold[:MATCH_TYPE]`."""
    name, _, rest = text.partition("=")
    name = name.strip()
    if not rest:
        raise ValueError(f"{text!r}: expected name=threshold")
    threshold_text, _, match_type = rest.partition(":")
    try:
        threshold = float(threshold_text)
    except ValueError as exc:
        raise ValueError(f"{text!r}: threshold must be a number") from exc
    return name, threshold, (match_type.strip().upper() or None)


def build_criteria(
    tier: str,
    specs: list[tuple[str, float, str | None]],
    *,
    judge_model: str,
    num_samples: int,
    rubrics: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    criteria: dict[str, Any] = {}

    if tier in ("pr", "nightly"):
        criteria.update({k: (dict(v) if isinstance(v, dict) else v) for k, v in PR_TIER.items()})
    if tier == "nightly":
        for name in NIGHTLY_EXTRA:
            criteria[name] = {"threshold": METRIC_CATALOG[name].default_threshold}
    for name, threshold, match_type in specs:
        spec = METRIC_CATALOG.get(name)
        entry: Any = {"threshold": threshold}
        if match_type:
            entry["match_type"] = match_type
        elif spec is None or not spec.extra_fields:
            entry = threshold
        criteria[name] = entry

    # Fill in what each metric needs, so nothing silently falls back.
    for name, value in list(criteria.items()):
        spec = METRIC_CATALOG.get(name)
        if spec is None or not spec.needs_judge:
            continue
        entry = value if isinstance(value, dict) else {"threshold": float(value)}
        entry.setdefault("judge_model_options", {"judge_model": judge_model, "num_samples": num_samples})
        if spec.requires_rubrics:
            entry.setdefault("rubrics", rubrics.get(name, rubrics.get("_default", [])))
        if name == "hallucinations_v1":
            entry.setdefault("evaluate_intermediate_nl_responses", True)
        criteria[name] = entry
    return criteria


#: Maps whose keys are names, not field aliases: a metric is registered under
#: `tool_trajectory_avg_score`, so camelCasing that key would break the lookup.
_NAME_KEYED = ("criteria", "custom_metrics", "customMetrics")


def to_camel(value: Any, *, keys_are_names: bool = False) -> Any:
    """Rename snake_case *fields* to their camelCase aliases, leaving the keys
    that are identifiers (metric names, rubric ids) exactly as they are."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            new_key = key if keys_are_names else (snake_to_camel(key) if "_" in key else key)
            out[new_key] = to_camel(item, keys_are_names=key in _NAME_KEYED)
        return out
    if isinstance(value, list):
        return [to_camel(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier", choices=("pr", "nightly", "custom"), default="pr")
    parser.add_argument("--metrics", default="", help="name=threshold[:MATCH_TYPE], comma separated")
    parser.add_argument("--judge-model", help="defaults to MODEL_NAME from the agent's .env, else gemini-flash-latest")
    parser.add_argument("--num-samples", type=int, default=3,
                        help="judge samples per invocation; 3 gives a majority vote (default: 3)")
    parser.add_argument("--rubrics-file", help="JSON: a list of rubrics, or a map of metric name to rubrics")
    parser.add_argument("--agent-dir", help="agent, used for the backend and the default judge model")
    parser.add_argument("--user-simulation", action="store_true",
                        help="add an llm_backed user simulator and drop reference-based metrics")
    parser.add_argument("--style", choices=("snake", "camel"), default="snake")
    parser.add_argument("--out", help="file to write; prints to stdout when omitted")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    parser.add_argument("--dry-run", action="store_true", help="show the write plan and stop")
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    args = parser.parse_args(argv)

    try:
        specs = [parse_metric_spec(item) for item in args.metrics.split(",") if item.strip()]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    for name, _, match_type in specs:
        if name not in METRIC_CATALOG:
            hint = suggest(name)
            print(f"error: unknown metric {name!r}" + (f"; did you mean {hint!r}?" if hint else ""),
                  file=sys.stderr)
            return EXIT_USAGE
        if match_type and match_type not in MATCH_TYPES:
            print(f"error: match type {match_type!r} must be one of {', '.join(MATCH_TYPES)}",
                  file=sys.stderr)
            return EXIT_USAGE
    if args.tier == "custom" and not specs:
        print("error: --tier custom needs --metrics", file=sys.stderr)
        return EXIT_USAGE

    rubrics: dict[str, list[dict[str, Any]]] = {}
    if args.rubrics_file:
        try:
            loaded = json.loads(Path(args.rubrics_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: could not read rubrics: {exc}", file=sys.stderr)
            return EXIT_USAGE
        rubrics = {"_default": loaded} if isinstance(loaded, list) else loaded

    backend = detect_backend(args.agent_dir) if args.agent_dir else None
    judge_model = args.judge_model or default_judge_model(backend)

    criteria = build_criteria(
        args.tier, specs, judge_model=judge_model, num_samples=args.num_samples, rubrics=rubrics
    )

    document: dict[str, Any] = {"criteria": criteria}
    if args.user_simulation:
        clashing = incompatible_with_user_sim(criteria)
        for name in clashing:
            criteria.pop(name, None)
        document["user_simulator_config"] = {
            "type": "llm_backed",
            "model": judge_model,
            "max_allowed_invocations": 8,
        }
        if not criteria:
            print("error: user simulation removed every metric; pick simulation-compatible ones "
                  "(rubric_based_*, hallucinations_v1, safety_v1, multi_turn_*)", file=sys.stderr)
            return EXIT_FINDINGS

    # `styled` is what gets written and reported; `document` stays snake_case so
    # the validator (which normalizes anyway) reads the same thing either way.
    styled = to_camel(document) if args.style == "camel" else document
    rendered = dumps_json(styled)

    # Validate what we are about to write, so a generated config is never the
    # thing that breaks a run.
    check = GapReport(subject=args.out or "(stdout)", skill="eval-criteria")
    validate_config(
        LoadedConfig(path=args.out or "(stdout)", doc=document, raw=document),
        report=check, backend=backend, agent_dir=args.agent_dir,
    )

    plan = WritePlan()
    if args.out:
        plan.add(args.out, rendered, force=args.force)

    if args.json:
        print(json.dumps({
            "config": styled,
            "cost_tier": cost_tier(criteria),
            "judge_model": judge_model,
            "write_plan": plan.to_dict(),
            "validation": check.to_dict(),
        }, indent=2))
    else:
        if args.out:
            print(plan.to_md())
            print()
        print(rendered if not args.out or args.dry_run else "")
        print(f"Cost tier: {cost_tier(criteria)}")
        if check.findings:
            print()
            print(check.to_md())

    if args.out and not args.dry_run:
        written = plan.apply()
        if not args.json:
            for path in written:
                print(f"wrote {path}")
            if not written:
                print("nothing written (the file exists; pass --force to replace it)")

    return EXIT_FINDINGS if check.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
