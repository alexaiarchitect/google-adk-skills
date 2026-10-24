#!/usr/bin/env python3
"""Measure reliability across repeated runs.

One green run is one sample. An LLM agent is not deterministic, so the useful
question is not "does it work" but "how often, and where is it fragile".

`pass@1` is what a user gets: one attempt per request. `pass@k` is the chance
that at least one of k attempts succeeds. The gap between them is **fragility** -
how much the agent depends on being lucky or on retrying.

ADK has no pass@k of its own. `adk eval` runs each case once, and
`AgentEvaluator(num_runs=N)` averages scores across runs, which hides exactly the
variance this measures. So produce the runs with `/adk:eval-run --runs N`, which
keeps each run's results separate, then aggregate here. ADK main after 2.13.0
(unreleased) adds `multi_run_config` (`num_runs`, `cross_run_scoring` with a
`pass_rate` rule), the likely future replacement for this script once released;
every released version ignores that key.

    pass_at_k.py --dir <eval_history> [--evalset ID] [--last N] [--k 1,3,5]

Exit codes: 0 every case reliable, 1 something is not, 2 nothing to read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.passk import DEFAULT_KS, RUN_GUIDANCE, aggregate  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.results import find_result_files, group_runs, result_identity  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent dir, project, or history dir")
    parser.add_argument("--dir", help="an eval_history directory")
    parser.add_argument("--evalset", help="eval set id (required when several are present)")
    parser.add_argument("--last", type=int, help="use only the most recent N runs")
    parser.add_argument("--k", default=",".join(str(k) for k in DEFAULT_KS))
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    args = parser.parse_args(argv)

    source = Path(args.dir) if args.dir else Path(args.path)
    if not source.exists():
        print(f"error: {source} does not exist", file=sys.stderr)
        return EXIT_USAGE

    files = find_result_files(source)
    if not files and source.is_dir():
        for history in detect_layout(source).eval_history_dirs:
            files.extend(find_result_files(history))
    if not files:
        print(f"error: no eval results under {source}", file=sys.stderr)
        return EXIT_USAGE

    eval_set_id = args.evalset
    if not eval_set_id:
        present = {result_identity(path).get("eval_set_id") for path in files}
        present.discard(None)
        if len(present) > 1:
            print(f"error: several eval sets here ({', '.join(sorted(present))}); "
                  "pass --evalset", file=sys.stderr)
            return EXIT_USAGE
        eval_set_id = next(iter(present), None)

    runs = group_runs(files, eval_set_id=eval_set_id)
    runs = [run for run in runs if run.ok]
    if args.last:
        runs = runs[-args.last:]
    if not runs:
        print(f"error: no readable runs for {eval_set_id!r}", file=sys.stderr)
        return EXIT_USAGE

    try:
        ks = [int(k) for k in args.k.split(",") if k.strip()]
    except ValueError:
        print("error: --k must be comma-separated integers", file=sys.stderr)
        return EXIT_USAGE

    report = aggregate(runs, ks=ks, eval_set_id=eval_set_id)

    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_md())
        print()
        if report.unreliable:
            print("Read `pass@1` as production behaviour: one attempt per request. A case at "
                  "0.40 fails three requests in five, whatever `pass@5` says.")
            print()
            print("Next:")
            print("  - take the weakest case to /adk:agent-debug: a case that sometimes passes "
                  "is a different diagnosis from one that never does")
            print("  - after a fix, re-run and compare, rather than trusting one green run")
        else:
            print(f"Every case is reliable over {report.runs} run(s). For a release decision, "
                  f"{RUN_GUIDANCE['qualification']} runs distinguish 95% from 99%; "
                  f"{report.runs} cannot.")

    return EXIT_OK if report.all_reliable else EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
