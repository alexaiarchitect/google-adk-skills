#!/usr/bin/env python3
"""Validate an ADK eval config against the metric catalog and the backend.

Catches the quiet failures: a metric name that does not exist, a threshold
outside the metric's range (a threshold below the minimum can never fail), a
criterion field ADK accepts and ignores, a judge model left to a deprecated
default, a Vertex-only metric with no project configured, rubrics that ask a
judge for a 1-5 rating, and user simulation combined with a metric that needs a
fixed reference.

    validate_config.py <config.json> [--agent-dir DIR] [--format json|md]

Exit codes: 0 clean, 1 findings, 2 bad target.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.env import detect_backend  # noqa: E402
from adk_skills.eval_config import load_config, validate_config  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.metrics import cost_tier  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.tools_ast import extract_module  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="eval config file, or an agent directory to find one in")
    parser.add_argument("--agent-dir", help="agent, for tool names and backend detection")
    parser.add_argument("--no-backend", action="store_true",
                        help="skip backend checks (useful in CI where credentials differ)")
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
    if agent_dir and Path(agent_dir).exists():
        known_tools = extract_module(agent_dir).bound_tool_names or None

    backend = None if args.no_backend else detect_backend(agent_dir or target.parent)

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
    }
    validate_config(loaded, report=report, backend=backend, agent_dir=agent_dir, known_tools=known_tools)

    print(report.render(args.format))
    if report.has_errors():
        return EXIT_FINDINGS
    if args.strict and report.count("warn"):
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
