#!/usr/bin/env python3
"""Name the shape of a misbehaving run, and say which layer to fix.

Most agent failures are one of a handful of shapes, and each one has a different
cause and therefore a different fix. Applying a correct fix at the wrong layer is
the expensive mistake, so this names the layer before suggesting anything:

| Shape | What you see | Layer |
|---|---|---|
| loop | the same call with the same arguments, repeatedly | tool |
| oscillation | two calls alternating, never progressing | prompt |
| stall | model text where a call was required | prompt |
| unknown tool | a call to something that is not registered | model |
| cascade | an early argument mistake that later calls build on | tool |

Input is whatever you have: an `adk run --jsonl` trace, a saved session, or an
eval result file.

    analyze_trace.py (--trace run.jsonl | --result r.json [--eval-id ID])
                     [--agent-dir DIR] [--format json|md]

Exit codes: 0 nothing found, 1 signatures found, 2 nothing to read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.jsonl_trace import load_trace, model_texts, tool_calls, tool_errors  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.results import find_result_files, parse_result  # noqa: E402
from adk_skills.tools_ast import extract_module  # noqa: E402
from adk_skills.trajectory import cascade_suspect, detect_signatures, diff  # noqa: E402

LAYER_NOTES = {
    "tool": "the tool layer: deterministic code, schemas, and the messages tools return",
    "prompt": "the prompt layer: ordering rules, prohibitions, tool descriptions",
    "model": "the model layer: sampling, capability, and what the model invents",
    "eval": "the evaluation itself: the golden trace or the thresholds may be wrong",
}


def from_trace(path: Path, known_tools: set[str] | None) -> dict:
    events = load_trace(path)
    if not events:
        return {}
    calls = tool_calls(events)
    signatures = detect_signatures(
        calls, model_texts=model_texts(events), known_tools=known_tools
    )
    return {
        "source": str(path),
        "kind": "trace",
        "events": len(events),
        "trajectory": [c["name"] for c in calls],
        "tool_errors": tool_errors(events),
        "signatures": [s.to_dict() for s in signatures],
    }


def from_result(path: Path, eval_id: str | None, known_tools: set[str] | None) -> list[dict]:
    run = parse_result(path)
    if not run.ok:
        return []
    entries: list[dict] = []
    for case in run.cases:
        if eval_id and case.eval_id != eval_id:
            continue
        if case.status == "PASSED":
            continue
        signatures: list[dict] = []
        divergences: list[dict] = []
        for invocation in case.invocations:
            actual = invocation.actual_tools
            found = detect_signatures(
                actual,
                model_texts=[invocation.actual_text] if invocation.actual_text else [],
                known_tools=known_tools,
                max_expected_calls=len(invocation.expected_tools) or None,
            )
            signatures.extend(s.to_dict() for s in found)
            if invocation.expected_tools:
                result = diff(invocation.expected_tools, actual)
                if not result.match:
                    payload = {"turn": invocation.index + 1, **result.to_dict()}
                    cascade = cascade_suspect(result)
                    if cascade:
                        signatures.append(cascade.to_dict())
                    divergences.append(payload)
        if case.status == "NOT_EVALUATED":
            signatures.append({
                "kind": "not_evaluated", "layer": "eval",
                "detail": "the metric never produced a score, so this is a configuration "
                          "problem rather than an agent regression",
                "evidence": [m.name for m in case.unevaluated_metrics()],
                "fix": "check judge credentials, the reference response, and that the "
                       "effective rubric list is non-empty",
            })
        entries.append({
            "source": str(path),
            "kind": "result",
            "eval_id": case.eval_id,
            "status": case.status,
            "trajectory": [t["name"] for inv in case.invocations for t in inv.actual_tools],
            "divergences": divergences,
            "signatures": signatures,
        })
    return entries


def render(entries: list[dict]) -> str:
    lines: list[str] = []
    for entry in entries:
        title = entry.get("eval_id") or Path(entry["source"]).name
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"- trajectory: {' -> '.join(entry['trajectory']) or '(no tool calls)'}")
        if entry.get("status"):
            lines.append(f"- status: {entry['status']}")
        # The same error repeated is one fact, not N.
        seen: dict[tuple, int] = {}
        for error in entry.get("tool_errors", []):
            key = (error["tool"], json.dumps(error["payload"], sort_keys=True))
            seen[key] = seen.get(key, 0) + 1
        for (tool, payload), count in seen.items():
            times = "" if count == 1 else f" (x{count})"
            lines.append(f"- tool error from `{tool}`{times}: {payload}")
        if seen:
            lines.append("  (a tool failure is returned as a result, not raised, so the run "
                         "continued past it)")

        for divergence in entry.get("divergences", []):
            lines.append(f"- turn {divergence['turn']}: {divergence['explanation']}")

        if not entry["signatures"]:
            lines.append("")
            lines.append("No known failure shape. Work from the first divergence, and if the "
                         "trajectory looks right, suspect the arguments before the sequence.")
            lines.append("")
            continue

        lines.append("")
        for signature in entry["signatures"]:
            lines.append(f"**{signature['kind']}** — {signature['detail']}")
            lines.append(f"- layer: {signature['layer']} ({LAYER_NOTES.get(signature['layer'], '')})")
            if signature.get("evidence"):
                evidence = ", ".join(str(e) for e in signature["evidence"][:4])
                lines.append(f"- evidence: {evidence}")
            lines.append(f"- fix: {signature['fix']}")
            lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trace", help="an `adk run --jsonl` file or a saved session")
    parser.add_argument("--result", help="an eval result file, or a directory of them")
    parser.add_argument("--eval-id", help="with --result, only this case")
    parser.add_argument("--agent-dir", help="agent, so invented tool names can be spotted")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    args = parser.parse_args(argv)

    if not args.trace and not args.result:
        print("error: pass --trace or --result", file=sys.stderr)
        return EXIT_USAGE

    known_tools = None
    agent_dir = args.agent_dir
    if not agent_dir:
        seed = Path(args.trace or args.result).parent
        agent_dir = detect_layout(seed).agent_dir
    if agent_dir and Path(agent_dir).exists():
        facts = extract_module(agent_dir)
        # A name is "unknown" only when the agent could never emit it: toolsets
        # and AgentTools are real, and a runtime-built tool list proves nothing.
        if facts.tools_unresolved:
            known_tools = None
        else:
            known_tools = (facts.bound_tool_names | set(facts.opaque_tools)) or None

    entries: list[dict] = []
    if args.trace:
        path = Path(args.trace)
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            return EXIT_USAGE
        entry = from_trace(path, known_tools)
        if not entry:
            print(f"error: no events in {path}. `adk run` needs --jsonl to emit tool calls",
                  file=sys.stderr)
            return EXIT_USAGE
        entries.append(entry)

    if args.result:
        source = Path(args.result)
        if not source.exists():
            print(f"error: {source} does not exist", file=sys.stderr)
            return EXIT_USAGE
        files = find_result_files(source)
        if not files:
            print(f"error: no eval results under {source}", file=sys.stderr)
            return EXIT_USAGE
        for path in files:
            entries.extend(from_result(Path(path), args.eval_id, known_tools))
        if not entries:
            print("no failing cases to analyze", file=sys.stderr)
            return EXIT_OK

    if args.format == "json":
        print(json.dumps({"known_tools": sorted(known_tools) if known_tools else None,
                          "entries": entries}, indent=2))
    else:
        print(render(entries))

    found = any(entry["signatures"] for entry in entries)
    return EXIT_FINDINGS if found else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
