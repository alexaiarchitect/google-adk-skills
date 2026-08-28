#!/usr/bin/env python3
"""Check that a simulation setup can actually score what it runs.

Three things go wrong, and all three are quiet:

* **Reference-based metrics with simulated users.** A simulated conversation has
  no fixed expected answer, so `tool_trajectory_avg_score` and friends cannot
  score it. The cases run and produce nothing useful.
* **Scenario cases with no simulator.** A case with a `conversation_scenario` and
  a config with no `user_simulator_config` has nothing to play the user.
* **A custom simulator prompt that drops a required placeholder.** Without
  `stop_signal`, `conversation_plan` and `conversation_history`, the simulator
  cannot see the plan or know when to stop.

    validate_sim_config.py <agent-dir | config.json> [--agent-dir D] [--json]

Exit codes: 0 clean, 1 findings, 2 nothing to check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.eval_config import load_config, validate_config  # noqa: E402
from adk_skills.env import detect_backend  # noqa: E402
from adk_skills.evalset import iter_cases, load_evalset  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.metrics import METRIC_CATALOG, incompatible_with_user_sim  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.tools_ast import extract_module  # noqa: E402

PREBUILT_PERSONAS = ("EXPERT", "NOVICE", "EVALUATOR")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--agent-dir")
    parser.add_argument("--config", help="the eval config to check")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(args.agent_dir or (target if target.is_dir() else target.parent))
    config_path = args.config
    if not config_path:
        if target.is_file() and target.suffix == ".json":
            config_path = str(target)
        else:
            for candidate in layout.configs:
                if Path(candidate).name in ("test_config.json", "eval_config.json"):
                    config_path = candidate
                    break
    if not config_path:
        print("error: no eval config found; pass --config", file=sys.stderr)
        return EXIT_USAGE

    loaded = load_config(config_path)
    report = GapReport(subject=str(config_path), skill="eval-simulation")

    known_tools = None
    if layout.agent_dir:
        known_tools = extract_module(layout.agent_dir).bound_tool_names or None

    validate_config(
        loaded, report=report, backend=detect_backend(layout.agent_dir),
        agent_dir=layout.agent_dir, known_tools=known_tools,
    )

    scenario_cases: list[str] = []
    static_cases: list[str] = []
    personas: set[str] = set()
    for path in layout.evalsets:
        evalset = load_evalset(path)
        if evalset.fmt != "evalset" or not isinstance(evalset.doc, dict):
            continue
        for case in iter_cases(evalset.doc):
            eval_id = str(case.get("eval_id", "?"))
            scenario = case.get("conversation_scenario")
            if scenario:
                scenario_cases.append(eval_id)
                if isinstance(scenario, dict):
                    persona = scenario.get("user_persona")
                    if isinstance(persona, str):
                        personas.add(persona)
                        if persona not in PREBUILT_PERSONAS:
                            report.add(
                                "SIM010", "warn", "simulation",
                                f"persona {persona!r} is not a pre-built one "
                                f"({', '.join(PREBUILT_PERSONAS)})",
                                path=path, locator=eval_id,
                                fix="register it, or inline the persona as an object with "
                                    "id, description and behaviors",
                                source="adk-python: evaluation/simulation/user_simulator_personas.py",
                            )
            else:
                static_cases.append(eval_id)

    metrics = loaded.metric_names()
    uses_simulator = loaded.uses_user_simulation()

    if scenario_cases and not uses_simulator:
        report.add(
            "SIM001", "error", "simulation",
            f"{len(scenario_cases)} scenario case(s) but no `user_simulator_config`",
            path=config_path,
            fix="add one with gen_sim_config.py --kind user; without it nothing plays the user",
            source="adk-python: evaluation/simulation/user_simulator_provider.py",
        )
    if uses_simulator and not scenario_cases:
        report.add(
            "SIM002", "warn", "simulation",
            "a simulator is configured but no case has a `conversation_scenario`, so it "
            "never runs",
            path=config_path,
            fix="write scenario cases with gen_scenarios.py, or drop the simulator config",
        )
    if scenario_cases:
        clashing = incompatible_with_user_sim(metrics)
        if clashing:
            report.add(
                "SIM003", "error", "simulation",
                f"reference-based metric(s) cannot score a simulated conversation: "
                f"{', '.join(clashing)}",
                path=config_path,
                fix="keep the static cases on this config, and give the simulated ones their "
                    "own with rubric-based, hallucinations_v1, safety_v1 or multi_turn_* metrics",
                source="adk-docs: docs/evaluate/criteria.md",
            )
        scoring = [m for m in metrics if (s := METRIC_CATALOG.get(m)) and s.user_sim_compatible]
        if not scoring:
            report.add(
                "SIM004", "error", "simulation",
                "no configured metric can score a simulated conversation",
                path=config_path,
                fix="add rubric_based_final_response_quality_v1, hallucinations_v1, safety_v1 "
                    "or a multi_turn_* metric",
            )
    if static_cases and uses_simulator:
        report.add(
            "SIM005", "info", "simulation",
            f"{len(static_cases)} static case(s) share this config with simulated ones; "
            "static cases ignore the simulator, and reference metrics only apply to them",
            path=config_path,
            fix="separate configs keep the intent clearer, and keep cost down",
        )

    report.discovered = {
        "config": config_path,
        "metrics": metrics,
        "uses_user_simulation": uses_simulator,
        "scenario_cases": scenario_cases,
        "static_cases": len(static_cases),
        "personas": sorted(personas),
        "simulation_compatible_metrics": [
            m for m in metrics if (s := METRIC_CATALOG.get(m)) and s.user_sim_compatible
        ],
    }

    print(report.render(args.format))
    return EXIT_FINDINGS if report.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
