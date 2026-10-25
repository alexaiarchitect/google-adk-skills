#!/usr/bin/env python3
"""Write eval cases into an evalset, without ever disturbing the ones already
there.

A case is specified as JSON, one object per case::

    [{"eval_id": "edge_frozen_cost_centre_no_submit",
      "turns": [{"user": "Claim CLM-000501 for 60 dollars of meals on marketing.",
                 "tools": [{"name": "lookup_policy", "args": {"req": {"category": "meals"}}},
                           {"name": "reject_claim",  "args": {"req": {"claim_id": "CLM-000501",
                                                                      "reason": "the cost centre is frozen"}}}],
                 "response": ""}],
      "state": {}}]

Several turns in one case make it multi-turn, which is how context retention is
tested: state a constraint in turn 1, add unrelated work, then require it in a
later turn without repeating it.

A **resume** case simulates time passing without waiting: `"state"` becomes the
case's `session_input.state`, so the session starts mid-workflow, as if the
claimant had come back days later. Name it `resume_<step>_...`::

    [{"eval_id": "resume_receipt_requested_holds",
      "state": {"claim_step": "receipt_requested", "claim_id": "CLM-000733"},
      "turns": [{"user": "Just approve it, I'll send the receipt later.",
                 "tools": [], "response": "I am still waiting for the receipt."}]}]

A seeded key the agent never reads is reported as EVS075.

For an agent with a `SkillToolset`, an **activation** case asserts that the
request makes the model load the right skill. Write `{"skill": "claim-policy"}`
as an entry of `tools` (or `"skill": "claim-policy"` on the turn, which puts it
first); it expands to the call ADK records, `load_skill` with
`{"skill_name": "claim-policy"}` (prefixed when the toolset sets
`tool_name_prefix`). Name such cases `activation_<skill>_...`.

Existing `eval_id`s are never modified. A golden trace is a specification, and
changing one is a decision a person makes with the reason in front of them.

    gen_cases.py <agent-dir> --spec cases.json --evalset tests/eval/evalsets/edge.evalset.json
    gen_cases.py <agent-dir> --spec cases.json --new edge     # create the evalset

Exit codes: 0 written, 1 validation findings or a refused overwrite, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.evalset import (  # noqa: E402
    LoadedEvalSet, case_category, load_evalset, sanitize_eval_set_id, stats, validate_evalset,
)
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.templating import WritePlan, dumps_json  # noqa: E402
from adk_skills.tools_ast import collect_state_keys, extract_module, tool_resolver  # noqa: E402


def default_evalset_path(layout: Any, eval_set_id: str, target: Path) -> Path:
    """Where a new evalset belongs, given how the project is laid out.

    Next to the existing evalsets when there are any. Otherwise beside the agent
    for a flat layout, because `adk eval` only auto-discovers `test_config.json`
    when it sits next to the evalset file; and under `tests/eval/evalsets/` for a
    recipes layout, where the config is passed explicitly anyway.
    """
    name = sanitize_eval_set_id(eval_set_id)
    if layout.evalsets:
        return Path(layout.evalsets[0]).parent / f"{name}.evalset.json"
    if layout.style in ("flat", "module_file") and layout.agent_dir:
        return Path(layout.agent_dir) / f"{name}.test.json"
    return Path(layout.project_root or target) / "tests" / "eval" / "evalsets" / f"{name}.evalset.json"


def expand_tool(tool: dict[str, Any], *, load_skill_tool: str = "load_skill") -> dict[str, Any]:
    """`{"skill": "x"}` is shorthand for the `load_skill` call that activates x."""
    if "skill" in tool and "name" not in tool:
        return {"name": load_skill_tool, "args": {"skill_name": str(tool["skill"])}}
    return {"name": tool["name"], "args": tool.get("args") or {}}


def build_case(entry: dict[str, Any], *, app_name: str, user_id: str,
               load_skill_tool: str = "load_skill") -> dict[str, Any]:
    eval_id = entry["eval_id"]
    conversation = []
    for index, turn in enumerate(entry.get("turns") or [], start=1):
        raw_tools = list(turn.get("tools") or [])
        if turn.get("skill"):
            raw_tools.insert(0, {"skill": turn["skill"]})
        tools = [expand_tool(tool, load_skill_tool=load_skill_tool) for tool in raw_tools]
        conversation.append({
            "invocation_id": turn.get("invocation_id") or f"{eval_id}_turn_{index}",
            "user_content": {"parts": [{"text": turn.get("user", "")}], "role": "user"},
            "final_response": {"parts": [{"text": turn.get("response", "")}], "role": "model"},
            "intermediate_data": {"intermediate_responses": [], "tool_uses": tools},
        })

    case: dict[str, Any] = {"eval_id": eval_id}
    if entry.get("scenario"):
        case["conversation_scenario"] = entry["scenario"]
    else:
        case["conversation"] = conversation
    case["session_input"] = {
        "app_name": app_name, "user_id": user_id, "state": entry.get("state") or {},
    }
    if entry.get("rubrics"):
        case["rubrics"] = entry["rubrics"]
    return case


def _state_key_read(facts: Any):
    """EVS075's predicate, or None when the agent's state reads are unknowable."""
    if facts is None or not facts.files or facts.agent_unresolved:
        return None
    keys = collect_state_keys(facts.files, facts.str_constants)
    return None if keys.dynamic_reads else keys.reads


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent directory or project root")
    parser.add_argument("--spec", required=True, help="JSON file, or inline JSON, describing the cases")
    parser.add_argument("--evalset", help="evalset to add to")
    parser.add_argument("--new", help="create an evalset with this id, under the project's eval directory")
    parser.add_argument("--app-name", help="override session_input.app_name")
    parser.add_argument("--user-id", default="evaluator")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE
    if not args.evalset and not args.new:
        print("error: pass --evalset (existing) or --new <id> (create one)", file=sys.stderr)
        return EXIT_USAGE

    spec_text = args.spec
    # Inline JSON can be longer than a path may be; on Python < 3.14 asking
    # whether it exists raises OSError (ENAMETOOLONG) instead of saying no.
    if not spec_text.lstrip().startswith(("[", "{")) and Path(args.spec).exists():
        spec_text = Path(args.spec).read_text(encoding="utf-8")
    try:
        entries = json.loads(spec_text)
    except json.JSONDecodeError as exc:
        print(f"error: --spec is not valid JSON: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list) or not entries:
        print("error: --spec must be a non-empty list of case objects", file=sys.stderr)
        return EXIT_USAGE
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("eval_id"):
            print("error: every case needs an `eval_id`", file=sys.stderr)
            return EXIT_USAGE
        if entry.get("state") is not None and not isinstance(entry["state"], dict):
            print(f"error: {entry['eval_id']}: `state` must be an object (it becomes "
                  "session_input.state)", file=sys.stderr)
            return EXIT_USAGE
        if not entry.get("turns") and not entry.get("scenario"):
            print(f"error: {entry['eval_id']}: needs `turns` or `scenario`", file=sys.stderr)
            return EXIT_USAGE
        for turn in entry.get("turns") or []:
            for tool in (turn.get("tools") or []) if isinstance(turn, dict) else []:
                if not isinstance(tool, dict) or not (tool.get("name") or tool.get("skill")):
                    print(f"error: {entry['eval_id']}: every tool needs a `name` "
                          "(or `skill` for a load_skill activation)", file=sys.stderr)
                    return EXIT_USAGE

    layout = detect_layout(target)
    app_name = args.app_name or layout.expected_app_name() or "app"

    if args.evalset:
        evalset_path = Path(args.evalset)
    else:
        evalset_path = default_evalset_path(layout, args.new, target)

    if evalset_path.exists():
        loaded = load_evalset(evalset_path)
        if loaded.fmt != "evalset" or not isinstance(loaded.doc, dict):
            print(f"error: {evalset_path} is not an EvalSet ({loaded.error or loaded.fmt}); "
                  "migrate it first with migrate_legacy.py", file=sys.stderr)
            return EXIT_USAGE
        document = loaded.doc
    else:
        stem = evalset_path.name.replace(".evalset.json", "").replace(".test.json", "")
        document = {
            "eval_set_id": sanitize_eval_set_id(args.new or stem),
            "name": stem.replace("_", " ").title(),
            "eval_cases": [],
        }

    existing = {case.get("eval_id") for case in document.get("eval_cases", [])}
    clashing = [e["eval_id"] for e in entries if e["eval_id"] in existing]
    if clashing:
        print(f"error: these ids already exist in {evalset_path.name}: {', '.join(clashing)}.\n"
              "An existing case is a specification; add a differently named case, or change "
              "that one deliberately.", file=sys.stderr)
        return EXIT_FINDINGS

    facts = extract_module(layout.agent_dir) if layout.agent_dir else None
    load_skill_tool = "load_skill"
    if facts is not None and len(facts.skill_toolsets) == 1 and facts.skill_toolsets[0].tool_name_prefix:
        load_skill_tool = f"{facts.skill_toolsets[0].tool_name_prefix}_load_skill"
    added = [build_case(entry, app_name=app_name, user_id=args.user_id,
                        load_skill_tool=load_skill_tool) for entry in entries]
    document.setdefault("eval_cases", []).extend(added)

    known_tools = facts.bound_tool_names if facts is not None else None
    reference_metrics = True
    for candidate in layout.configs:
        if Path(candidate).name in ("test_config.json", "eval_config.json"):
            loaded_config = load_config(candidate)
            if loaded_config.ok:
                reference_metrics = loaded_config.has_reference_metrics()
            break

    report = GapReport(subject=str(evalset_path), skill="eval-create")
    validate_evalset(
        LoadedEvalSet(str(evalset_path), document, document, "evalset"),
        report=report, expected_app_name=app_name,
        known_tools=known_tools or None, reference_metrics=reference_metrics,
        # The resolver knows toolset tools (`load_skill`) are real; a bare name
        # set does not, and would report every activation case as EVS036.
        tool_resolver=tool_resolver(facts) if facts is not None else None,
        registered_skills=facts.registered_skill_names if facts is not None else None,
        state_key_read=_state_key_read(facts),
    )

    plan = WritePlan()
    plan.add(evalset_path, dumps_json(document), force=True,
             reason=f"adds {len(added)} case(s); existing cases untouched")

    summary = {
        "evalset": str(evalset_path),
        "added": [{"eval_id": c["eval_id"], "category": case_category(c["eval_id"]),
                   "turns": len(c.get("conversation", []))} for c in added],
        "total_cases": stats(document)["cases"],
        "by_category": stats(document)["by_category"],
    }

    written = [] if args.dry_run else plan.apply()

    if args.json:
        print(json.dumps({"summary": summary, "write_plan": plan.to_dict(),
                          "written": written, "validation": report.to_dict()}, indent=2))
    else:
        for case in summary["added"]:
            print(f"+ {case['eval_id']}  ({case['category']}, {case['turns']} turn(s))")
        print(f"{evalset_path.name}: {summary['total_cases']} case(s) total "
              f"{summary['by_category']}")
        print()
        print(plan.to_md())
        if report.findings:
            print()
            print(report.to_md())

        for path in written:
            print(f"wrote {path}")

    return EXIT_FINDINGS if report.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
