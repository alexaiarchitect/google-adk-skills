#!/usr/bin/env python3
"""Check whether this machine can actually run ADK evals for an agent.

Answers three questions, in the order they block you:

1. Is there an interpreter that can import `google.adk`, and which version?
2. Is a GenAI backend configured in the agent's `.env`?
3. Can the metrics in the eval config be served by that backend?

It also reports which optional knobs are set, in the agent's `.env` or the
environment, e.g. `ADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS` (silences the
YAML loader's `[EXPERIMENTAL]` warning, and from 2.13 the feature registry's).

    check_env.py <agent-dir> [--json] [--config path]

Exit codes: 0 ready, 1 something is missing, 2 the target is unusable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills import ADK_TARGET  # noqa: E402
from adk_skills.adk_subprocess import describe_runner  # noqa: E402
from adk_skills.env import (  # noqa: E402
    ADK_REQUIREMENT, OPTIONAL_KNOBS, detect_backend, dotenv_value, resolve_runner,
    version_is_supported,
)
from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.metrics import METRIC_CATALOG, cost_tier  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent directory or project root")
    parser.add_argument("--config", help="eval config to check the backend against")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-probe", action="store_true", help="do not run an interpreter to check ADK")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(target)
    agent_dir = layout.agent_dir or str(target)
    runner = resolve_runner(agent_dir, layout.project_root, probe=not args.no_probe)
    backend = detect_backend(agent_dir)

    blockers: list[str] = []
    notes: list[str] = []

    if not runner.available:
        blockers.append(runner.note or "no interpreter with google.adk")
    elif runner.adk_version and not version_is_supported(runner.adk_version):
        blockers.append(
            f"ADK {runner.adk_version} is installed; these skills target {ADK_TARGET} (2.x)"
        )
    elif runner.kind == "uv":
        notes.append(f"ADK will be installed on demand via {ADK_REQUIREMENT}")

    if backend.backend == "unknown":
        blockers.append(
            "no backend: set GOOGLE_GENAI_USE_VERTEXAI=TRUE with GOOGLE_CLOUD_PROJECT and "
            "GOOGLE_CLOUD_LOCATION in the agent's .env, or GOOGLE_API_KEY"
        )
    elif backend.missing:
        blockers.append(f"backend {backend.backend} is missing {', '.join(backend.missing)}")

    config_path = args.config
    if not config_path:
        for candidate in layout.configs:
            if Path(candidate).name in ("test_config.json", "eval_config.json"):
                config_path = candidate
                break

    metrics: list[str] = []
    if config_path and Path(config_path).exists():
        loaded = load_config(config_path)
        metrics = loaded.metric_names()
        tier = cost_tier(metrics)
        if tier == "vertex" and not backend.vertex_ready:
            blockers.append(
                "the config uses a metric scored by the Vertex AI evaluation service "
                f"({', '.join(n for n in metrics if METRIC_CATALOG.get(n) and METRIC_CATALOG[n].needs_vertex)})"
                ", which needs GOOGLE_API_KEY, or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION "
                "with ADC; neither is configured"
            )
        elif tier != "free" and not backend.judge_ready:
            blockers.append("the config uses a judge metric but no backend can serve it")
        elif tier != "free":
            notes.append(f"this config spends model calls (cost tier: {tier})")

    knobs: dict[str, dict[str, str]] = {}
    for name in OPTIONAL_KNOBS:
        from_dotenv = dotenv_value(agent_dir, name)
        value = from_dotenv if from_dotenv is not None else os.environ.get(name)
        if value is not None:
            knobs[name] = {"value": value,
                           "from": ".env" if from_dotenv is not None else "environment"}
    for name, knob in knobs.items():
        notes.append(f"{name}={knob['value']} is set ({knob['from']}): {OPTIONAL_KNOBS[name]}")

    payload = {
        "agent_dir": agent_dir,
        "layout": layout.style,
        "runner": runner.to_dict(),
        "backend": backend.to_dict(),
        "config": config_path,
        "metrics": metrics,
        "cost_tier": cost_tier(metrics) if metrics else "free",
        "ready": not blockers,
        "blockers": blockers,
        "notes": notes,
        "knobs": knobs,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Agent:    {agent_dir} ({layout.style} layout)")
        print(f"Runner:   {describe_runner(runner)}")
        print(f"Backend:  {backend.backend}" + (f" project={backend.project} location={backend.location}"
                                                if backend.project else ""))
        if metrics:
            print(f"Criteria: {', '.join(metrics)} (cost tier: {payload['cost_tier']})")
        print(f"Ready:    {'yes' if payload['ready'] else 'no'}")
        for blocker in blockers:
            print(f"  - blocker: {blocker}")
        for note in notes:
            print(f"  - note: {note}")
        unset = [name for name in OPTIONAL_KNOBS if name not in knobs]
        if unset:
            print(f"  - optional knobs (unset): {', '.join(unset)}")

    return EXIT_OK if payload["ready"] else EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
