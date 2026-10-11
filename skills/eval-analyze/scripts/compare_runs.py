#!/usr/bin/env python3
"""Compare two eval runs: did the change help, hurt, or just cost less?

Eval-driven development is a loop: write the case first, take a baseline run,
change the agent, run again, and compare. This is the compare step. It puts the
two runs side by side:

* **pass rate** (passed / cases) and the cases that **flipped** - passed to
  failed is a regression whatever the averages say, failed to passed is a fix;
* **cost and speed** from the efficiency metrics ADK 2.10+ reports on every
  run: mean prompt, cached and output tokens per invocation, the uncached part
  of the prompt (prompt minus cached: what is billed at the full input rate),
  the cache-hit ratio (`cached_tokens / prompt_tokens`), mean seconds, tool
  calls and model calls per invocation, each with its delta;
* a one-line **verdict**.

A run from ADK 2.9 has no efficiency metrics, and a model may not report cached
tokens; those figures read "n/a", never 0, and the pass/fail comparison still
stands.

Each side is a result file, or a directory (an `eval_history`, an agent dir or a
project) from which `--latest` takes the newest run of each eval set; `--evalset`
keeps one eval set. Cases are matched by `eval_id`.

    compare_runs.py <baseline> <candidate> [--latest] [--evalset ID] [--json]

Exit codes: 0 no regression, 1 the pass rate dropped or a case flipped to
failed, 2 nothing to compare.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.results import (  # noqa: E402
    CaseResult, RunResult, find_result_files, latest_per_evalset, parse_result,
    result_identity,
)

#: Figures compared per invocation, with the efficiency metric each comes from.
#: Token figures come from `token_usage_v1`'s breakdown; lower is better for all.
CALL_METRICS = {
    "duration_s": "invocation_duration_v1",
    "tool_calls": "tool_call_count_v1",
    "model_calls": "inference_call_count_v1",
}
TOKEN_FIGURES = {
    "prompt_tokens": "prompt_tokens",
    "cached_tokens": "cached_tokens",
    "output_tokens": "output_tokens",
}
LABELS = {
    "pass_rate": "pass rate",
    "prompt_tokens": "prompt tokens / invocation",
    "cached_tokens": "cached tokens / invocation",
    "uncached_prompt_tokens": "uncached prompt tokens / invocation",
    "output_tokens": "output tokens / invocation",
    "cache_hit_ratio": "cache-hit ratio",
    "duration_s": "seconds / invocation",
    "tool_calls": "tool calls / invocation",
    "model_calls": "model calls / invocation",
}


class UsageError(Exception):
    pass


def load_side(source: Path, *, latest: bool, evalset: str | None) -> list[RunResult]:
    """The runs one side of the comparison stands for."""
    if not source.exists():
        raise UsageError(f"{source} does not exist")
    files = find_result_files(source)
    if not files and source.is_dir():
        for history in detect_layout(source).eval_history_dirs:
            files.extend(find_result_files(history))
    if evalset:
        files = [f for f in files if result_identity(f).get("eval_set_id") == evalset]
    if not files:
        scope = f" for eval set {evalset!r}" if evalset else ""
        raise UsageError(f"no eval results{scope} under {source}")
    if latest:
        files = list(latest_per_evalset(files).values())
    else:
        seen: dict[str, int] = {}
        for path in files:
            key = result_identity(path).get("eval_set_id") or path
            seen[key] = seen.get(key, 0) + 1
        repeated = sorted(key for key, count in seen.items() if count > 1)
        if repeated:
            raise UsageError(
                f"{source} holds several runs of {', '.join(repeated)}; pass --latest, "
                "or name the two result files"
            )
    runs = [parse_result(path) for path in sorted(set(files))]
    runs = [run for run in runs if run.ok]
    if not runs:
        raise UsageError(f"no readable result files under {source}")
    return runs


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _case_key(case: CaseResult, duplicated: set[str]) -> str:
    return f"{case.eval_set_id}/{case.eval_id}" if case.eval_id in duplicated else case.eval_id


def side_stats(runs: list[RunResult]) -> dict[str, Any]:
    """Pass rate and mean efficiency figures over every case in the runs."""
    cases = [case for run in runs for case in run.cases]
    ids = [case.eval_id for case in cases]
    duplicated = {i for i in ids if ids.count(i) > 1}
    statuses = {_case_key(case, duplicated): case.status for case in cases}

    tokens: dict[str, list[float]] = {name: [] for name in TOKEN_FIGURES}
    calls: dict[str, list[float]] = {name: [] for name in CALL_METRICS}
    uncached: list[float] = []
    cached_sum = prompt_sum = 0.0
    ratio_cases = 0
    for case in cases:
        usage = case.token_usage()
        for name, key in TOKEN_FIGURES.items():
            if key in usage:
                tokens[name].append(usage[key])
        if "cached_tokens" in usage and "prompt_tokens" in usage:
            uncached.append(usage["prompt_tokens"] - usage["cached_tokens"])
        if "cached_tokens" in usage and usage.get("prompt_tokens"):
            cached_sum += usage["cached_tokens"]
            prompt_sum += usage["prompt_tokens"]
            ratio_cases += 1
        for name, metric_name in CALL_METRICS.items():
            metric = case.metric(metric_name)
            if metric is not None and isinstance(metric.score, (int, float)):
                calls[name].append(float(metric.score))

    passed = sum(1 for case in cases if case.passed)
    figures: dict[str, Any] = {
        "pass_rate": round(passed / len(cases), 4) if cases else None,
        **{name: _mean(values) for name, values in tokens.items()},
        "uncached_prompt_tokens": _mean(uncached),
        "cache_hit_ratio": round(cached_sum / prompt_sum, 4) if ratio_cases and prompt_sum else None,
        **{name: _mean(values) for name, values in calls.items()},
    }
    return {
        "runs": [Path(run.path).name for run in runs],
        "eval_sets": sorted({run.eval_set_id for run in runs}),
        "cases": len(cases),
        "passed": passed,
        "failed": sum(1 for case in cases if case.status == "FAILED"),
        "not_evaluated": sum(1 for case in cases if case.not_evaluated),
        "figures": figures,
        "statuses": statuses,
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    deltas: dict[str, dict[str, Any]] = {}
    for name in LABELS:
        before = baseline["figures"].get(name)
        after = candidate["figures"].get(name)
        delta = round(after - before, 4) if before is not None and after is not None else None
        relative = (round(delta / before, 4)
                    if delta is not None and before not in (None, 0) else None)
        deltas[name] = {"baseline": before, "candidate": after, "delta": delta,
                        "relative": relative}

    before_status, after_status = baseline["statuses"], candidate["statuses"]
    flips: list[dict[str, str]] = []
    for key in sorted(set(before_status) & set(after_status)):
        if before_status[key] != after_status[key]:
            change = ("regressed" if after_status[key] == "FAILED"
                      else "fixed" if after_status[key] == "PASSED"
                      else "changed")
            flips.append({"eval_id": key, "baseline": before_status[key],
                          "candidate": after_status[key], "change": change})
    added = sorted(set(after_status) - set(before_status))
    removed = sorted(set(before_status) - set(after_status))

    rate = deltas["pass_rate"]
    rate_dropped = rate["delta"] is not None and rate["delta"] < 0
    regressed = [f for f in flips if f["change"] == "regressed"]
    return {
        "deltas": deltas,
        "flips": flips,
        "added_cases": added,
        "removed_cases": removed,
        "pass_rate_dropped": rate_dropped,
        "regressed_cases": [f["eval_id"] for f in regressed],
        "regression": rate_dropped or bool(regressed),
        "verdict": verdict(deltas, flips, rate_dropped, regressed),
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def verdict(deltas: dict[str, dict[str, Any]], flips: list[dict[str, str]],
            rate_dropped: bool, regressed: list[dict[str, str]]) -> str:
    rate = deltas["pass_rate"]
    if rate_dropped or regressed:
        parts = []
        if rate_dropped:
            parts.append(f"pass rate fell from {_pct(rate['baseline'])} to {_pct(rate['candidate'])}")
        if regressed:
            parts.append(f"{len(regressed)} case(s) flipped to failed: "
                         + ", ".join(f["eval_id"] for f in regressed))
        return "REGRESSION: " + "; ".join(parts)
    fixed = [f for f in flips if f["change"] == "fixed"]
    lead = (f"IMPROVED: pass rate {_pct(rate['baseline'])} -> {_pct(rate['candidate'])}"
            if rate["delta"] and rate["delta"] > 0
            else f"NO REGRESSION: pass rate {_pct(rate['candidate'])}")
    if fixed:
        lead += f", {len(fixed)} case(s) fixed"
    costs = []
    token_figure = ("uncached_prompt_tokens"
                    if deltas["uncached_prompt_tokens"]["relative"] is not None
                    else "prompt_tokens")
    for name in (token_figure, "duration_s"):
        relative = deltas[name]["relative"]
        if relative is not None and relative != 0:
            costs.append(f"{LABELS[name].split(' /')[0]} {relative:+.1%}")
    ratio = deltas["cache_hit_ratio"]
    if ratio["candidate"] is not None and ratio["candidate"] != ratio["baseline"]:
        costs.append(f"cache-hit ratio {_pct(ratio['baseline'])} -> {_pct(ratio['candidate'])}")
    return lead + (f"; {', '.join(costs)}" if costs else "")


def _fmt(name: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    if name in ("pass_rate", "cache_hit_ratio"):
        return f"{value:.1%}"
    return f"{value:g}"


def render(baseline: dict[str, Any], candidate: dict[str, Any], result: dict[str, Any]) -> str:
    lines = ["# Run comparison", ""]
    lines.append(f"- baseline: {', '.join(baseline['runs'])} ({baseline['passed']}/"
                 f"{baseline['cases']} passed)")
    lines.append(f"- candidate: {', '.join(candidate['runs'])} ({candidate['passed']}/"
                 f"{candidate['cases']} passed)")
    if baseline["eval_sets"] != candidate["eval_sets"]:
        lines.append(f"- note: the eval sets differ ({', '.join(baseline['eval_sets'])} vs "
                     f"{', '.join(candidate['eval_sets'])}); cases are matched by eval_id")
    lines += ["", f"**{result['verdict']}**", "",
              "| figure | baseline | candidate | delta |", "|---|---|---|---|"]
    for name, row in result["deltas"].items():
        if row["delta"] is None:
            delta = "n/a"
        elif name in ("pass_rate", "cache_hit_ratio"):
            delta = f"{row['delta'] * 100:+.1f} pts"
        else:
            delta = f"{row['delta']:+g}" + (
                f" ({row['relative']:+.1%})" if row["relative"] is not None else "")
        lines.append(f"| {LABELS[name]} | {_fmt(name, row['baseline'])} | "
                     f"{_fmt(name, row['candidate'])} | {delta} |")
    lines.append("")
    lines.append("Lower is better for every efficiency figure except the cache-hit ratio. "
                 "\"n/a\" means the run did not report it (ADK 2.9 has no efficiency "
                 "metrics), not zero.")
    if result["flips"]:
        lines += ["", "## Cases that changed", ""]
        for flip in result["flips"]:
            lines.append(f"- `{flip['eval_id']}`: {flip['baseline']} -> {flip['candidate']} "
                         f"({flip['change']})")
    if result["added_cases"] or result["removed_cases"]:
        lines += ["", "## Cases in only one run", ""]
        for key in result["added_cases"]:
            lines.append(f"- `{key}`: new in the candidate")
        for key in result["removed_cases"]:
            lines.append(f"- `{key}`: missing from the candidate")
    if result["regression"]:
        lines += ["", "## Next", "",
                  "- Read each regressed case with `summarize_results.py --eval-id <id>`; "
                  "fix the first divergence.",
                  "- One run is one sample: re-run both sides a few times "
                  "(`/adk:eval-run --runs 5`, then `pass_at_k.py`) before trusting a "
                  "small difference."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("baseline", help="result file, eval_history dir, agent dir or project")
    parser.add_argument("candidate", help="result file, eval_history dir, agent dir or project")
    parser.add_argument("--latest", action="store_true",
                        help="for a directory, use the newest run of each eval set")
    parser.add_argument("--evalset", help="compare only this eval set id")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    args = parser.parse_args(argv)

    try:
        baseline = side_stats(load_side(Path(args.baseline), latest=args.latest,
                                        evalset=args.evalset))
        candidate = side_stats(load_side(Path(args.candidate), latest=args.latest,
                                         evalset=args.evalset))
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not baseline["cases"] or not candidate["cases"]:
        print("error: one of the runs has no cases to compare", file=sys.stderr)
        return EXIT_USAGE

    result = compare(baseline, candidate)
    if args.format == "json":
        print(json.dumps({
            "baseline": {k: v for k, v in baseline.items() if k != "statuses"},
            "candidate": {k: v for k, v in candidate.items() if k != "statuses"},
            **result,
        }, indent=2))
    else:
        print(render(baseline, candidate, result))
    return EXIT_FINDINGS if result["regression"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
