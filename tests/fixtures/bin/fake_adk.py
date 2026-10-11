#!/usr/bin/env python3
"""Stand-in for an interpreter that has ADK installed.

Subprocess tests point `ADK_PYTHON` at this file. It answers the version probe,
and for `-m google.adk.cli eval ...` it prints a summary in ADK's format and
writes a result file where ADK would write one, so the eval wrapper's parsing,
file discovery and exit-code logic are all exercised without ADK or a network.

Behaviour is driven by environment variables:

    FAKE_ADK_PASSED / FAKE_ADK_FAILED / FAKE_ADK_NOT_EVALUATED  case counts
    FAKE_ADK_RETURNCODE                                          exit code to use
    FAKE_ADK_NO_RESULT_FILE                                      skip writing results
    FAKE_ADK_CONFORMANCE_RETURNCODE                              `conformance` exit code
    FAKE_ADK_RUN_TOOL                                            tool name in the run trace
    FAKE_ADK_RUN_EMPTY                                           `run` emits no events
    FAKE_ADK_GENERATED_CASES                                     cases `generate_eval_cases` writes
    FAKE_ADK_EFFICIENCY                                          add ADK 2.10's four
                                                                 informational efficiency
                                                                 metrics to every case
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

#: The release the plugin targets. `FAKE_ADK_VERSION=2.9.2` makes this stand-in
#: an older runner, which is how the 2.9-vs-2.10+ behaviour is tested.
VERSION = os.environ.get("FAKE_ADK_VERSION", "2.11.0")


def _probe(code: str, args: list[str]) -> int:
    if "AgentLoader" in code and len(args) >= 3:
        return _load_probe(*args[:3])
    if "google.adk" in code:
        print(VERSION)
        return 0
    exec(compile(code, "<fake-adk>", "exec"), {"__name__": "__main__"})
    return 0


def _load_probe(agents_dir: str, agent_name: str, module_path: str) -> int:
    """Answer load_agent.py's probe from the files alone, the way the real
    AgentLoader would resolve them: a `root_agent.yaml`, else an `app`, else a
    `root_agent`."""
    import re

    agent_dir = Path(agents_dir) / agent_name
    result = {"module": module_path, "adk_version": VERSION, "loader": "fake AgentLoader"}
    yaml_config = agent_dir / "root_agent.yaml"
    source = ""
    for candidate in (agent_dir / "agent.py", agent_dir / "__init__.py"):
        if candidate.is_file():
            source = candidate.read_text(encoding="utf-8")
            break
    if os.environ.get("FAKE_ADK_LOAD_ERROR"):
        result.update({"loaded": False, "error_type": "ValidationError",
                       "error": os.environ["FAKE_ADK_LOAD_ERROR"], "traceback": []})
    elif yaml_config.is_file():
        text = yaml_config.read_text(encoding="utf-8")
        name = re.search(r"^name:\s*(\S+)", text, re.M)
        model = re.search(r"^model:\s*(\S+)", text, re.M)
        result.update({"loaded": True, "export": "root_agent.yaml",
                       "agent_name": name.group(1) if name else None,
                       "agent_type": "LlmAgent",
                       "model": model.group(1) if model else None,
                       "tools": [], "sub_agents": [], "instruction_chars": 0})
    elif source:
        name = re.search(r"name\s*=\s*[\"']([^\"']+)[\"']", source)
        export = "app" if re.search(r"^\s*app\s*=|return App\(", source, re.M) else "root_agent"
        result.update({"loaded": True, "export": export,
                       "agent_name": name.group(1) if name else None,
                       "agent_type": "LlmAgent", "model": "gemini-flash-latest",
                       "tools": [], "sub_agents": [], "instruction_chars": 0})
        if export == "app":
            result["app_name"] = agent_name
            result["plugins"] = []
    else:
        result.update({"loaded": False, "error_type": "ValueError",
                       "error": f"No root_agent found for '{agent_name}'.", "traceback": []})
    print(json.dumps(result))
    return 0


#: What ADK 2.10 appends to every eval, whatever the config says: a value, no
#: threshold, no criterion, and `eval_status` 4 (`EvalStatus.INFORMATIONAL`).
EFFICIENCY_RESULTS = [
    {"metric_name": "tool_call_count_v1", "score": 1.0, "eval_status": 4, "details": {}},
    {"metric_name": "inference_call_count_v1", "score": 2.0, "eval_status": 4, "details": {}},
    {"metric_name": "token_usage_v1", "score": 312.0, "eval_status": 4, "details": {
        "token_usage_details": {
            "total_tokens": 312.0, "input_tokens": 257.0, "prompt_tokens": 250.0,
            "cached_tokens": 40.0, "tool_use_tokens": 7.0, "output_tokens": 55.0,
            "candidates_tokens": 50.0, "reasoning_tokens": 5.0,
        }
    }},
    {"metric_name": "invocation_duration_v1", "score": 2.5, "eval_status": 4, "details": {}},
]


def _status_case(eval_id: str, status: int) -> dict:
    metrics = [{
        "metric_name": "tool_trajectory_avg_score",
        "threshold": 1.0,
        "score": 1.0 if status == 1 else (None if status == 3 else 0.5),
        "eval_status": status,
        "criterion": {"threshold": 1.0},
    }]
    if os.environ.get("FAKE_ADK_EFFICIENCY"):
        metrics.extend(json.loads(json.dumps(EFFICIENCY_RESULTS)))
    return {
        "eval_set_id": os.environ.get("FAKE_ADK_EVAL_SET", "fixture_set"),
        "eval_id": eval_id,
        "final_eval_status": status,
        "overall_eval_metric_results": metrics,
        "eval_metric_result_per_invocation": [],
        "session_id": f"___eval___session___{eval_id}",
        "user_id": "evaluator",
    }


def _eval(args: list[str]) -> int:
    agent_dir = Path(args[0]).resolve() if args else Path.cwd()
    eval_set = os.environ.get("FAKE_ADK_EVAL_SET", "fixture_set")
    passed = int(os.environ.get("FAKE_ADK_PASSED", "2"))
    failed = int(os.environ.get("FAKE_ADK_FAILED", "0"))
    skipped = int(os.environ.get("FAKE_ADK_NOT_EVALUATED", "0"))

    cases = [_status_case(f"case_pass_{i}", 1) for i in range(passed)]
    cases += [_status_case(f"case_fail_{i}", 2) for i in range(failed)]
    cases += [_status_case(f"case_skip_{i}", 3) for i in range(skipped)]

    if not os.environ.get("FAKE_ADK_NO_RESULT_FILE"):
        history = agent_dir / ".adk" / "eval_history"
        history.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        target = history / f"{agent_dir.name}_{eval_set}_{stamp}.evalset_result.json"
        target.write_text(json.dumps({
            "eval_set_result_id": target.stem,
            "eval_set_result_name": target.stem,
            "eval_set_id": eval_set,
            "creation_timestamp": float(stamp),
            "eval_case_results": cases,
        }, indent=2))

    print("*********************************************************************")
    print("Eval Run Summary")
    print(f"{eval_set}:")
    # ADK counts anything that is not PASSED in the failed column.
    print(f"  Tests passed: {passed}")
    print(f"  Tests failed: {failed + skipped}")
    return int(os.environ.get("FAKE_ADK_RETURNCODE", "0"))


def _run(args: list[str]) -> int:
    """`adk run <agent> --jsonl "<prompt>"`: two events, the shape a real trace has.

    One user event and one model event carrying a function call, which is enough
    for the trace writer, the JSONL filter and analyze_trace.py to be exercised.
    """
    prompt = args[-1] if args else ""
    if os.environ.get("FAKE_ADK_RUN_EMPTY"):
        print("no events")
        return 0
    tool = os.environ.get("FAKE_ADK_RUN_TOOL", "lookup_policy")
    print(json.dumps({
        "author": "user",
        "invocationId": "inv_1",
        "content": {"role": "user", "parts": [{"text": prompt}]},
    }))
    print(json.dumps({
        "author": "claims_agent",
        "invocationId": "inv_1",
        "content": {"role": "model", "parts": [
            {"functionCall": {"name": tool, "args": {"req": {"category": "travel"}}}}
        ]},
        "nodeInfo": {"path": "root"},
    }))
    return int(os.environ.get("FAKE_ADK_RETURNCODE", "0"))


def _conformance(args: list[str]) -> int:
    """`adk conformance record|test`: unlike `adk eval`, this one really does
    set a non-zero exit code, so the wrapper has something honest to pass on."""
    action = args[0] if args else "?"
    code = int(os.environ.get("FAKE_ADK_CONFORMANCE_RETURNCODE", "0"))
    if action == "record":
        # The trailing positional is the streaming mode, not a flag.
        positional = [a for a in args[1:] if not a.startswith("-")]
        mode = "none"
        if positional and positional[-1] in ("none", "sse", "bidi"):
            mode = positional.pop()
        for directory in positional:
            case = Path(directory)
            if case.is_dir():
                (case / "generated-recordings.yaml").write_text(
                    f"# fake recording ({mode})\n", encoding="utf-8"
                )
        print(f"Generating ADK conformance tests... mode={mode}")
        return code
    print("Conformance test run")
    print(f"  cases: {len([a for a in args[1:] if not a.startswith('-')])}")
    if code:
        print("FAILED: recorded interactions do not match", file=sys.stderr)
    return code


def _eval_set(args: list[str]) -> int:
    """`adk eval_set generate_eval_cases <agent> <id> --user_simulation_config_file F`."""
    action = args[0] if args else "?"
    if action != "generate_eval_cases":
        print(f"fake-adk: unsupported eval_set action {action}", file=sys.stderr)
        return 2
    positional = [a for a in args[1:] if not a.startswith("-")]
    agent_dir = Path(positional[0]).resolve() if positional else Path.cwd()
    eval_set_id = positional[1] if len(positional) > 1 else "generated"
    count = int(os.environ.get("FAKE_ADK_GENERATED_CASES", "2"))
    document = {
        "eval_set_id": eval_set_id,
        "name": eval_set_id.replace("_", " ").title(),
        "eval_cases": [
            {
                "eval_id": f"{eval_set_id}_scenario_{index + 1}",
                "conversation_scenario": {
                    "starting_prompt": "I need to claim some expenses from last week.",
                    "conversation_plan": (
                        "State that you work in engineering, then ask about a "
                        "900 dollar flight without repeating the cost centre."
                    ),
                    "user_persona": "NOVICE",
                },
                "session_input": {
                    "app_name": agent_dir.name, "user_id": "evaluator", "state": {}
                },
            }
            for index in range(count)
        ],
    }
    target = agent_dir / f"{eval_set_id}.evalset.json"
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"Generated {count} eval case(s) into {target}")
    return int(os.environ.get("FAKE_ADK_RETURNCODE", "0"))


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "-c":
        return _probe(argv[1], argv[2:])
    if len(argv) >= 2 and argv[0] == "-m" and argv[1] == "google.adk.cli":
        rest = argv[2:]
        if rest and rest[0] == "eval":
            return _eval(rest[1:])
        if rest and rest[0] == "run":
            return _run(rest[1:])
        if rest and rest[0] == "conformance":
            return _conformance(rest[1:])
        if rest and rest[0] == "eval_set":
            return _eval_set(rest[1:])
        print(f"fake-adk: unsupported subcommand {' '.join(rest) or '(none)'}", file=sys.stderr)
        return 2
    print(f"fake-adk: unsupported invocation {' '.join(argv)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
