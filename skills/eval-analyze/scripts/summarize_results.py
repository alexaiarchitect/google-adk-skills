#!/usr/bin/env python3
"""Explain an eval result: what failed, where it diverged, and what that means.

For each failing case it reports:

* the **quadrant** - trajectory score against response score. The dangerous
  combination is a good answer over a bad trajectory: the agent skipped work and
  said the right thing anyway, and every text-only metric passes it.
* the **divergence point** - the earliest call where actual and expected part
  company. Everything after it is downstream noise, so that is where a fix
  belongs.
* **rubric verdicts**, by id, so a judge failure names the property that failed.
* **NOT_EVALUATED** separately from failure, because "the judge was unreachable"
  and "the agent regressed" need different fixes.

    summarize_results.py <agent-dir | result.json | --dir eval_history>
                         [--latest] [--eval-id ID] [--format json|md]

Exit codes: 0 all passed, 1 failures, 3 nothing failed but something was never
scored, 2 nothing to read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import (  # noqa: E402
    EXIT_FINDINGS, EXIT_INCONCLUSIVE, EXIT_OK, EXIT_USAGE,
)
from adk_skills.results import (  # noqa: E402
    CaseResult, RunResult, find_result_files, latest_per_evalset, parse_result,
)
from adk_skills.trajectory import cascade_suspect, diff, quadrant

TRAJECTORY_METRIC = "tool_trajectory_avg_score"
RESPONSE_METRICS = ("response_match_score", "final_response_match_v2", "response_evaluation_score")

INFERENCE_FAILED_CAUSES = (
    "the model could not be reached: an invalid or missing API key, no Application "
    "Default Credentials, or a project without the Vertex AI API enabled",
    "the model id does not exist or is not available in this project or region",
    "a tool raised instead of returning an error, which ends the invocation",
    "the agent module failed to import (check with /adk:agent-review's load_agent.py)",
)

NOT_EVALUATED_CAUSES = (
    "the judge model could not be reached (credentials, quota, or a Vertex-only "
    "metric without a project)",
    "no reference response, for a metric that compares against one",
    "an empty rubric list: criterion-level rubrics plus type-matching per-case rubrics",
)


def analyze_case(case: CaseResult) -> dict:
    trajectory = case.metric(TRAJECTORY_METRIC)
    response = next((case.metric(name) for name in RESPONSE_METRICS if case.metric(name)), None)

    entry: dict = {
        "eval_id": case.eval_id,
        "status": case.status,
        "metrics": [m.to_dict() for m in case.metrics],
        "failing_metrics": [m.name for m in case.failing_metrics()],
        "unevaluated_metrics": [m.name for m in case.unevaluated_metrics()],
        # A failed case with no metric results at all did not score badly: the run
        # never got far enough to score. Different problem, different fix.
        "inference_failed": case.status == "FAILED" and not case.metrics,
    }

    if trajectory and response:
        entry["quadrant"] = quadrant(
            trajectory.score, response.score,
            trajectory_threshold=trajectory.threshold or 1.0,
            response_threshold=response.threshold or 0.8,
        )

    divergences = []
    for invocation in case.invocations:
        if not invocation.expected_tools:
            continue
        result = diff(invocation.expected_tools, invocation.actual_tools)
        if result.match:
            continue
        payload = {"turn": invocation.index + 1, **result.to_dict()}
        cascade = cascade_suspect(result)
        if cascade:
            payload["cascade"] = cascade.to_dict()
        divergences.append(payload)
    if divergences:
        entry["divergences"] = divergences

    rubrics = []
    for metric in case.metrics:
        for score in metric.rubric_scores:
            rubrics.append({
                "metric": metric.name, "rubric_id": score.get("rubric_id"),
                "score": score.get("score"), "rationale": score.get("rationale"),
            })
    if rubrics:
        entry["rubric_scores"] = rubrics
    return entry


def render(run: RunResult, cases: list[dict], *, filtered: bool = False) -> str:
    lines = [f"# {Path(run.path).name}", ""]
    lines.append(f"`{run.eval_set_id}`: **{run.passed} passed, {run.failed} failed, "
                 f"{run.not_evaluated} never scored** of {len(run.cases)} case(s).")
    if filtered:
        lines.append("")
        lines.append(f"Showing {len(cases)} of them.")
    lines.append("")

    for entry in cases:
        marker = {"PASSED": "PASS", "FAILED": "FAIL", "NOT_EVALUATED": "SKIP"}.get(entry["status"], "?")
        lines.append(f"## [{marker}] `{entry['eval_id']}`")
        lines.append("")
        for metric in entry["metrics"]:
            score = "-" if metric["score"] is None else f"{metric['score']:.3f}"
            threshold = "-" if metric["threshold"] is None else f"{metric['threshold']:g}"
            lines.append(f"- `{metric['name']}` {score} vs {threshold} -> {metric['status']}")
            for rubric in metric.get("failed_rubrics") or []:
                lines.append(f"  - rubric not met: `{rubric}`")

        if entry.get("quadrant"):
            q = entry["quadrant"]
            if q.get("quadrant"):
                lines.append("")
                lines.append(f"**{q['quadrant']}: {q['label']}** - {q['reading']}.")
                lines.append(f"What to do: {q['action']}.")

        for divergence in entry.get("divergences", []):
            lines.append("")
            lines.append(f"**Turn {divergence['turn']}**: {divergence['explanation']}")
            lines.append(f"- expected: {' -> '.join(divergence['expected']) or '(none)'}")
            lines.append(f"- actual:   {' -> '.join(divergence['actual']) or '(none)'}")
            for mismatch in divergence.get("arg_mismatches", []):
                lines.append(f"- argument mismatch in `{mismatch['tool']}`: "
                             f"expected {mismatch['expected']}, got {mismatch['actual']}")
            if divergence.get("cascade"):
                lines.append(f"- {divergence['cascade']['detail']}")
                lines.append(f"  Fix: {divergence['cascade']['fix']}")

        if entry.get("inference_failed"):
            lines.append("")
            lines.append("**The run failed before any metric could score it** - there are no "
                         "metric results at all, so this is not a score regression. Usual "
                         "causes:")
            for cause in INFERENCE_FAILED_CAUSES:
                lines.append(f"- {cause}")

        if entry["status"] == "NOT_EVALUATED" or entry["unevaluated_metrics"]:
            lines.append("")
            lines.append("**Never scored.** This is a configuration problem, not a regression. "
                         "Usual causes:")
            for cause in NOT_EVALUATED_CAUSES:
                lines.append(f"- {cause}")
        lines.append("")

    failing = [c for c in cases if c["status"] == "FAILED"]
    if failing and all(c.get("inference_failed") for c in failing):
        lines.append("## Next")
        lines.append("")
        lines.append("- Nothing was scored, so there is nothing to diagnose about the agent's "
                     "behaviour yet. Fix the environment first: "
                     "`check_env.py <agent-dir> --config <config>`.")
        return "\n".join(lines)
    if failing:
        lines.append("## Next")
        lines.append("")
        lines.append("- Fix the **first** divergence in each case; later calls follow from it.")
        lines.append("- Triage which layer to fix with `/adk:agent-debug`.")
        lines.append("- Before trusting a fix, re-run several times: "
                     "`/adk:eval-run --runs 5`, then `pass_at_k.py`.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent dir, project, or a result file")
    parser.add_argument("--dir", help="an eval_history directory")
    parser.add_argument("--latest", action="store_true", help="only the newest run per eval set")
    parser.add_argument("--eval-id", help="only this case")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    args = parser.parse_args(argv)

    source = Path(args.dir) if args.dir else Path(args.path)
    if not source.exists():
        print(f"error: {source} does not exist", file=sys.stderr)
        return EXIT_USAGE

    files = find_result_files(source)
    if not files and source.is_dir():
        layout = detect_layout(source)
        for history in layout.eval_history_dirs:
            files.extend(find_result_files(history))
    if not files:
        print(f"error: no eval results under {source}. Run some with /adk:eval-run.",
              file=sys.stderr)
        return EXIT_USAGE

    if args.latest:
        files = list(latest_per_evalset(files).values())

    runs = [parse_result(path) for path in sorted(set(files))]
    runs = [run for run in runs if run.ok]
    if not runs:
        print("error: no readable result files", file=sys.stderr)
        return EXIT_USAGE

    payloads = []
    any_failed = any_skipped = False
    for run in runs:
        cases = [
            analyze_case(case) for case in run.cases
            if not args.eval_id or case.eval_id == args.eval_id
        ]
        if not cases:
            continue
        any_failed = any_failed or any(c["status"] == "FAILED" for c in cases)
        any_skipped = any_skipped or any(c["status"] == "NOT_EVALUATED" for c in cases)
        payloads.append({"run": run.summary(), "cases": cases, "_run": run})

    if not payloads:
        print(f"error: no case matched {args.eval_id!r}", file=sys.stderr)
        return EXIT_USAGE

    if args.format == "json":
        print(json.dumps(
            [{"run": p["run"], "cases": p["cases"]} for p in payloads], indent=2
        ))
    else:
        for index, payload in enumerate(payloads):
            if index:
                print()
            print(render(payload["_run"], payload["cases"], filtered=bool(args.eval_id)))

    if any_failed:
        return EXIT_FINDINGS
    return EXIT_INCONCLUSIVE if any_skipped else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
