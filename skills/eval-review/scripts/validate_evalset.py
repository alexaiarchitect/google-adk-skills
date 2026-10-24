#!/usr/bin/env python3
"""Validate ADK evalsets, and cross-check them against the agent they describe.

Two kinds of problem, and the second is the reason this exists:

* **Schema** - things ADK will reject, such as an `eval_set_id` with a dash or a
  case that sets both `conversation` and `conversation_scenario`.
* **Silent** - things ADK accepts and ignores. Eval cases allow unknown keys, so
  a misspelled field raises nothing and changes nothing; an expected call to a
  tool that does not exist can never match; a per-case rubric without a `type`
  is read by no criterion.

    validate_evalset.py <file | directory | agent-dir> [--agent-dir DIR]
                        [--config FILE] [--adk-version V] [--format json|md] [--strict]

`--adk-version` names the release that will run the suite. A rubric without
`text_property` is then an error below 2.12 (EVS082); without it, or on 2.12+,
it is a warning. When the agent registers skills through a `SkillToolset`, a
case expecting `load_skill` for a skill it does not register is EVS074. A case
whose `session_input.state` seeds a key the agent never reads is EVS075 (info).
An `eval_set_id` or `app_name` that starts with a drive letter (`c:...`) is
EVS076: an error on 2.13+ (ADK raises when it saves the results), a warning
below or when the version is unknown.

Exit codes: 0 clean, 1 findings, 2 bad target.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.evalset import load_evalset, stats, validate_evalset  # noqa: E402
from adk_skills.layout import EVALSET_SUFFIXES, detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.tools_ast import collect_state_keys, extract_module, tool_resolver  # noqa: E402


def collect_evalsets(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    layout = detect_layout(target)
    if layout.evalsets:
        return [Path(p) for p in layout.evalsets]
    return sorted(
        p for p in target.rglob("*")
        if p.is_file() and p.name.endswith(EVALSET_SUFFIXES)
    )


def _layout_for(target: Path):
    """The project an evalset belongs to.

    A file under `tests/eval/evalsets/` has its agent and config three levels
    up; reading the layout off the file's own directory would miss both and
    validate against default metrics. Walk up until a project is found.
    """
    start = target if target.is_dir() else target.parent
    layout = detect_layout(start)
    if target.is_dir() or layout.agent_dir:
        return layout
    for parent in list(start.parents)[:4]:
        candidate = detect_layout(parent)
        if candidate.agent_dir:
            return candidate
    return layout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="an evalset file, a directory of them, or an agent directory")
    parser.add_argument("--agent-dir", help="agent to cross-check tool names and app_name against")
    parser.add_argument("--config", help="eval config, to know whether a reference response is needed")
    parser.add_argument("--adk-version",
                        help="the ADK release that will run the suite (e.g. 2.11.0); decides "
                             "whether a rubric without text_property is an error")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    evalsets = collect_evalsets(target)
    if not evalsets:
        print(f"error: no evalset files (*.evalset.json, *.test.json) under {target}", file=sys.stderr)
        return EXIT_USAGE

    # Two layouts, deliberately: the evalset's own project decides which config
    # applies and what app_name a run resolves to, while `--agent-dir` decides
    # whose tools the cases are checked against. Reading both off the agent
    # directory checks an evalset against a config that does not govern it.
    agent_dir = args.agent_dir
    layout = _layout_for(target)
    if agent_dir is None and layout.agent_dir:
        agent_dir = layout.agent_dir

    resolver = None
    known_tools = None
    registered_skills = None
    state_key_read = None
    if agent_dir and Path(agent_dir).exists():
        facts = extract_module(agent_dir)
        resolver = tool_resolver(facts)
        known_tools = facts.bound_tool_names or None
        registered_skills = facts.registered_skill_names
        state_keys = collect_state_keys(facts.files, facts.str_constants)
        # A read through a computed key could be any key: EVS075 stays silent.
        if facts.files and not state_keys.dynamic_reads and not facts.agent_unresolved:
            state_key_read = state_keys.reads

    reference_metrics = True
    match_type = None
    config_path = args.config
    if not config_path:
        for candidate in layout.configs:
            if Path(candidate).name in ("test_config.json", "eval_config.json"):
                config_path = candidate
                break
    if config_path and Path(config_path).exists():
        loaded_config = load_config(config_path)
        if loaded_config.ok:
            reference_metrics = loaded_config.has_reference_metrics()
            match_type = loaded_config.trajectory_match_type()

    def rel(path: str | None) -> str | None:
        if not path:
            return path
        try:
            return str(Path(path).resolve().relative_to(Path(target).resolve().parent))
        except ValueError:
            return Path(path).name

    report = GapReport(subject=str(target), skill="eval-review")
    report.discovered = {
        "evalsets": [rel(str(p)) for p in evalsets],
        "agent_dir": rel(agent_dir),
        "known_tools": sorted(known_tools) if known_tools else None,
        "config": rel(config_path),
        "reference_metrics_configured": reference_metrics,
        "trajectory_match_type": match_type,
        "expected_app_name": layout.expected_app_name(),
        "registered_skills": sorted(registered_skills) if registered_skills is not None else None,
        "adk_version": args.adk_version,
    }

    totals = {"cases": 0, "files": len(evalsets)}
    for path in evalsets:
        loaded = load_evalset(path)
        validate_evalset(
            loaded,
            report=report,
            expected_app_name=layout.expected_app_name(),
            tool_resolver=resolver,
            trajectory_match_type=match_type,
            reference_metrics=reference_metrics,
            adk_version=args.adk_version,
            registered_skills=registered_skills,
            state_key_read=state_key_read,
        )
        if isinstance(loaded.doc, dict):
            totals["cases"] += stats(loaded.doc)["cases"]
            if loaded.is_legacy:
                totals["legacy_files"] = totals.get("legacy_files", 0) + 1
    report.discovered["totals"] = totals

    print(report.render(args.format))
    if report.has_errors():
        return EXIT_FINDINGS
    if args.strict and report.count("warn"):
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
