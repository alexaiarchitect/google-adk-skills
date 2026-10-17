#!/usr/bin/env python3
"""Run ADK evals and produce an exit code that means something.

`adk eval` prints

    Eval Run Summary
    <eval set>:
      Tests passed: 3
      Tests failed: 1

and **returns 0 either way**, counting `NOT_EVALUATED` cases in the failed
column. A CI step that calls it directly therefore passes forever, and cannot
tell "the agent regressed" from "the judge was unreachable".

This wrapper runs the command, finds the result files it wrote, and reports the
three statuses separately:

    0  every case passed
    1  at least one case failed
    2  the environment could not run it
    3  nothing failed, but some case was never evaluated (with --not-evaluated warn)

    run_evals.py <agent-dir> [--evalset F ...] [--config F] [--cases a,b]
                 [--runs N] [--detailed] [--storage gs://...] [--json]
                 [--min-pass-rate P]

`--min-pass-rate P` turns the run into a global-threshold gate: exit 0 when at
least that fraction of all cases scored (passed / every case, NOT_EVALUATED
counted as not passed) passed, 1 below it. Without it any failing case fails
the run. Pair it with `compare_runs.py --max-regressions 0` against the frozen
baseline for the second merge rule.

On ADK main after 2.12.0 (c2ff634, unreleased) bare `adk eval` also exits
non-zero when a case fails; every released version up to 2.12.0 exits 0, so
this wrapper's exit code, read from the result files, stays the one to gate on.

`--runs N` runs the whole set N times and keeps each run's results separate, so
`/adk:eval-analyze` can compute pass@k. That is deliberately not
`AgentEvaluator(num_runs=N)`, which averages scores across runs and hides the
variance.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.adk_subprocess import describe_runner, run_adk_eval  # noqa: E402
from adk_skills.env import (  # noqa: E402
    ADK_REQUIREMENT, detect_backend, resolve_runner, version_is_supported,
)
from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.evalset import load_evalset  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.metrics import METRIC_CATALOG, cost_tier  # noqa: E402
from adk_skills.report import (  # noqa: E402
    EXIT_FINDINGS, EXIT_INCONCLUSIVE, EXIT_OK, EXIT_USAGE,
)


def nearest_config(layout, evalsets: list) -> tuple:
    """The ADK config closest to the evalsets being run, plus the others found.

    `adk eval` only auto-discovers `test_config.json` beside a single evalset; a
    project with both a `tests/eval/eval_config.json` and an agent-level
    `test_config.json` should get the neighbour, not the first in path order.
    """
    candidates = [c for c in layout.configs
                  if Path(c).name in ("test_config.json", "eval_config.json")]
    if not candidates:
        return None, []
    evalset_dirs = [Path(e).resolve().parent for e in evalsets]

    def distance(config: str) -> int:
        cdir = Path(config).resolve().parent
        best = 99
        for edir in evalset_dirs:
            hops = 0
            probe = edir
            while probe != probe.parent:
                if probe == cdir:
                    best = min(best, hops)
                    break
                probe = probe.parent
                hops += 1
        return best

    ordered = sorted(candidates, key=lambda c: (distance(c), c))
    return ordered[0], ordered[1:]


def estimate_judge_calls(config_doc: dict, cases: int, turns: int) -> int:
    """Roughly how many judge calls a run will make, so cost is stated up front."""
    total = 0
    for name, value in (config_doc.get("criteria") or {}).items():
        spec = METRIC_CATALOG.get(name)
        if not spec or not spec.needs_judge:
            continue
        samples = 5
        if isinstance(value, dict):
            options = value.get("judge_model_options") or {}
            samples = options.get("num_samples", 5) if isinstance(options, dict) else 5
        total += max(cases, 1) * max(turns, 1) * samples
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent directory or project root")
    parser.add_argument("--evalset", action="append", default=[], help="repeatable; default: all found")
    parser.add_argument("--config", help="eval config (required by the recipes layout)")
    parser.add_argument("--cases", help="comma-separated eval ids to run")
    parser.add_argument("--runs", type=int, default=1, help="repeat the whole set N times, for pass@k")
    parser.add_argument("--detailed", action="store_true", help="pass --print_detailed_results")
    parser.add_argument("--storage", help="--eval_storage_uri gs://bucket/prefix")
    parser.add_argument("--not-evaluated", choices=("fail", "warn"), default="fail",
                        help="how to treat cases that were never scored (default: fail)")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--dry-run", action="store_true", help="print the commands and stop")
    parser.add_argument("--min-pass-rate", type=float, metavar="P",
                        help="gate on a global pass threshold (0..1, e.g. 0.95) instead of "
                             "requiring every case to pass")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE
    if args.min_pass_rate is not None and not 0.0 <= args.min_pass_rate <= 1.0:
        print("error: --min-pass-rate is a fraction between 0 and 1", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(target)
    if not layout.agent_dir:
        print(f"error: no ADK agent under {target}"
              + (f"; found {len(layout.sibling_agents)} agents, pick one"
                 if layout.sibling_agents else ""), file=sys.stderr)
        return EXIT_USAGE

    evalsets = args.evalset or layout.evalsets
    if not evalsets:
        print(f"error: no evalsets found under {target}. Write some with /adk:eval-create",
              file=sys.stderr)
        return EXIT_USAGE

    # A flat-turn legacy file crashes `adk eval` inside ADK (KeyError 'data').
    for evalset in evalsets:
        loaded_set = load_evalset(evalset)
        if loaded_set.is_flat_legacy:
            print(f"error: {Path(evalset).name} is a flat-turn legacy file that `adk eval` "
                  "cannot read (KeyError 'data'). Migrate it first: "
                  "/adk:eval-create migrate_legacy.py, or run it through the pytest path.",
                  file=sys.stderr)
            return EXIT_USAGE

    config = args.config
    if not config:
        config, alternatives = nearest_config(layout, evalsets)
        if alternatives:
            print(f"note: using {Path(config).name} next to the evalsets; other configs found: "
                  f"{', '.join(Path(a).name for a in alternatives)}", file=sys.stderr)
    # `adk eval` auto-discovers only `test_config.json` beside a single evalset.
    if config and layout.eval_command_needs_explicit_config():
        pass                      # we always pass it explicitly anyway
    elif not config:
        print("note: no eval config found; ADK will use its defaults "
              "(tool_trajectory_avg_score 1.0, response_match_score 0.8)", file=sys.stderr)

    config_doc = {}
    if config and Path(config).exists():
        loaded = load_config(config)
        if loaded.ok and loaded.doc:
            config_doc = loaded.doc
    metrics = list((config_doc.get("criteria") or {}))
    tier = cost_tier(metrics) if metrics else "free"

    cases = [c.strip() for c in (args.cases or "").split(",") if c.strip()]

    if args.dry_run:
        # Show the command before any credential gate: the point of a dry run is
        # to see what would run, including on a machine that cannot run it.
        runner = resolve_runner(layout.agent_dir, layout.project_root, probe=False)
        prefix = runner.adk_argv() if runner.available else ["adk"]
        for run_index in range(args.runs):
            for evalset in evalsets:
                target_spec = f"{evalset}:{','.join(cases)}" if cases else evalset
                command = [*prefix, "eval", str(layout.agent_dir), str(target_spec)]
                if config:
                    command.append(f"--config_file_path={config}")
                if args.detailed:
                    command.append("--print_detailed_results")
                if args.storage:
                    command.append(f"--eval_storage_uri={args.storage}")
                print(" ".join(command))
        if not runner.available:
            print("note: no interpreter with google.adk was found; the command above assumes "
                  "`adk` on PATH", file=sys.stderr)
        print("note: bare `adk eval` exits 0 on every release up to 2.12.0 whatever the "
              "result (main after 2.12.0 exits 1 on a failure, unreleased); this wrapper "
              "reads the result files, so its exit code is the gate", file=sys.stderr)
        if args.min_pass_rate is not None:
            print(f"note: gating on a pass rate of at least {args.min_pass_rate:.1%}",
                  file=sys.stderr)
        return EXIT_OK

    backend = detect_backend(layout.agent_dir)
    if tier == "vertex" and not backend.vertex_ready:
        print("error: the config uses a metric scored by the Vertex AI evaluation service, "
              "which needs GOOGLE_API_KEY, or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION "
              "with ADC. Fix the agent's .env, or run a deterministic config.", file=sys.stderr)
        return EXIT_USAGE
    if tier != "free" and not backend.judge_ready:
        print("error: the config uses a judge metric but no backend is configured.",
              file=sys.stderr)
        return EXIT_USAGE

    # Resolving a runner probes interpreters, so only do it when we will run.
    runner = resolve_runner(layout.agent_dir, layout.project_root)
    if not runner.available:
        print(f"error: {runner.note}", file=sys.stderr)
        return EXIT_USAGE
    if runner.adk_version and not version_is_supported(runner.adk_version):
        print(f"error: ADK {runner.adk_version} found; these skills target 2.x", file=sys.stderr)
        return EXIT_USAGE

    runs: list[dict] = []
    totals = {"passed": 0, "failed": 0, "not_evaluated": 0}
    result_files: list[str] = []

    for run_index in range(args.runs):
        for evalset in evalsets:
            outcome = run_adk_eval(
                layout.agent_dir, [str(evalset)], runner=runner, config=config,
                cases=cases, detailed=args.detailed, storage_uri=args.storage,
                timeout=args.timeout,
            )
            runs.append({
                "run": run_index + 1,
                "evalset": Path(evalset).name,
                **outcome.to_dict(),
            })
            totals["passed"] += outcome.passed
            totals["failed"] += outcome.failed
            totals["not_evaluated"] += outcome.not_evaluated
            result_files.extend(outcome.new_result_files)

            if outcome.missing_eval_extra:
                print('error: the eval extra is not installed. '
                      f'Install with: pip install "{ADK_REQUIREMENT}"', file=sys.stderr)
                return EXIT_USAGE
            if not outcome.command.ok and not outcome.new_result_files:
                print(f"error: the run failed before producing results:\n{outcome.command.tail()}",
                      file=sys.stderr)
                return EXIT_USAGE

    failing = sorted({case for run in runs for case in run["failing_cases"]})
    skipped = sorted({case for run in runs for case in run["not_evaluated_cases"]})

    scored = totals["passed"] + totals["failed"] + totals["not_evaluated"]
    pass_rate = round(totals["passed"] / scored, 4) if scored else None
    if args.min_pass_rate is not None:
        # The global threshold replaces "every case passes": a NOT_EVALUATED
        # case counts against the rate, never for it.
        exit_code = EXIT_OK if pass_rate is not None and pass_rate >= args.min_pass_rate \
            else EXIT_FINDINGS
    elif totals["failed"]:
        exit_code = EXIT_FINDINGS
    elif skipped:
        exit_code = EXIT_FINDINGS if args.not_evaluated == "fail" else EXIT_INCONCLUSIVE
    else:
        exit_code = EXIT_OK

    payload = {
        "agent_dir": layout.agent_dir,
        "runner": describe_runner(runner),
        "config": config,
        "metrics": metrics,
        "cost_tier": tier,
        "evalsets": [Path(e).name for e in evalsets],
        "runs": args.runs,
        "totals": totals,
        "pass_rate": pass_rate,
        "min_pass_rate": args.min_pass_rate,
        "failing_cases": failing,
        "not_evaluated_cases": skipped,
        "result_files": result_files,
        "exit_code": exit_code,
        "detail": runs,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Runner:   {payload['runner']}")
        print(f"Config:   {config or '(ADK defaults)'}  [cost tier: {tier}]")
        print(f"Evalsets: {', '.join(payload['evalsets'])}"
              + (f"  cases: {', '.join(cases)}" if cases else ""))
        print(f"Runs:     {args.runs}")
        print()
        print(f"passed {totals['passed']}   failed {totals['failed']}   "
              f"not evaluated {totals['not_evaluated']}")
        if args.min_pass_rate is not None:
            rate = "n/a" if pass_rate is None else f"{pass_rate:.1%}"
            print(f"  pass rate {rate} against a floor of {args.min_pass_rate:.1%}: "
                  + ("PASS" if exit_code == EXIT_OK else "FAIL"))
        if failing:
            print(f"  failed: {', '.join(failing)}")
        if skipped:
            print(f"  never scored: {', '.join(skipped)}")
            print("  a NOT_EVALUATED case is a configuration problem, not a regression: "
                  "an unreachable judge, a missing reference, or an empty rubric list")
        if result_files:
            print()
            print(f"{len(result_files)} result file(s) written; "
                  "explain them with /adk:eval-analyze")
        if args.runs > 1:
            print(f"Reliability across {args.runs} runs: /adk:eval-analyze pass_at_k.py")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
