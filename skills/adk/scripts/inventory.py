#!/usr/bin/env python3
"""Every ADK agent in a repository, on one page, with its evaluation standing.

The single-agent scripts in this plugin answer "what is the state of *this*
agent?". Once a repository holds several - a router and its sub-agents, a
monorepo of services, a samples directory - the question changes to "which of
these is actually covered, and which one should I work on next?". Answering that
by running a discovery script per directory buries the comparison in noise.

So this reports one row per loadable agent: how it is laid out, what app name it
resolves to, how many tools it binds, how many eval cases it has and of what
kind, what its config costs to run, whether anything was ever recorded, and the
maturity level that follows. Read down the maturity column and the next piece of
work names itself.

The ladder is the same one `/adk:eval-ci` uses, recomputed here from the shared
library rather than imported, so the two stay independent:

    L0  nothing replayable states what the agent should do
    L1  cases exist and replay deterministically
    L2  they run automatically, gated, on every change
    L3  reliability is measured across repeated runs
    L4  production failures feed back into the suite

    inventory.py [repo-root] [--format json|md] [--json]

Exit codes: 0 always (this is a report, not a gate), 2 if the root is unusable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.env import detect_backend  # noqa: E402
from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.evalset import load_evalset, migrate_legacy, sanitize_eval_set_id, stats  # noqa: E402
from adk_skills.layout import detect_layout, find_agent_dirs  # noqa: E402
from adk_skills.metrics import DETERMINISTIC_METRICS, cost_tier  # noqa: E402
from adk_skills.report import EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.results import find_result_files, parse_result, result_identity  # noqa: E402
from adk_skills.tools_ast import extract_module  # noqa: E402

LEVEL_NAMES = {
    0: "vibe checks",
    1: "golden traces",
    2: "automated gates",
    3: "statistical reliability",
    4: "continuous observability",
}


def eval_workflows(root: Path) -> tuple[list[str], bool]:
    """Workflow files that mention evaluation, and whether any of them gates.

    A workflow that calls bare `adk eval` is not a gate: that command returns 0
    however the run went, so the step is green forever.
    """
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return [], False
    names: list[str] = []
    gated = False
    for path in sorted(directory.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        if "adk" in path.name.lower() or "eval" in path.name.lower() or "eval" in lowered:
            names.append(path.name)
            if any(marker in text for marker in
                   ("run_evals.py", "run_registry.py", "run_pytest_eval.py", "pytest")):
                gated = True
    return names, gated


def suite_facts(layout: Any) -> dict[str, Any]:
    """Case counts and categories, counting legacy files by normalizing them.

    A legacy `.test.json` still specifies behaviour; refusing to count it would
    grade a project that has evals as though it had none.
    """
    cases = 0
    categories: dict[str, int] = {}
    legacy_files = 0
    unreadable: list[str] = []

    for path in layout.evalsets:
        loaded = load_evalset(path)
        document: dict[str, Any] | None = None
        if loaded.fmt == "evalset" and isinstance(loaded.doc, dict):
            document = loaded.doc
        elif loaded.is_legacy and isinstance(loaded.doc, list):
            legacy_files += 1
            document = migrate_legacy(
                loaded.doc, eval_set_id=sanitize_eval_set_id(Path(path).stem)
            )
        else:
            unreadable.append(path)
        if document is None:
            continue
        summary = stats(document)
        cases += summary["cases"]
        for category, count in summary["by_category"].items():
            categories[category] = categories.get(category, 0) + count

    return {
        "evalsets": len(layout.evalsets),
        "cases": cases,
        "by_category": categories,
        "legacy_files": legacy_files,
        "unreadable": unreadable,
    }


def config_facts(layout: Any) -> dict[str, Any]:
    metrics: list[str] = []
    configs: list[str] = []
    deterministic = False
    judged = False
    for path in layout.configs:
        if Path(path).suffix != ".json":
            continue                      # `.yaml` here is a foreign config
        loaded = load_config(path)
        if not loaded.ok:
            continue
        configs.append(path)
        names = loaded.metric_names()
        metrics.extend(names)
        if any(name in DETERMINISTIC_METRICS for name in names):
            deterministic = True
        if loaded.cost() != "free":
            judged = True
    unique = sorted(set(metrics))
    return {
        "configs": [Path(p).name for p in configs],
        "metrics": unique,
        "cost_tier": cost_tier(unique) if unique else "free",
        "deterministic_config": deterministic,
        "judged_config": judged,
    }


def result_facts(layout: Any) -> dict[str, Any]:
    paths: list[str] = []
    for history in layout.eval_history_dirs:
        paths.extend(find_result_files(history))
    runs_per_set: dict[str, int] = {}
    for path in paths:
        eval_set = result_identity(path).get("eval_set_id") or "?"
        runs_per_set[eval_set] = runs_per_set.get(eval_set, 0) + 1

    latest: dict[str, Any] | None = None
    if paths:
        newest = max(paths, key=lambda p: Path(p).stat().st_mtime)
        run = parse_result(newest)
        if run.ok:
            latest = {
                "file": Path(newest).name,
                "eval_set_id": run.eval_set_id,
                "passed": run.passed,
                "failed": run.failed,
                "not_evaluated": run.not_evaluated,
                "clean": run.is_clean,
            }
    return {
        "result_files": len(paths),
        "max_runs_per_eval_set": max(runs_per_set.values()) if runs_per_set else 0,
        "latest": latest,
    }


def maturity_level(
    suite: dict[str, Any], config: dict[str, Any], results: dict[str, Any],
    *, workflows: list[str], gated: bool,
) -> tuple[int, list[str]]:
    """The ladder, recomputed from facts. Each level requires the one below it."""
    level = 0
    evidence: list[str] = []
    if suite["cases"] > 0:
        level = 1
        evidence.append(f"L1: {suite['cases']} case(s) in {suite['evalsets']} file(s)")
        if config["deterministic_config"]:
            evidence.append("L1: a deterministic config exists")
    if level >= 1 and workflows and gated:
        level = 2
        evidence.append(f"L2: {len(workflows)} eval workflow(s) gated on a real exit code")
    if level >= 2 and results["max_runs_per_eval_set"] >= 3:
        level = 3
        evidence.append(f"L3: up to {results['max_runs_per_eval_set']} runs recorded "
                        "for one eval set")
    if level >= 3 and suite["by_category"].get("regression", 0) > 0:
        level = 4
        evidence.append(f"L4: {suite['by_category']['regression']} regression case(s)")
    return level, evidence


def next_step(level: int, suite: dict[str, Any], config: dict[str, Any]) -> str:
    if level == 0:
        return "write the first happy-path case and one refusal: /adk:eval-create"
    if level == 1:
        if not config["deterministic_config"]:
            return "write a deterministic config: /adk:eval-criteria"
        return "gate it: /adk:eval-ci --tier pr (bare `adk eval` never fails)"
    if level == 2:
        return "measure reliability: /adk:eval-run --runs 5, then /adk:eval-analyze"
    if level == 3:
        return "turn production failures into regression_* cases: /adk:eval-create"
    return "keep the loop running; revisit thresholds as the agent improves"


def inspect_agent(agent_dir: str, repo_root: Path) -> dict[str, Any]:
    layout = detect_layout(agent_dir)
    facts = extract_module(agent_dir)
    suite = suite_facts(layout)
    config = config_facts(layout)
    results = result_facts(layout)
    workflows, gated = eval_workflows(repo_root)
    level, evidence = maturity_level(
        suite, config, results, workflows=workflows, gated=gated
    )
    backend = detect_backend(layout.agent_dir)

    gaps: list[str] = []
    if suite["cases"] and not suite["by_category"].get("adversarial"):
        gaps.append("no adversarial case: nothing tries to make the agent fail")
    if suite["cases"] and not config["judged_config"]:
        gaps.append("no judged config: response quality and restraint are untested")
    if suite["legacy_files"]:
        gaps.append(f"{suite['legacy_files']} legacy file(s): migrate with "
                    "/adk:eval-create migrate_legacy.py")
    mismatch = layout.app_name_mismatch()
    if mismatch:
        gaps.append(f"App.name {mismatch[0]!r} disagrees with the directory {mismatch[1]!r}")

    return {
        "agent_dir": agent_dir,
        "name": Path(agent_dir).name,
        "layout": layout.style,
        "app_name": layout.expected_app_name(),
        "declared_app_name": layout.declared_app_name,
        "export": layout.exports.preferred_export if layout.exports else None,
        "tools": sorted(facts.bound_tool_names),
        "tool_count": len(facts.bound_tool_names),
        "backend": backend.backend,
        "suite": suite,
        "config": config,
        "results": results,
        "maturity": {"level": level, "name": LEVEL_NAMES[level], "evidence": evidence},
        "gaps": gaps,
        "next_step": next_step(level, suite, config),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="repository or directory to scan")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format",
                        help="shorthand for --format json")
    parser.add_argument("--max-depth", type=int, default=3,
                        help="how deep to look for agent directories")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return EXIT_USAGE

    agent_dirs = find_agent_dirs(root, max_depth=args.max_depth)
    agents = [inspect_agent(directory, root.resolve()) for directory in agent_dirs]
    workflows, gated = eval_workflows(root.resolve())

    payload = {
        "root": str(root.resolve()),
        "agents": agents,
        "totals": {
            "agents": len(agents),
            "cases": sum(a["suite"]["cases"] for a in agents),
            "evalsets": sum(a["suite"]["evalsets"] for a in agents),
            "with_no_cases": [a["name"] for a in agents if a["suite"]["cases"] == 0],
            "eval_workflows": workflows,
            "gated": gated,
            "lowest_level": min((a["maturity"]["level"] for a in agents), default=0),
        },
    }

    if args.format == "json":
        print(json.dumps(payload, indent=2))
        return EXIT_OK

    print(f"# ADK inventory: {payload['root']}")
    print()
    if not agents:
        print("No loadable ADK agent found (no agent.py, loadable __init__.py or "
              "root_agent.yaml under this path).")
        return EXIT_OK

    print(f"{len(agents)} agent(s), {payload['totals']['cases']} eval case(s) in "
          f"{payload['totals']['evalsets']} file(s).")
    print()
    print("| Agent | Layout | App name | Tools | Evalsets | Cases | Cost | Last run | Level |")
    print("|---|---|---|---|---|---|---|---|---|")
    for agent in agents:
        latest = agent["results"]["latest"]
        last = (f"{latest['passed']}/{latest['passed'] + latest['failed']} pass"
                if latest else "never")
        print(f"| {agent['name']} | {agent['layout']} | {agent['app_name']} | "
              f"{agent['tool_count']} | {agent['suite']['evalsets']} | "
              f"{agent['suite']['cases']} | {agent['config']['cost_tier']} | {last} | "
              f"L{agent['maturity']['level']} |")

    print()
    for agent in agents:
        print(f"## {agent['name']} — L{agent['maturity']['level']} "
              f"{agent['maturity']['name']}")
        print()
        print(f"- path: `{agent['agent_dir']}`")
        print(f"- export: {agent['export'] or '(none found)'}; backend: {agent['backend']}")
        if agent["suite"]["by_category"]:
            spread = ", ".join(f"{k} {v}" for k, v in sorted(agent["suite"]["by_category"].items()))
            print(f"- cases by category: {spread}")
        if agent["config"]["configs"]:
            print(f"- config: {', '.join(agent['config']['configs'])} "
                  f"({', '.join(agent['config']['metrics']) or 'no criteria'})")
        else:
            print("- config: none, so ADK's defaults apply "
                  "(tool_trajectory_avg_score 1.0, response_match_score 0.8)")
        for gap in agent["gaps"]:
            print(f"- gap: {gap}")
        print(f"- next: {agent['next_step']}")
        print()

    if payload["totals"]["with_no_cases"]:
        print(f"Agents with no eval cases at all: "
              f"{', '.join(payload['totals']['with_no_cases'])}. "
              "Those are the ones where a regression cannot be detected.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
