#!/usr/bin/env python3
"""Curate an eval suite that already exists: list, rename, move, split, dedupe.

Writing the first cases is the part everyone does. What decides whether a suite
survives a year is what happens afterwards: a case named `test_3` that nobody
can interpret when CI goes red, the same request pasted into three files, a
multi-concern case that fails without saying which concern broke, an
`app_name` left behind when the agent directory was renamed.

Doing that by hand in a JSON file is where suites get quietly damaged, because
the edit that looks safest - adjusting an existing case until the run passes -
is the one that destroys the specification. So every operation here is
mechanical, reversible by reading a diff, and refuses the two moves that lose
information:

* an `eval_id` is never silently replaced by another case (rename, move and
  split all stop when the destination id is taken);
* a legacy-format file is never rewritten, because the conversion is lossy
  enough to deserve its own command (`migrate_legacy.py`).

Nothing is written until you have seen the write plan, and `--dry-run` shows it
without touching anything.

    manage_cases.py list            [path] [--evalset F] [--by-category]
    manage_cases.py show            <eval_id> [path] [--evalset F]
    manage_cases.py rename          <eval_id> <new_id> [path] [--evalset F]
    manage_cases.py move            <eval_id> --to <evalset> [path] [--evalset F]
    manage_cases.py remove          <eval_id> [path] [--evalset F]
    manage_cases.py dedupe          [path] [--evalset F] [--apply]
    manage_cases.py split           <eval_id> [path] [--evalset F]
    manage_cases.py rebase-app-name <name> [path] [--evalset F]

Exit codes: 0 done, 1 refused (id clash, duplicates found, validation errors),
2 usage or environment (no such target, no such case, legacy file, ambiguous
evalset).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.evalset import (  # noqa: E402
    LoadedEvalSet, case_category, iter_invocations, load_evalset,
    sanitize_eval_set_id, stats, text_of, tool_sequence, validate_evalset,
)
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import (  # noqa: E402
    EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport,
)
from adk_skills.templating import WritePlan, dumps_json  # noqa: E402
from adk_skills.tools_ast import extract_module  # noqa: E402

LEGACY_HINT = (
    "legacy list format: converting it is lossy enough to be its own step. "
    "Run migrate_legacy.py first, then manage the result."
)


# ---------------------------------------------------------------------------
# resolving which file(s) to work on
# ---------------------------------------------------------------------------

def collect_evalsets(path: str, explicit: str | None) -> tuple[list[str], Any]:
    """Every evalset in scope, plus the layout it was found through."""
    if explicit:
        return [explicit], detect_layout(path)
    target = Path(path)
    if target.is_file():
        return [str(target)], detect_layout(target.parent)
    layout = detect_layout(target)
    return list(layout.evalsets), layout


def load_one(candidates: list[str], *, for_write: bool) -> tuple[LoadedEvalSet | None, str | None]:
    """Exactly one evalset, or an explanation of why that is not possible."""
    if not candidates:
        return None, "no evalset found; pass --evalset, or write cases with gen_cases.py"
    if len(candidates) > 1:
        names = ", ".join(Path(c).name for c in candidates)
        return None, f"{len(candidates)} evalsets in scope ({names}); pick one with --evalset"
    loaded = load_evalset(candidates[0])
    if loaded.fmt == "invalid":
        return None, f"{candidates[0]}: {loaded.error}"
    if loaded.is_legacy:
        return None, f"{candidates[0]}: {LEGACY_HINT}"
    if for_write and not isinstance(loaded.doc, dict):
        return None, f"{candidates[0]}: not an EvalSet document"
    return loaded, None


def find_case(doc: dict[str, Any], eval_id: str) -> tuple[int, dict[str, Any]] | None:
    for index, case in enumerate(doc.get("eval_cases") or []):
        if isinstance(case, dict) and case.get("eval_id") == eval_id:
            return index, case
    return None


def existing_ids(doc: dict[str, Any]) -> set[str]:
    return {
        case.get("eval_id")
        for case in doc.get("eval_cases") or []
        if isinstance(case, dict) and case.get("eval_id")
    }


# ---------------------------------------------------------------------------
# validation of a mutated document
# ---------------------------------------------------------------------------

def revalidate(path: str, doc: dict[str, Any], layout: Any) -> GapReport:
    """Check the result of an edit the same way eval-review would."""
    report = GapReport(subject=path, skill="eval-create")
    known: set[str] | None = None
    if getattr(layout, "agent_dir", None):
        names = extract_module(layout.agent_dir).bound_tool_names
        known = names or None
    validate_evalset(
        LoadedEvalSet(path, doc, doc, "evalset"),
        report=report,
        expected_app_name=layout.expected_app_name() if layout else None,
        known_tools=known,
        reference_metrics=False,
    )
    return report


# ---------------------------------------------------------------------------
# read-only subcommands
# ---------------------------------------------------------------------------

def case_row(case: dict[str, Any]) -> dict[str, Any]:
    eval_id = str(case.get("eval_id", "?"))
    turns = list(iter_invocations(case))
    return {
        "eval_id": eval_id,
        "category": case_category(eval_id),
        "turns": len(turns),
        "tools": tool_sequence(case),
        "scenario": bool(case.get("conversation_scenario")),
        "app_name": (case.get("session_input") or {}).get("app_name"),
        "first_user_text": text_of(turns[0].get("user_content")) if turns else "",
    }


def cmd_list(args: argparse.Namespace) -> int:
    candidates, layout = collect_evalsets(args.path, args.evalset)
    if not candidates:
        print("error: no evalset found; pass --evalset or a path that holds one",
              file=sys.stderr)
        return EXIT_USAGE

    files: list[dict[str, Any]] = []
    for candidate in candidates:
        loaded = load_evalset(candidate)
        if loaded.fmt == "invalid":
            files.append({"evalset": candidate, "error": loaded.error, "cases": []})
            continue
        if loaded.is_legacy:
            files.append({"evalset": candidate, "error": LEGACY_HINT, "cases": []})
            continue
        doc = loaded.doc
        rows = [case_row(c) for c in doc.get("eval_cases") or [] if isinstance(c, dict)]
        files.append({
            "evalset": candidate,
            "eval_set_id": doc.get("eval_set_id"),
            "stats": stats(doc),
            "cases": rows,
        })

    if args.json:
        print(json.dumps({"files": files, "by_category": args.by_category}, indent=2))
        return EXIT_OK

    for entry in files:
        print(f"# {Path(entry['evalset']).name}"
              + (f"  ({entry.get('eval_set_id')})" if entry.get("eval_set_id") else ""))
        if entry.get("error"):
            print(f"  ! {entry['error']}")
            print()
            continue
        rows = entry["cases"]
        if not rows:
            print("  (no cases)")
        elif args.by_category:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(row["category"], []).append(row)
            for category in sorted(grouped):
                print(f"  {category} ({len(grouped[category])})")
                for row in grouped[category]:
                    print(f"    {row['eval_id']}  {row['turns']} turn(s)"
                          f"  [{', '.join(row['tools']) or 'no tools'}]")
        else:
            for row in rows:
                print(f"  {row['eval_id']}  ({row['category']}, {row['turns']} turn(s))"
                      f"  [{', '.join(row['tools']) or 'no tools'}]")
        print()
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    candidates, layout = collect_evalsets(args.path, args.evalset)
    loaded, problem = load_one(candidates, for_write=False)
    if loaded is None:
        # `show` can search several files, so only complain when nothing matched.
        found = None
        for candidate in candidates:
            other = load_evalset(candidate)
            if other.fmt == "evalset" and find_case(other.doc, args.eval_id):
                found = other
                break
        if found is None:
            print(f"error: {problem}", file=sys.stderr)
            return EXIT_USAGE
        loaded = found
    hit = find_case(loaded.doc, args.eval_id)
    if hit is None:
        print(f"error: no case {args.eval_id!r} in {Path(loaded.path).name}", file=sys.stderr)
        return EXIT_USAGE

    index, case = hit
    turns = []
    for position, invocation in enumerate(iter_invocations(case), start=1):
        data = invocation.get("intermediate_data") or {}
        turns.append({
            "turn": position,
            "user": text_of(invocation.get("user_content")),
            "reference": text_of(invocation.get("final_response")),
            "tool_uses": data.get("tool_uses") or [],
        })
    payload = {
        "evalset": loaded.path,
        "index": index,
        "eval_id": args.eval_id,
        "category": case_category(args.eval_id),
        "session_input": case.get("session_input"),
        "conversation_scenario": case.get("conversation_scenario"),
        "rubrics": case.get("rubrics") or [],
        "turns": turns,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return EXIT_OK

    print(f"{args.eval_id}  ({payload['category']})  in {Path(loaded.path).name} "
          f"at eval_cases[{index}]")
    session = payload["session_input"] or {}
    print(f"session: app_name={session.get('app_name')!r} user_id={session.get('user_id')!r} "
          f"state keys={sorted((session.get('state') or {}))}")
    if payload["conversation_scenario"]:
        scenario = payload["conversation_scenario"]
        print("scenario:")
        print(f"  starting_prompt: {scenario.get('starting_prompt')}")
        print(f"  conversation_plan: {scenario.get('conversation_plan')}")
        print(f"  user_persona: {scenario.get('user_persona')}")
    for turn in turns:
        print()
        print(f"turn {turn['turn']}")
        print(f"  user:      {turn['user']}")
        for tool in turn["tool_uses"]:
            print(f"  calls:     {tool.get('name')}({json.dumps(tool.get('args') or {})})")
        print(f"  reference: {turn['reference'] or '(empty: trajectory-only case)'}")
    if payload["rubrics"]:
        print()
        print(f"rubrics: {len(payload['rubrics'])}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# mutating subcommands
# ---------------------------------------------------------------------------

def finish(
    args: argparse.Namespace,
    *,
    plan: WritePlan,
    summary: dict[str, Any],
    reports: list[GapReport],
) -> int:
    errors = sum(r.count("error") for r in reports)
    written = [] if args.dry_run else plan.apply()

    if args.json:
        print(json.dumps({
            "summary": summary,
            "write_plan": plan.to_dict(),
            "written": written,
            "validation": [r.to_dict() for r in reports],
        }, indent=2))
    else:
        for line in summary.get("lines", []):
            print(line)
        print()
        print(plan.to_md())
        for report in reports:
            if report.findings:
                print()
                print(report.to_md())
        if args.dry_run:
            print()
            print("--dry-run: nothing was written")
        for path in written:
            print(f"wrote {path}")
    return EXIT_FINDINGS if errors else EXIT_OK


def cmd_rename(args: argparse.Namespace) -> int:
    candidates, layout = collect_evalsets(args.path, args.evalset)
    loaded, problem = load_one(candidates, for_write=True)
    if loaded is None:
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_USAGE
    doc = loaded.doc
    hit = find_case(doc, args.eval_id)
    if hit is None:
        print(f"error: no case {args.eval_id!r} in {Path(loaded.path).name}", file=sys.stderr)
        return EXIT_USAGE
    if args.new_id in existing_ids(doc):
        print(f"error: {args.new_id!r} already names a case in {Path(loaded.path).name}. "
              "An eval id addresses a specification; pick a name that is free.",
              file=sys.stderr)
        return EXIT_FINDINGS

    index, case = hit
    case["eval_id"] = args.new_id
    # Invocation ids that embedded the old name would otherwise disagree with it.
    for position, invocation in enumerate(iter_invocations(case), start=1):
        current = invocation.get("invocation_id")
        if isinstance(current, str) and current.startswith(args.eval_id):
            invocation["invocation_id"] = f"{args.new_id}_turn_{position}"

    plan = WritePlan()
    plan.add(loaded.path, dumps_json(doc), force=True,
             reason=f"renames eval_cases[{index}] {args.eval_id} -> {args.new_id}")
    report = revalidate(loaded.path, doc, layout)
    return finish(args, plan=plan, summary={
        "action": "rename", "evalset": loaded.path,
        "from": args.eval_id, "to": args.new_id,
        "category_before": case_category(args.eval_id),
        "category_after": case_category(args.new_id),
        "lines": [f"{args.eval_id} -> {args.new_id} "
                  f"({case_category(args.eval_id)} -> {case_category(args.new_id)})"],
    }, reports=[report])


def cmd_move(args: argparse.Namespace) -> int:
    candidates, layout = collect_evalsets(args.path, args.evalset)
    loaded, problem = load_one(candidates, for_write=True)
    if loaded is None:
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_USAGE
    source = loaded.doc
    hit = find_case(source, args.eval_id)
    if hit is None:
        print(f"error: no case {args.eval_id!r} in {Path(loaded.path).name}", file=sys.stderr)
        return EXIT_USAGE

    destination_path = Path(args.to)
    if destination_path.exists():
        destination_loaded = load_evalset(destination_path)
        if destination_loaded.fmt == "invalid":
            print(f"error: {destination_path}: {destination_loaded.error}", file=sys.stderr)
            return EXIT_USAGE
        if destination_loaded.is_legacy:
            print(f"error: {destination_path}: {LEGACY_HINT}", file=sys.stderr)
            return EXIT_USAGE
        destination = destination_loaded.doc
    else:
        stem = destination_path.name.replace(".evalset.json", "").replace(".test.json", "")
        destination = {
            "eval_set_id": sanitize_eval_set_id(stem),
            "name": stem.replace("_", " ").title(),
            "eval_cases": [],
        }

    if args.eval_id in existing_ids(destination):
        print(f"error: {destination_path.name} already has a case named {args.eval_id!r}",
              file=sys.stderr)
        return EXIT_FINDINGS

    index, case = hit
    source["eval_cases"].pop(index)
    destination.setdefault("eval_cases", []).append(case)

    plan = WritePlan()
    plan.add(loaded.path, dumps_json(source), force=True,
             reason=f"removes {args.eval_id} (moved out)")
    plan.add(destination_path, dumps_json(destination), force=True,
             reason=f"receives {args.eval_id}")
    reports = [
        revalidate(loaded.path, source, layout),
        revalidate(str(destination_path), destination, layout),
    ]
    return finish(args, plan=plan, summary={
        "action": "move", "eval_id": args.eval_id,
        "from": loaded.path, "to": str(destination_path),
        "lines": [f"{args.eval_id}: {Path(loaded.path).name} -> {destination_path.name}"],
    }, reports=reports)


def cmd_remove(args: argparse.Namespace) -> int:
    candidates, layout = collect_evalsets(args.path, args.evalset)
    loaded, problem = load_one(candidates, for_write=True)
    if loaded is None:
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_USAGE
    doc = loaded.doc
    hit = find_case(doc, args.eval_id)
    if hit is None:
        print(f"error: no case {args.eval_id!r} in {Path(loaded.path).name}", file=sys.stderr)
        return EXIT_USAGE

    index, case = hit
    doc["eval_cases"].pop(index)

    plan = WritePlan()
    plan.add(loaded.path, dumps_json(doc), force=True,
             reason=f"removes eval_cases[{index}] {args.eval_id}")
    report = revalidate(loaded.path, doc, layout)
    lines = [f"- {args.eval_id} ({case_category(args.eval_id)}) removed; "
             f"{len(doc['eval_cases'])} case(s) left"]
    if not doc["eval_cases"]:
        lines.append("the evalset now asserts nothing; add a case before running it")
    return finish(args, plan=plan, summary={
        "action": "remove", "evalset": loaded.path, "eval_id": args.eval_id,
        "remaining": len(doc["eval_cases"]), "lines": lines,
    }, reports=[report])


def duplicate_signature(case: dict[str, Any]) -> str:
    """Two cases are duplicates when they say the same thing to the agent and
    expect the same calls with the same arguments."""
    turns = []
    for invocation in iter_invocations(case):
        data = invocation.get("intermediate_data") or {}
        calls = [
            (tool.get("name"), json.dumps(tool.get("args") or {}, sort_keys=True))
            for tool in data.get("tool_uses") or []
            if isinstance(tool, dict)
        ]
        turns.append((text_of(invocation.get("user_content")).strip(), tuple(calls)))
    scenario = case.get("conversation_scenario") or {}
    return json.dumps({
        "turns": turns,
        "scenario": [scenario.get("starting_prompt"), scenario.get("conversation_plan")],
    }, sort_keys=True)


def cmd_dedupe(args: argparse.Namespace) -> int:
    candidates, layout = collect_evalsets(args.path, args.evalset)
    if not candidates:
        print("error: no evalset found; pass --evalset or a path that holds one",
              file=sys.stderr)
        return EXIT_USAGE

    groups: dict[str, list[tuple[str, str]]] = {}
    documents: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        loaded = load_evalset(candidate)
        if loaded.fmt != "evalset" or not isinstance(loaded.doc, dict):
            continue
        documents[candidate] = loaded.doc
        for case in loaded.doc.get("eval_cases") or []:
            if not isinstance(case, dict) or not case.get("eval_id"):
                continue
            groups.setdefault(duplicate_signature(case), []).append(
                (candidate, str(case["eval_id"]))
            )

    duplicates = [members for members in groups.values() if len(members) > 1]
    payload_groups = [
        {"keep": members[0][1], "keep_in": members[0][0],
         "drop": [{"eval_id": eid, "evalset": path} for path, eid in members[1:]]}
        for members in duplicates
    ]

    if not duplicates:
        if args.json:
            print(json.dumps({"action": "dedupe", "duplicate_groups": [], "removed": []}, indent=2))
        else:
            print(f"no duplicates across {len(candidates)} evalset(s)")
        return EXIT_OK

    if not args.apply:
        if args.json:
            print(json.dumps({"action": "dedupe", "duplicate_groups": payload_groups,
                              "removed": [], "applied": False}, indent=2))
        else:
            print(f"{len(duplicates)} duplicate group(s):")
            for group in payload_groups:
                dropped = ", ".join(d["eval_id"] for d in group["drop"])
                print(f"  keep {group['keep']}  ->  duplicate of it: {dropped}")
            print()
            print("Two cases with the same input and the same expected calls cost twice "
                  "and prove once. Re-run with --apply to keep the first of each group.")
        return EXIT_FINDINGS

    removed: list[dict[str, str]] = []
    for group in payload_groups:
        for entry in group["drop"]:
            doc = documents[entry["evalset"]]
            hit = find_case(doc, entry["eval_id"])
            if hit is not None:
                doc["eval_cases"].pop(hit[0])
                removed.append(entry)

    touched = sorted({entry["evalset"] for entry in removed})
    plan = WritePlan()
    reports: list[GapReport] = []
    for path in touched:
        count = sum(1 for entry in removed if entry["evalset"] == path)
        plan.add(path, dumps_json(documents[path]), force=True,
                 reason=f"removes {count} duplicate case(s)")
        reports.append(revalidate(path, documents[path], layout))

    return finish(args, plan=plan, summary={
        "action": "dedupe", "duplicate_groups": payload_groups,
        "removed": removed, "applied": True,
        "lines": [f"- removed {entry['eval_id']} from {Path(entry['evalset']).name}"
                  for entry in removed],
    }, reports=reports)


def cmd_split(args: argparse.Namespace) -> int:
    candidates, layout = collect_evalsets(args.path, args.evalset)
    loaded, problem = load_one(candidates, for_write=True)
    if loaded is None:
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_USAGE
    doc = loaded.doc
    hit = find_case(doc, args.eval_id)
    if hit is None:
        print(f"error: no case {args.eval_id!r} in {Path(loaded.path).name}", file=sys.stderr)
        return EXIT_USAGE

    index, case = hit
    turns = list(iter_invocations(case))
    if len(turns) < 2:
        print(f"error: {args.eval_id!r} has {len(turns)} turn(s); there is nothing to split. "
              "Splitting separates the turns of a multi-turn case into one case each.",
              file=sys.stderr)
        return EXIT_USAGE

    new_ids = [f"{args.eval_id}_turn_{position}" for position in range(1, len(turns) + 1)]
    taken = sorted(set(new_ids) & existing_ids(doc))
    if taken:
        print(f"error: these ids already exist in {Path(loaded.path).name}: {', '.join(taken)}",
              file=sys.stderr)
        return EXIT_FINDINGS

    parts: list[dict[str, Any]] = []
    for position, (new_id, invocation) in enumerate(zip(new_ids, turns), start=1):
        part: dict[str, Any] = {"eval_id": new_id}
        turn = json.loads(json.dumps(invocation))
        turn["invocation_id"] = f"{new_id}_inv_1"
        part["conversation"] = [turn]
        if case.get("session_input") is not None:
            part["session_input"] = json.loads(json.dumps(case["session_input"]))
        if case.get("rubrics"):
            part["rubrics"] = json.loads(json.dumps(case["rubrics"]))
        parts.append(part)

    doc["eval_cases"][index:index + 1] = parts

    plan = WritePlan()
    plan.add(loaded.path, dumps_json(doc), force=True,
             reason=f"splits {args.eval_id} into {len(parts)} single-turn case(s)")
    report = revalidate(loaded.path, doc, layout)
    lines = [f"{args.eval_id} -> " + ", ".join(new_ids)]
    lines.append("each part keeps the original session_input, so state-dependent turns "
                 "still start from the same place - check that a later turn does not "
                 "depend on an earlier turn's answer")
    return finish(args, plan=plan, summary={
        "action": "split", "evalset": loaded.path, "eval_id": args.eval_id,
        "parts": new_ids, "lines": lines,
    }, reports=[report])


def cmd_rebase_app_name(args: argparse.Namespace) -> int:
    candidates, layout = collect_evalsets(args.path, args.evalset)
    if not candidates:
        print("error: no evalset found; pass --evalset or a path that holds one",
              file=sys.stderr)
        return EXIT_USAGE

    plan = WritePlan()
    reports: list[GapReport] = []
    changed: list[dict[str, Any]] = []
    blocked: list[str] = []

    for candidate in candidates:
        loaded = load_evalset(candidate)
        if loaded.fmt != "evalset" or not isinstance(loaded.doc, dict):
            blocked.append(f"{candidate}: {loaded.error or LEGACY_HINT}")
            continue
        doc = loaded.doc
        touched = 0
        for case in doc.get("eval_cases") or []:
            if not isinstance(case, dict):
                continue
            session = case.setdefault("session_input", {})
            if not isinstance(session, dict):
                continue
            if session.get("app_name") != args.name:
                session["app_name"] = args.name
                touched += 1
        if touched:
            plan.add(candidate, dumps_json(doc), force=True,
                     reason=f"sets session_input.app_name to {args.name!r} on {touched} case(s)")
            reports.append(revalidate(candidate, doc, layout))
            changed.append({"evalset": candidate, "cases": touched})

    expected = layout.expected_app_name() if layout else None
    lines = [f"- {Path(entry['evalset']).name}: {entry['cases']} case(s)" for entry in changed]
    if not changed:
        lines.append(f"every case already declares app_name {args.name!r}")
    if expected and expected != args.name:
        lines.append(f"note: the agent directory resolves to {expected!r}, not {args.name!r}; "
                     "ADK aligns the runner against the directory name")
    for entry in blocked:
        lines.append(f"skipped {entry}")

    return finish(args, plan=plan, summary={
        "action": "rebase-app-name", "app_name": args.name,
        "changed": changed, "skipped": blocked,
        "expected_app_name": expected, "lines": lines,
    }, reports=reports)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Declared here too, so `--help` advertises them; the subparsers suppress
    # their own defaults so a global `--json` is not overwritten by a subcommand.
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the write plan and change nothing")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--evalset", help="the evalset to act on, when several exist")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    listing = subparsers.add_parser("list", parents=[common], help="every case, with its category")
    listing.add_argument("--by-category", action="store_true", help="group by category")
    listing.add_argument("path", nargs="?", default=".",
                       help="agent directory, project root, or an evalset file")
    listing.set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", parents=[common], help="one case in full")
    show.add_argument("eval_id")
    show.add_argument("path", nargs="?", default=".",
                       help="agent directory, project root, or an evalset file")
    show.set_defaults(func=cmd_show)

    rename = subparsers.add_parser("rename", parents=[common], help="give a case a better name")
    rename.add_argument("eval_id")
    rename.add_argument("new_id")
    rename.add_argument("path", nargs="?", default=".",
                       help="agent directory, project root, or an evalset file")
    rename.set_defaults(func=cmd_rename)

    move = subparsers.add_parser("move", parents=[common], help="move a case to another evalset")
    move.add_argument("eval_id")
    move.add_argument("--to", required=True, help="destination evalset (created if absent)")
    move.add_argument("path", nargs="?", default=".",
                       help="agent directory, project root, or an evalset file")
    move.set_defaults(func=cmd_move)

    remove = subparsers.add_parser("remove", parents=[common], help="delete a case")
    remove.add_argument("eval_id")
    remove.add_argument("path", nargs="?", default=".",
                       help="agent directory, project root, or an evalset file")
    remove.set_defaults(func=cmd_remove)

    dedupe = subparsers.add_parser("dedupe", parents=[common],
                                   help="find cases with identical input and trajectory")
    dedupe.add_argument("--apply", action="store_true",
                        help="keep the first of each group and remove the rest")
    dedupe.add_argument("path", nargs="?", default=".",
                       help="agent directory, project root, or an evalset file")
    dedupe.set_defaults(func=cmd_dedupe)

    split = subparsers.add_parser("split", parents=[common],
                                  help="turn a multi-turn case into one case per turn")
    split.add_argument("eval_id")
    split.add_argument("path", nargs="?", default=".",
                       help="agent directory, project root, or an evalset file")
    split.set_defaults(func=cmd_split)

    rebase = subparsers.add_parser("rebase-app-name", parents=[common],
                                   help="set session_input.app_name on every case")
    rebase.add_argument("name")
    rebase.add_argument("path", nargs="?", default=".",
                       help="agent directory, project root, or an evalset file")
    rebase.set_defaults(func=cmd_rebase_app_name)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    if not Path(args.path).exists():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return EXIT_USAGE
    args.dry_run = getattr(args, "dry_run", False)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
