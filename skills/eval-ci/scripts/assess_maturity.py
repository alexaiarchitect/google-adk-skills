#!/usr/bin/env python3
"""Where a project's evaluation practice actually stands, and what unlocks the
next level.

Five levels. Each is defined by something checkable, not by intent:

    L0  no evalset, or one that asserts nothing
    L1  golden traces exist and can be replayed deterministically
    L2  they run automatically, gated, on every change
    L3  reliability is measured across repeated runs
    L4  production behaviour feeds back into the suite

Most projects are between L0 and L1 and believe they are higher, because someone
ran the agent by hand and it looked right. The point of the ladder is that each
step is a small, concrete piece of work.

    assess_maturity.py <repo-root> [--format json|md]

Exit codes: 0 always (this is a report, not a gate).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.evalset import load_evalset, stats  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.metrics import DETERMINISTIC_METRICS  # noqa: E402
from adk_skills.report import EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.results import find_result_files, result_identity  # noqa: E402

LEVELS = {
    0: ("Vibe checks", "Nothing replayable states what the agent should do. "
                       "Every judgement is someone looking at output."),
    1: ("Golden traces", "Cases exist and replay deterministically, so a change "
                         "can be checked rather than inspected."),
    2: ("Automated gates", "The suite runs without anyone remembering to, and a "
                           "failure blocks the change."),
    3: ("Statistical reliability", "Repeated runs give a pass rate, so "
                                   "\"it works\" has a number attached."),
    4: ("Continuous observability", "Production failures become eval cases, so "
                                    "the suite grows from reality."),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(root)

    cases = 0
    categories: dict[str, int] = {}
    for path in layout.evalsets:
        loaded = load_evalset(path)
        # Legacy files are normalized at load, so their cases count too: ADK's
        # AgentEvaluator replays them, which is what L1 asks for.
        if isinstance(loaded.doc, dict) and isinstance(loaded.doc.get("eval_cases"), list):
            summary = stats(loaded.doc)
            cases += summary["cases"]
            for category, count in summary["by_category"].items():
                categories[category] = categories.get(category, 0) + count

    deterministic_config = False
    judged_config = False
    for path in layout.configs:
        loaded = load_config(path)
        if not loaded.ok:
            continue
        metrics = loaded.metric_names()
        if any(m in DETERMINISTIC_METRICS for m in metrics):
            deterministic_config = True
        if loaded.cost() != "free":
            judged_config = True

    workflow_dirs = [root / ".github" / "workflows"]
    workflows: list[Path] = []
    for directory in workflow_dirs:
        if directory.is_dir():
            workflows.extend(sorted(directory.glob("*.y*ml")))
    contents = {w: w.read_text(encoding="utf-8", errors="ignore") for w in workflows}
    eval_workflows = [
        w for w in workflows
        if "adk" in w.name.lower() or "eval" in w.name.lower() or "eval" in contents[w].lower()
    ]
    gated = any(
        marker in contents[w]
        for w in eval_workflows
        for marker in ("run_evals.py", "run_registry.py", "pytest")
    )

    result_files: list[str] = []
    for history in layout.eval_history_dirs:
        result_files.extend(find_result_files(history))
    runs_per_set: dict[str, int] = {}
    for path in result_files:
        eval_set = result_identity(path).get("eval_set_id") or "?"
        runs_per_set[eval_set] = runs_per_set.get(eval_set, 0) + 1
    repeated = max(runs_per_set.values()) if runs_per_set else 0

    has_regression_cases = categories.get("regression", 0) > 0

    # Each level needs the one below it.
    level = 0
    evidence: dict[int, list[str]] = {0: [], 1: [], 2: [], 3: [], 4: []}
    if cases > 0:
        level = 1
        evidence[1].append(f"{cases} eval case(s) across {len(layout.evalsets)} file(s)")
        if deterministic_config:
            evidence[1].append("a deterministic config exists")
    if level >= 1 and eval_workflows and gated:
        level = 2
        evidence[2].append(f"{len(eval_workflows)} eval workflow(s), gated on a real exit code")
    if level >= 2 and repeated >= 3:
        level = 3
        evidence[3].append(f"up to {repeated} runs recorded for one eval set")
    if level >= 3 and has_regression_cases:
        level = 4
        evidence[4].append(f"{categories['regression']} regression case(s) from real failures")

    next_steps: list[str] = []
    if level == 0:
        next_steps = [
            "write the first happy-path case: /adk:eval-create",
            "then one refusal case, so the suite can fail for the right reason",
        ]
    elif level == 1:
        if not deterministic_config:
            next_steps.append("write a deterministic config: /adk:eval-criteria --tier pr")
        next_steps.append("add a PR-tier workflow: /adk:eval-ci --tier pr")
        if eval_workflows and not gated:
            next_steps.append(
                "an eval workflow exists but does not gate: bare `adk eval` always exits 0, "
                "so route it through run_evals.py or pytest"
            )
    elif level == 2:
        next_steps = [
            "run the suite several times and read the reliability: "
            "/adk:eval-run --runs 5, then /adk:eval-analyze",
            "add a nightly tier for judge metrics: /adk:eval-ci --tier nightly",
        ]
    elif level == 3:
        next_steps = [
            "turn production failures into cases: capture the session and add it with "
            "/adk:eval-create",
            "name them `regression_*` so coverage can count them",
            "sample passing cases periodically: calibration_sample.py",
        ]
    else:
        next_steps = [
            "keep the loop running: every production failure becomes a case",
            "revisit thresholds as the agent improves, so the gate stays meaningful",
        ]

    gaps: list[str] = []
    if cases and not categories.get("adversarial"):
        gaps.append("no adversarial case: nothing in the suite tries to make the agent fail")
    if cases and not judged_config:
        gaps.append("no judged config: response quality, groundedness and restraint are untested")
    if repeated in (1, 2):
        gaps.append(f"only {repeated} run(s) recorded, which cannot measure reliability")

    payload = {
        "level": level,
        "level_name": LEVELS[level][0],
        "level_meaning": LEVELS[level][1],
        "evidence": {str(k): v for k, v in evidence.items() if v},
        "facts": {
            "evalsets": len(layout.evalsets),
            "cases": cases,
            "by_category": categories,
            "deterministic_config": deterministic_config,
            "judged_config": judged_config,
            "eval_workflows": [w.name for w in eval_workflows],
            "gated": gated,
            "result_files": len(result_files),
            "max_runs_per_eval_set": repeated,
        },
        "gaps": gaps,
        "next_steps": next_steps,
    }

    if args.format == "json":
        print(json.dumps(payload, indent=2))
        return EXIT_OK

    print(f"# Evaluation maturity: L{level} — {LEVELS[level][0]}")
    print()
    print(LEVELS[level][1])
    print()
    print("| Level | | Reached |")
    print("|---|---|---|")
    for index, (name, _) in LEVELS.items():
        mark = "yes" if index <= level else "not yet"
        print(f"| L{index} | {name} | {mark} |")
    if payload["evidence"]:
        print()
        print("## Evidence")
        print()
        for index, items in sorted(payload["evidence"].items()):
            for item in items:
                print(f"- L{index}: {item}")
    if gaps:
        print()
        print("## Gaps at this level")
        print()
        for gap in gaps:
            print(f"- {gap}")
    print()
    print(f"## To reach L{min(level + 1, 4)}")
    print()
    for step in next_steps:
        print(f"- {step}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
