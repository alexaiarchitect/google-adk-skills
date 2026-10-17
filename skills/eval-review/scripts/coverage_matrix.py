#!/usr/bin/env python3
"""Map an agent's instruction to the eval cases that exercise it.

The instruction is the specification: each conditional and each prohibition is a
branch the agent can take, and a branch with no case is behaviour nobody is
checking. This script reads the instruction into rules, matches cases against
them by the tools they call and the words they share, and reports:

* **branch coverage** - N branches need at least N+1 cases (one per branch, plus
  the unobstructed happy path),
* **suite shape** - roughly 70% narrow single-turn, 20% multi-turn, 10%
  adversarial,
* **tool coverage** - tools no case ever expects, and calls to tools that do not
  exist;
* **skill activation** - for an agent with a `SkillToolset`, registered skills
  no case expects to be loaded with `load_skill` (EVS073).

Matching is a heuristic, so the matrix is evidence for a human decision, never a
pass or fail.

    coverage_matrix.py <agent-dir> [--evalset FILE ...] [--format json|md]

Exit codes: 0 (gaps are advice), 1 errors or gaps with --strict, 2 bad target.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.coverage import (  # noqa: E402
    extract_sop_rules, matrix, n_plus_one_gaps, shape, skill_coverage, summarize_cases,
    tool_coverage,
)
from adk_skills.evalset import load_evalset  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.tools_ast import LLM_AGENT_CALLS, extract_module, tool_resolver  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent directory or project root")
    parser.add_argument("--evalset", action="append", default=[],
                        help="evalset to consider (repeatable; defaults to everything found)")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 on coverage gaps (by default gaps are advice, exit 0)")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(target)
    if not layout.agent_dir:
        print(f"error: no ADK agent under {target}", file=sys.stderr)
        return EXIT_USAGE

    facts = extract_module(layout.agent_dir)
    agent = facts.root_agent()
    dynamic = bool(agent and getattr(agent, "instruction_is_dynamic", False))
    llm_root = bool(agent and agent.call in LLM_AGENT_CALLS)
    rules = extract_sop_rules(agent.spec_text if agent else None, facts.bound_tool_names)

    paths = [Path(p) for p in (args.evalset or layout.evalsets)]
    docs = {}
    for path in paths:
        loaded = load_evalset(path)
        if loaded.fmt == "evalset" and isinstance(loaded.doc, dict):
            docs[str(path)] = loaded.doc
    cases = summarize_cases(docs)

    grid = matrix(rules, cases)
    gaps = n_plus_one_gaps(rules, cases)
    suite_shape = shape(cases)
    tools = tool_coverage(facts.bound_tool_names, cases, resolver=tool_resolver(facts))
    skills = skill_coverage(facts.registered_skill_names, docs)

    report = GapReport(subject=str(target), skill="eval-review")
    report.discovered = {
        "agent": agent.name if agent else None,
        "instruction_chars": agent.instruction_len if agent else 0,
        "rules": len(rules),
        "branches": gaps["branches"],
        "cases": gaps["cases"],
        "minimum_cases": gaps["minimum_cases"],
        "evalsets": [Path(p).name for p in docs],
        "shape": suite_shape["ratios"],
        "tools": tools,
        "skills": skills,
        "instruction_dynamic": dynamic,
        "root_is_llm_agent": llm_root,
    }

    if dynamic:
        report.add("EVS071", "info", "coverage",
                   "the instruction is built at runtime (an InstructionProvider), so rule "
                   "coverage cannot be computed statically",
                   fix="run the agent once and review the rendered instruction by hand")
    elif not rules and llm_root:
        report.add("EVS070", "warn", "coverage",
                   "the agent's instruction yields no testable rules",
                   fix="an instruction with no ordering or refusal rules cannot be checked; "
                       "state what must happen, in what order, and when to refuse")
    if len(rules) > 40:
        report.add("EVS072", "info", "coverage",
                   f"{len(rules)} rules extracted: the instruction reads as prose rather than "
                   "an SOP, so the branch count is inflated",
                   fix="treat the matrix as a reading aid here, not a coverage target")

    if gaps["branches"] and gaps["shortfall"]:
        report.add_gap(
            "GAP-BRANCH", f"{gaps['shortfall']} case(s) short of branch coverage",
            f"{gaps['branches']} branches need at least {gaps['minimum_cases']} cases; "
            f"there are {gaps['cases']}",
            "add one case per uncovered branch with /adk:eval-create",
        )

    for rule in grid.uncovered_branches():
        report.add_gap(
            f"GAP-RULE-{rule.index}", f"rule {rule.index} has no case",
            f"{rule.kind}: {rule.text}",
            "write a case whose expected trajectory shows this rule being honoured",
        )

    for note in suite_shape.get("notes", []):
        report.add_gap("GAP-SHAPE", "Suite shape", note,
                       "rebalance with /adk:eval-create")

    if tools["never_exercised"]:
        report.add_gap(
            "GAP-TOOLS", f"{len(tools['never_exercised'])} tool(s) never exercised",
            f"no case expects a call to: {', '.join(tools['never_exercised'])}",
            "add a case per unexercised tool, or remove the tool from the agent",
        )
    # With no case at all, GAP-EVALSET already says everything; EVS073 needs a suite.
    if skills and skills["never_activated"] and cases:
        report.add(
            "EVS073", "warn", "coverage",
            f"{len(skills['never_activated'])} registered skill(s) no case activates: "
            f"{', '.join(skills['never_activated'])}",
            fix="add an activation case per skill: a request the skill's description should "
                "trigger, expecting `load_skill` with {\"skill_name\": \"<name>\"} before the "
                "skill's own tool calls (gen_cases.py accepts {\"skill\": \"<name>\"} in a turn)",
            source="adk-python: tools/skill_toolset.py (LoadSkillTool)",
        )
    for unknown in tools["unknown_in_cases"]:
        report.add("EVS036", "error", "coverage",
                   f"cases expect {unknown!r}, which the agent does not define",
                   fix="fix the name, or add the tool")

    if args.format == "json":
        payload = report.to_dict()
        payload["matrix"] = grid.to_dict()
        payload["shape"] = suite_shape
        payload["n_plus_one"] = gaps
        print(json.dumps(payload, indent=2))
    else:
        print(report.to_md())
        print()
        print("## Rule coverage")
        print()
        print(grid.to_md())

    if report.has_errors():
        return EXIT_FINDINGS
    if args.strict and report.gaps:
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
