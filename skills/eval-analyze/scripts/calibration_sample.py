#!/usr/bin/env python3
"""Sample passing cases for human review.

An eval suite measures the agent against rules somebody wrote. If those rules are
too lenient, the suite goes green while the agent does things a reviewer would
reject, and nothing in the system notices - the metrics are measuring the wrong
thing, and measuring it perfectly.

The fix is periodic calibration: read a few **passing** cases by hand and ask
whether you agree with the verdict. When you do not, the criteria need
tightening, not the agent.

    calibration_sample.py --dir <eval_history> [--n 5] [--seed 0] [--format json|md]

Exit codes: 0 sampled, 2 nothing to read.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.results import find_result_files, latest_per_evalset, parse_result  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--dir", help="an eval_history directory")
    parser.add_argument("--n", type=int, default=5, help="how many passing cases to sample")
    parser.add_argument("--seed", type=int, default=0, help="fixed so a review is repeatable")
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

    passing = []
    for path in latest_per_evalset(files).values():
        run = parse_result(path)
        for case in run.cases:
            if case.status != "PASSED":
                continue
            passing.append({
                "eval_set_id": run.eval_set_id,
                "eval_id": case.eval_id,
                "metrics": [{"name": m.name, "score": m.score, "threshold": m.threshold}
                            for m in case.metrics],
                "turns": [{
                    "user": inv.user_text,
                    "expected_tools": [t["name"] for t in inv.expected_tools],
                    "actual_tools": [t["name"] for t in inv.actual_tools],
                    "actual_response": inv.actual_text,
                } for inv in case.invocations],
            })

    if not passing:
        print("error: no passing cases to calibrate against", file=sys.stderr)
        return EXIT_USAGE

    rng = random.Random(args.seed)
    sample = rng.sample(passing, min(args.n, len(passing)))

    if args.format == "json":
        print(json.dumps({"sampled": sample, "population": len(passing), "seed": args.seed},
                         indent=2))
        return EXIT_OK

    print(f"# Calibration sample: {len(sample)} of {len(passing)} passing case(s), seed {args.seed}")
    print()
    print("For each one, decide whether you agree it should have passed. Disagreement means "
          "the criteria are too lenient, not that the agent is wrong.")
    print()
    for entry in sample:
        print(f"## `{entry['eval_id']}` ({entry['eval_set_id']})")
        print()
        for metric in entry["metrics"]:
            score = "-" if metric["score"] is None else f"{metric['score']:.3f}"
            print(f"- {metric['name']}: {score} (threshold {metric['threshold']})")
        for index, turn in enumerate(entry["turns"], start=1):
            print()
            print(f"**Turn {index}** user: {turn['user'][:200]}")
            print(f"- tools: {' -> '.join(turn['actual_tools']) or '(none)'}")
            if turn["actual_response"]:
                print(f"- response: {turn['actual_response'][:300]}")
        print()
        print("Agree it passed?  [ ] yes  [ ] no, because ...")
        print()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
