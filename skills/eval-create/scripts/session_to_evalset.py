#!/usr/bin/env python3
"""Turn a recorded run into an eval case: the golden-trace capture step.

Reads an `adk run --jsonl` trace or a saved session, and writes what happened -
the user turns, the tool calls with their arguments, the final answers - as an
eval case. This is the same conversion the dev UI's "add current session to eval
set" button performs.

What you capture becomes a specification, so capture a run you have checked.
A trace of the agent doing the wrong thing, saved as a golden trace, locks in
the wrong thing.

    session_to_evalset.py --trace run.jsonl --eval-id happy_travel_claim \
        --evalset tests/eval/evalsets/happy.evalset.json [--app-name app]

Exit codes: 0 done, 1 validation findings, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.evalset import (  # noqa: E402
    LoadedEvalSet, load_evalset, sanitize_eval_set_id, validate_evalset,
)
from adk_skills.jsonl_trace import load_trace, to_eval_invocations  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.templating import WritePlan, dumps_json  # noqa: E402
from adk_skills.tools_ast import extract_module  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trace", required=True, help="an `adk run --jsonl` file or a saved session")
    parser.add_argument("--eval-id", required=True, help="name the behaviour: <category>_<behaviour>")
    parser.add_argument("--evalset", required=True, help="evalset to add the case to (created if absent)")
    parser.add_argument("--app-name", help="session_input.app_name (inferred from the layout when omitted)")
    parser.add_argument("--user-id", default="evaluator")
    parser.add_argument("--agent-dir", help="agent, so tool names can be checked")
    parser.add_argument("--keep-responses", action="store_true",
                        help="also record tool responses (useful for groundedness review)")
    parser.add_argument("--drop-final-response", action="store_true",
                        help="record trajectory only, leaving the expected answer empty")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    trace_path = Path(args.trace)
    if not trace_path.exists():
        print(f"error: {trace_path} does not exist", file=sys.stderr)
        return EXIT_USAGE

    events = load_trace(trace_path)
    if not events:
        print(f"error: no events in {trace_path}. `adk run` prints tool calls only with --jsonl",
              file=sys.stderr)
        return EXIT_USAGE

    invocations = to_eval_invocations(events, include_responses=args.keep_responses)
    if not invocations:
        print("error: the trace has no user turn to build a case from", file=sys.stderr)
        return EXIT_USAGE
    if args.drop_final_response:
        for invocation in invocations:
            invocation["final_response"] = {"parts": [{"text": ""}], "role": "model"}

    evalset_path = Path(args.evalset)
    layout = detect_layout(args.agent_dir or evalset_path.parent)
    app_name = args.app_name or layout.expected_app_name() or "app"

    if evalset_path.exists():
        loaded = load_evalset(evalset_path)
        if loaded.fmt != "evalset" or not isinstance(loaded.doc, dict):
            print(f"error: {evalset_path} is not an EvalSet "
                  f"({loaded.error or loaded.fmt}); migrate it first", file=sys.stderr)
            return EXIT_USAGE
        document = loaded.doc
    else:
        document = {
            "eval_set_id": sanitize_eval_set_id(
                evalset_path.name.replace(".evalset.json", "").replace(".test.json", "")
            ),
            "name": evalset_path.stem,
            "eval_cases": [],
        }

    existing = {case.get("eval_id") for case in document.get("eval_cases", [])}
    if args.eval_id in existing:
        print(f"error: {args.eval_id!r} already exists in {evalset_path.name}. A golden trace is "
              "a specification: add a new case, or change the existing one deliberately.",
              file=sys.stderr)
        return EXIT_USAGE

    case = {
        "eval_id": args.eval_id,
        "conversation": invocations,
        "session_input": {"app_name": app_name, "user_id": args.user_id, "state": {}},
    }
    document.setdefault("eval_cases", []).append(case)

    known_tools = None
    if layout.agent_dir:
        known_tools = extract_module(layout.agent_dir).bound_tool_names or None

    report = GapReport(subject=str(evalset_path), skill="eval-create")
    validate_evalset(
        LoadedEvalSet(str(evalset_path), document, document, "evalset"),
        report=report, expected_app_name=app_name, known_tools=known_tools,
        reference_metrics=not args.drop_final_response,
    )

    plan = WritePlan()
    plan.add(evalset_path, dumps_json(document), force=True,
             reason="adds one case; existing cases are untouched")

    captured = {
        "eval_id": args.eval_id,
        "turns": len(invocations),
        "tool_calls": [t["name"] for inv in invocations
                       for t in inv["intermediate_data"]["tool_uses"]],
        "app_name": app_name,
    }

    written = [] if args.dry_run else plan.apply()

    if args.json:
        print(json.dumps({
            "captured": captured, "write_plan": plan.to_dict(), "written": written,
            "validation": report.to_dict(),
        }, indent=2))
    else:
        print(f"Captured {captured['turns']} turn(s) as {args.eval_id!r}")
        print(f"  trajectory: {' -> '.join(captured['tool_calls']) or '(no tool calls)'}")
        print(f"  app_name:   {app_name}")
        print()
        print(plan.to_md())
        if report.findings:
            print()
            print(report.to_md())
        print()
        print("Check the captured trajectory before committing it: a saved trace is a "
              "specification, and a wrong one is worse than none.")

        for path in written:
            print(f"wrote {path}")

    return EXIT_FINDINGS if report.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
