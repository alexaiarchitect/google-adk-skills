#!/usr/bin/env python3
"""Validate an ADK eval config against the metric catalog and the backend.

Catches the quiet failures: a metric name that does not exist, a threshold
outside the metric's range (a threshold below the minimum can never fail), a
criterion field ADK accepts and ignores, a judge model left to a deprecated
default, a Vertex-only metric with no project configured, rubrics that ask a
judge for a 1-5 rating, and user simulation combined with a metric that needs a
fixed reference. Cross-checks against the agent: a rubric about actions where
no metric grades the trajectory (CFG075), and the multi-turn trajectory judge
on an agent with a built-in tool, which crashes through ADK 2.13.0 (CFG076).

    validate_config.py <config.json> [--agent-dir DIR] [--format json|md]

Exit codes: 0 clean, 1 findings, 2 bad target.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.env import detect_backend, resolve_runner  # noqa: E402
from adk_skills.eval_config import load_config, validate_config  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.metrics import cost_tier  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.tools_ast import (  # noqa: E402
    agent_model_ids, builtin_model_tools, extract_module,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="eval config file, or an agent directory to find one in")
    parser.add_argument("--agent-dir", help="agent, for tool names and backend detection")
    parser.add_argument("--no-backend", action="store_true",
                        help="skip backend checks (useful in CI where credentials differ)")
    parser.add_argument("--no-probe", action="store_true",
                        help="do not look for an interpreter with google.adk; version-specific "
                             "notes are then written for the version this plugin targets")
    parser.add_argument("--adk-version",
                        help="the ADK version this config will run on, instead of probing")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(args.agent_dir or (target if target.is_dir() else target.parent))
    config_path = target
    if target.is_dir():
        candidates = [p for p in layout.configs if Path(p).name in ("test_config.json", "eval_config.json")]
        if not candidates:
            print(f"error: no eval config under {target}", file=sys.stderr)
            return EXIT_USAGE
        config_path = Path(candidates[0])

    agent_dir = args.agent_dir or layout.agent_dir
    known_tools = None
    builtin_tools = None
    facts = None
    if agent_dir and Path(agent_dir).exists():
        facts = extract_module(agent_dir)
        known_tools = facts.bound_tool_names or None
        builtin_tools = builtin_model_tools(facts) or None

    backend = None if args.no_backend else detect_backend(agent_dir or target.parent)
    agent_models = (agent_model_ids(facts, backend.model_name if backend else None)
                    if facts is not None else None)

    # Which ADK will run this config decides two findings only: CFG071 (the four
    # efficiency metrics exist from 2.10 on) and CFG076 (the multi-turn
    # trajectory judge crashes on built-in tools through 2.13.0; an unprobed
    # runner is taken to be the 2.13 target). Every error is version-independent.
    adk_version = args.adk_version
    if adk_version is None and not args.no_probe:
        runner = resolve_runner(agent_dir or layout.agent_dir, layout.project_root)
        adk_version = runner.adk_version

    loaded = load_config(config_path)
    report = GapReport(subject=str(config_path), skill="eval-criteria")
    report.discovered = {
        "config": str(config_path),
        "metrics": loaded.metric_names(),
        "cost_tier": cost_tier(loaded.metric_names()),
        "uses_user_simulation": loaded.uses_user_simulation(),
        "needs_response_reference": loaded.has_reference_metrics(),
        "agent_dir": agent_dir,
        "backend": backend.backend if backend else "not checked",
        "adk_version": adk_version or "not detected",
    }
    validate_config(loaded, report=report, backend=backend, agent_dir=agent_dir,
                    known_tools=known_tools, adk_version=adk_version,
                    agent_models=agent_models, builtin_tools=builtin_tools)

    print(report.render(args.format))
    if report.has_errors():
        return EXIT_FINDINGS
    if args.strict and report.count("warn"):
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
