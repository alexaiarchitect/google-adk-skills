#!/usr/bin/env python3
"""Write conversation-scenario eval cases, for simulated users.

A scenario case replaces fixed turns with a **plan** that an LLM plays as the
user. That buys coverage a scripted case cannot get: different phrasings, follow
ups you did not anticipate, and the ordinary human habit of not repeating
yourself.

It also costs you the reference. With no fixed expected answer, reference-based
metrics (`tool_trajectory_avg_score`, `response_match_score`,
`final_response_match_v2`, `response_evaluation_score`) cannot score these cases
at all. Pair them with rubric metrics, `hallucinations_v1`, `safety_v1`, or the
`multi_turn_*` metrics.

    gen_scenarios.py <agent-dir> --spec scenarios.json --evalset F | --new ID

Spec entries::

    [{"eval_id": "memory_simulated_repeat_claims",
      "starting_prompt": "I need to claim some expenses from last week's trip.",
      "conversation_plan": "Say you work in engineering. Ask about a 900 dollar flight, then a 40 dollar meal, without repeating the cost centre. Confirm both, then stop.",
      "persona": "NOVICE"}]

Exit codes: 0 written, 1 findings or an id clash, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.evalset import (  # noqa: E402
    LoadedEvalSet, load_evalset, sanitize_eval_set_id, stats, validate_evalset,
)
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.metrics import incompatible_with_user_sim  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.templating import WritePlan, dumps_json  # noqa: E402

PREBUILT_PERSONAS = ("EXPERT", "NOVICE", "EVALUATOR")


def build_case(entry: dict[str, Any], *, app_name: str, user_id: str) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "starting_prompt": entry["starting_prompt"],
        "conversation_plan": entry["conversation_plan"],
    }
    if entry.get("persona"):
        scenario["user_persona"] = entry["persona"]
    case: dict[str, Any] = {
        "eval_id": entry["eval_id"],
        "conversation_scenario": scenario,
        "session_input": {
            "app_name": app_name, "user_id": user_id, "state": entry.get("state") or {},
        },
    }
    if entry.get("rubrics"):
        case["rubrics"] = entry["rubrics"]
    return case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent directory or project root")
    parser.add_argument("--spec", required=True, help="JSON file, or inline JSON")
    parser.add_argument("--evalset", help="evalset to add to")
    parser.add_argument("--new", help="create an evalset with this id")
    parser.add_argument("--app-name")
    parser.add_argument("--user-id", default="evaluator")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE
    if not args.evalset and not args.new:
        print("error: pass --evalset or --new <id>", file=sys.stderr)
        return EXIT_USAGE

    spec_text = args.spec
    # Inline JSON can be longer than a path may be; on Python < 3.14 asking
    # whether it exists raises OSError (ENAMETOOLONG) instead of saying no.
    if not spec_text.lstrip().startswith(("[", "{")) and Path(args.spec).exists():
        spec_text = Path(args.spec).read_text(encoding="utf-8")
    try:
        entries = json.loads(spec_text)
    except json.JSONDecodeError as exc:
        print(f"error: --spec is not valid JSON: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if isinstance(entries, dict):
        entries = [entries]
    for entry in entries:
        for required in ("eval_id", "starting_prompt", "conversation_plan"):
            if not entry.get(required):
                print(f"error: every scenario needs {required!r}", file=sys.stderr)
                return EXIT_USAGE
        persona = entry.get("persona")
        if isinstance(persona, str) and persona not in PREBUILT_PERSONAS:
            print(f"note: persona {persona!r} is not one of {', '.join(PREBUILT_PERSONAS)}; "
                  "it must be registered, or given as an object", file=sys.stderr)

    layout = detect_layout(target)
    app_name = args.app_name or layout.expected_app_name() or "app"

    if args.evalset:
        evalset_path = Path(args.evalset)
    elif layout.evalsets:
        evalset_path = Path(layout.evalsets[0]).parent / f"{sanitize_eval_set_id(args.new)}.evalset.json"
    else:
        evalset_path = (Path(layout.project_root or target) / "tests" / "eval" / "evalsets"
                        / f"{sanitize_eval_set_id(args.new)}.evalset.json")

    if evalset_path.exists():
        loaded = load_evalset(evalset_path)
        if loaded.fmt != "evalset" or not isinstance(loaded.doc, dict):
            print(f"error: {evalset_path} is not an EvalSet", file=sys.stderr)
            return EXIT_USAGE
        document = loaded.doc
    else:
        stem = evalset_path.name.replace(".evalset.json", "")
        document = {
            "eval_set_id": sanitize_eval_set_id(args.new or stem),
            "name": stem.replace("_", " ").title(),
            "description": "Simulated conversations. Reference-based metrics cannot score these.",
            "eval_cases": [],
        }

    existing = {case.get("eval_id") for case in document.get("eval_cases", [])}
    clashing = [e["eval_id"] for e in entries if e["eval_id"] in existing]
    if clashing:
        print(f"error: these ids already exist: {', '.join(clashing)}", file=sys.stderr)
        return EXIT_FINDINGS

    added = [build_case(e, app_name=app_name, user_id=args.user_id) for e in entries]
    document.setdefault("eval_cases", []).extend(added)

    report = GapReport(subject=str(evalset_path), skill="eval-simulation")
    validate_evalset(
        LoadedEvalSet(str(evalset_path), document, document, "evalset"),
        report=report, expected_app_name=app_name, reference_metrics=False,
    )

    # The config has to be able to score what we just wrote.
    for candidate in layout.configs:
        if Path(candidate).name in ("test_config.json", "eval_config.json"):
            loaded_config = load_config(candidate)
            clashing_metrics = incompatible_with_user_sim(loaded_config.metric_names())
            if clashing_metrics:
                report.add(
                    "CFG030", "error", "config",
                    f"{Path(candidate).name} uses reference-based metric(s) "
                    f"({', '.join(clashing_metrics)}) that cannot score a simulated conversation",
                    path=candidate,
                    fix="add a simulation config with rubric-based, hallucinations_v1, "
                        "safety_v1 or multi_turn_* metrics, and point the run at it",
                    source="adk-docs: docs/evaluate/criteria.md",
                )
            if not loaded_config.uses_user_simulation():
                report.add(
                    "CFG067", "warn", "config",
                    f"{Path(candidate).name} has no `user_simulator_config`, so these cases "
                    "have nothing to play the user",
                    path=candidate,
                    fix="generate one with gen_sim_config.py --kind user",
                )
            break

    plan = WritePlan()
    plan.add(evalset_path, dumps_json(document), force=True,
             reason=f"adds {len(added)} scenario case(s); existing cases untouched")
    written = [] if args.dry_run else plan.apply()

    summary = {
        "evalset": str(evalset_path),
        "added": [c["eval_id"] for c in added],
        "total_cases": stats(document)["cases"],
        "scenario_cases": stats(document)["scenario_cases"],
    }

    if args.json:
        print(json.dumps({"summary": summary, "write_plan": plan.to_dict(),
                          "written": written, "validation": report.to_dict()}, indent=2))
    else:
        for eval_id in summary["added"]:
            print(f"+ {eval_id}  (simulated)")
        print(f"{evalset_path.name}: {summary['total_cases']} case(s), "
              f"{summary['scenario_cases']} simulated")
        print()
        print(plan.to_md())
        if report.findings:
            print()
            print(report.to_md())
        for path in written:
            print(f"wrote {path}")

    return EXIT_FINDINGS if report.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
