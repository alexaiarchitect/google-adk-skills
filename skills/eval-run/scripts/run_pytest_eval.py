#!/usr/bin/env python3
"""Run a project's pytest eval suite through an interpreter that has ADK.

`AgentEvaluator` raises an `AssertionError` when a case fails, so pytest's exit
code is a real gate - unlike `adk eval`, which always returns 0. Where a project
already has `tests/eval/test_eval.py`, this is the simplest honest gate there is.

On ADK 2.10 `AgentEvaluator.evaluate` / `evaluate_eval_set` raise `ValueError`
when the run would evaluate no eval case at all (an empty evalset, or a `-k`
expression that matched nothing) and when `num_runs < 1`; on 2.9 both cases ran
and reported nothing. So a `ValueError` out of the library here is usually an
empty suite rather than a broken agent.

`AgentEvaluator` also accepts an `agent_name`, which evaluates one sub-agent in
isolation. `adk eval` has no equivalent, so a multi-agent project that wants to
gate a single sub-agent has to come through here. `--agent-name` is passed to
the generated test as `ADK_EVAL_AGENT_NAME`; a test file written by hand needs to
read it too (`gen_test_eval.py` writes one that does).

    run_pytest_eval.py <agent-dir-or-project> [-k EXPR] [--num-runs N]
                       [--agent-name NAME] [--] [pytest args]
    run_pytest_eval.py <agent-dir-or-project> --behavioral [--runs N]
                       [--min-pass-rate P] [-k EXPR]

`--behavioral` runs `tests/eval/test_behaviors.py` (written by
`gen_test_eval.py --behavioral`) on its own: each behaviour `--runs` times
(`ADK_EVAL_RUNS`), gated on `--min-pass-rate` (`ADK_EVAL_MIN_PASS_RATE`,
default 1.0). Without the flag that file is left out of the AgentEvaluator run,
so the two gates stay separate.

Exit code is pytest's own, except 2 when the environment cannot run it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.adk_subprocess import describe_runner, run_pytest  # noqa: E402
from adk_skills.env import resolve_runner  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_USAGE  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("-k", dest="expression", help="pytest -k expression")
    parser.add_argument("--num-runs", type=int,
                        help="sets ADK_EVAL_NUM_RUNS; note AgentEvaluator averages across runs, "
                             "so use /adk:eval-run --runs N for pass@k instead. Must be >= 1: "
                             "ADK 2.10 raises ValueError for less")
    parser.add_argument("--agent-name",
                        help="evaluate this sub-agent instead of the root agent "
                             "(AgentEvaluator only; `adk eval` cannot do this)")
    parser.add_argument("--tests", help="test path (default: the project's eval tests)")
    parser.add_argument("--behavioral", action="store_true",
                        help="run tests/eval/test_behaviors.py instead of the AgentEvaluator "
                             "suite")
    parser.add_argument("--runs", type=int,
                        help="--behavioral: run each behaviour N times (sets ADK_EVAL_RUNS)")
    parser.add_argument("--min-pass-rate", type=float,
                        help="--behavioral: the per-behaviour pass-rate floor, 0..1 (sets "
                             "ADK_EVAL_MIN_PASS_RATE; default 1.0)")
    # Everything this script does not know goes to pytest, before or after `--`;
    # a positional REMAINDER would swallow flags written after the path.
    raw = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in raw:
        cut = raw.index("--")
        raw, passthrough = raw[:cut], raw[cut + 1:]
    args, unknown = parser.parse_known_args(raw)
    args.pytest_args = unknown + passthrough

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(target)
    if (args.runs is not None or args.min_pass_rate is not None) and not args.behavioral:
        print("error: --runs and --min-pass-rate apply to --behavioral; for the "
              "AgentEvaluator suite measure reliability with /adk:eval-run --runs N",
              file=sys.stderr)
        return EXIT_USAGE
    if args.runs is not None and args.runs < 1:
        print("error: --runs must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    if args.min_pass_rate is not None and not 0.0 <= args.min_pass_rate <= 1.0:
        print("error: --min-pass-rate is a fraction between 0 and 1", file=sys.stderr)
        return EXIT_USAGE
    tests = args.tests
    behavior_file = None
    if args.behavioral and not tests:
        candidates = [Path(p) for p in layout.pytest_eval_files if Path(p).name == "test_behaviors.py"]
        candidates.append(Path(layout.project_root or target) / "tests" / "eval" / "test_behaviors.py")
        behavior_file = next((c for c in candidates if c.is_file()), None)
        if behavior_file is None:
            print("error: no tests/eval/test_behaviors.py. Write one with "
                  "gen_test_eval.py --behavioral.", file=sys.stderr)
            return EXIT_USAGE
        tests = str(behavior_file)
    if not tests:
        if layout.pytest_eval_files:
            tests = str(Path(layout.pytest_eval_files[0]).parent)
        else:
            candidate = Path(layout.project_root or target) / "tests" / "eval"
            if not candidate.is_dir():
                print("error: no pytest eval suite found. Generate one with "
                      "/adk:agent-create, or run /adk:eval-run instead.", file=sys.stderr)
                return EXIT_USAGE
            tests = str(candidate)

    runner = resolve_runner(layout.agent_dir or target, layout.project_root)
    if not runner.available:
        print(f"error: {runner.note}", file=sys.stderr)
        return EXIT_USAGE

    pytest_args = [tests, "-v"]
    if not args.behavioral and Path(tests).is_dir() and (Path(tests) / "test_behaviors.py").is_file():
        # Behavioural checks are their own gate, with their own run count.
        pytest_args.append(f"--ignore={Path(tests) / 'test_behaviors.py'}")
    if args.expression:
        pytest_args.extend(["-k", args.expression])
    extra = [a for a in args.pytest_args if a != "--"]
    pytest_args.extend(extra)

    env: dict[str, str] = {}
    if args.num_runs:
        env["ADK_EVAL_NUM_RUNS"] = str(args.num_runs)
    if args.agent_name:
        env["ADK_EVAL_AGENT_NAME"] = args.agent_name
    if args.runs is not None:
        env["ADK_EVAL_RUNS"] = str(args.runs)
    if args.min_pass_rate is not None:
        env["ADK_EVAL_MIN_PASS_RATE"] = str(args.min_pass_rate)

    print(f"Runner: {describe_runner(runner)}")
    print(f"Tests:  {tests}")
    if args.agent_name:
        print(f"Agent:  {args.agent_name} (sub-agent; the test must pass agent_name through)")
    outcome = run_pytest(pytest_args, runner=runner, cwd=layout.project_root or target,
                         env=env or None)
    print(outcome.stdout)
    if outcome.stderr:
        print(outcome.stderr, file=sys.stderr)
    return outcome.returncode


if __name__ == "__main__":
    raise SystemExit(main())
