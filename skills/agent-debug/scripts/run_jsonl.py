#!/usr/bin/env python3
"""Reproduce one interaction and keep the evidence.

Debugging an agent from the chat transcript is guessing. `adk run` without
`--jsonl` prints only the text the agent said, so every tool call, every
argument and every tool error - which is where the actual bug almost always is -
is invisible. With `--jsonl` the same run emits one JSON object per event, and
the failure becomes readable.

Two further things this does that running the command by hand does not:

* **It keeps the trace.** Traces land in `<agent>/.adk/skills/traces/<stamp>.jsonl`
  rather than scrolling past, so the run before a fix can be compared with the
  run after it. A debugging session without a before-trace has nothing to
  compare against.
* **It says what the run costs before spending it.** This is the one script in
  the debug skill that calls a real model. One invocation, one agent turn per
  tool round-trip; `--runs N` multiplies that, which is why the flag makes you
  ask for it.

    run_jsonl.py <agent-dir> "<prompt>" [--runs N] [--out F] [--timeout S]
                 [--dry-run] [--json]

Exit codes: 0 a trace was captured, 1 the run produced no events, 2 usage or
environment (no agent, no runner).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.adk_subprocess import describe_runner, run_adk_run_jsonl  # noqa: E402
from adk_skills.env import detect_backend, resolve_runner  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.templating import WritePlan  # noqa: E402

TRACE_DIR = Path(".adk") / "skills" / "traces"


def json_lines(text: str) -> list[dict[str, Any]]:
    """Keep only the lines that are JSON objects.

    `adk run` mixes a banner and log lines into stdout, so a naive save produces
    a file the trace reader rejects on its first line.
    """
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def tool_names(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for event in events:
        content = event.get("content") or {}
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            call = part.get("functionCall") or part.get("function_call")
            if isinstance(call, dict) and call.get("name"):
                names.append(call["name"])
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="agent directory or project root")
    parser.add_argument("prompt", help="the request to reproduce, in quotes")
    parser.add_argument("--runs", type=int, default=1,
                        help="repeat the same prompt N times, to see whether the failure "
                             "is deterministic (costs N invocations)")
    parser.add_argument("--out", help="write the trace here instead of under .adk/skills/traces")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--dry-run", action="store_true", help="print the command and stop")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(target)
    if not layout.agent_dir:
        extra = (f"; found {len(layout.sibling_agents)} agents, pick one"
                 if layout.sibling_agents else "")
        print(f"error: no ADK agent under {target}{extra}", file=sys.stderr)
        return EXIT_USAGE

    agent_dir = Path(layout.agent_dir)
    analyze = Path(__file__).resolve().parent / "analyze_trace.py"

    if args.dry_run:
        command = f'adk run {agent_dir} --jsonl "{args.prompt}"'
        payload = {
            "command": command,
            "runs": args.runs,
            "cost": f"{args.runs} real invocation(s) against the configured backend",
            "trace_dir": str(agent_dir / TRACE_DIR),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"cost: {payload['cost']}")
            for _ in range(args.runs):
                print(command)
        return EXIT_OK

    backend = detect_backend(layout.agent_dir)
    if backend.backend == "unknown":
        print("error: no backend configured for this agent, so the run would fail "
              "after doing nothing useful. Set GOOGLE_API_KEY, or "
              "GOOGLE_GENAI_USE_VERTEXAI=TRUE with a project and location, in the "
              f"agent's .env. Checked: {', '.join(backend.env_files) or 'no .env found'}",
              file=sys.stderr)
        return EXIT_USAGE

    runner = resolve_runner(layout.agent_dir, layout.project_root)
    if not runner.available:
        print(f"error: {runner.note}", file=sys.stderr)
        return EXIT_USAGE

    if not args.json:
        print(f"Runner:  {describe_runner(runner)}")
        print(f"Backend: {backend.backend}")
        print(f"Cost:    {args.runs} real invocation(s); "
              "this is the one debug step that spends.")

    plan = WritePlan()
    traces: list[dict[str, Any]] = []

    for index in range(args.runs):
        outcome = run_adk_run_jsonl(
            agent_dir, args.prompt, runner=runner, timeout=args.timeout
        )
        events = json_lines(outcome.combined)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        suffix = f"_{index + 1}" if args.runs > 1 else ""
        destination = (
            Path(args.out) if args.out and args.runs == 1
            else agent_dir / TRACE_DIR / f"{stamp}{suffix}.jsonl"
        )
        body = "".join(json.dumps(event) + "\n" for event in events)
        plan.add(destination, body, force=True,
                 reason=f"{len(events)} event(s) from run {index + 1}")
        traces.append({
            "run": index + 1,
            "path": str(destination),
            "events": len(events),
            "tool_calls": tool_names(events),
            "returncode": outcome.returncode,
            "timed_out": outcome.timed_out,
        })
        if not events:
            print(f"run {index + 1}: no JSONL events. Tail of the output:\n"
                  f"{outcome.tail(15)}", file=sys.stderr)
        payload_tail = outcome.tail(15)
        traces[-1]["output_tail"] = payload_tail if not events else ""

    written = plan.apply()
    captured = [t for t in traces if t["events"]]

    payload = {
        "agent_dir": str(agent_dir),
        "prompt": args.prompt,
        "runner": describe_runner(runner),
        "runs": args.runs,
        "traces": traces,
        "written": written,
        "analyze_command": (
            f"python3 ${{CLAUDE_SKILL_DIR}}/scripts/analyze_trace.py "
            f"--trace {traces[0]['path']} --agent-dir {agent_dir}"
        ) if traces else None,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print()
        for trace in traces:
            calls = ", ".join(trace["tool_calls"]) or "no tool calls"
            print(f"run {trace['run']}: {trace['events']} event(s)  [{calls}]")
            print(f"  {trace['path']}")
        if args.runs > 1:
            shapes = {tuple(t["tool_calls"]) for t in traces}
            print()
            print(f"{len(shapes)} distinct trajector{'y' if len(shapes) == 1 else 'ies'} "
                  f"across {args.runs} runs: "
                  + ("the failure is deterministic" if len(shapes) == 1
                     else "the failure is intermittent, so a single trace is not the whole story"))
        if captured:
            print()
            print("Name the failure shape:")
            print(f"  {payload['analyze_command']}")
            print(f"({analyze.name} reads this file directly; "
                  "`${CLAUDE_SKILL_DIR}` is /adk:agent-debug's directory.)")

    return EXIT_OK if captured else EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
