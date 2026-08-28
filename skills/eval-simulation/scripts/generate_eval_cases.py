#!/usr/bin/env python3
"""Generate conversation scenarios from a description, and check what came back.

Writing scenario cases by hand is slow, and the ones a person writes cluster
around the paths they already had in mind - which is the opposite of what
simulation is for. `adk eval_set generate_eval_cases` asks the Vertex AI
evaluation service to invent scenarios from the agent's own definition plus a
description of what to explore, so the breadth comes from somewhere other than
your assumptions.

Three things make the raw command hard to use, and this wrapper handles each:

* **The config is `extra="forbid"` with exactly four fields.** `count` and
  `model_name` are required; `generation_instruction` and `environment_context`
  are optional. A misspelled key does not get ignored - ADK rejects the file
  after you have already paid for nothing. The config is checked first, locally.
* **It costs money on a service most agents are not configured for.** This is
  the Vertex AI Gen AI Evaluation Service, which needs `GOOGLE_API_KEY` or a
  project and location. The credential is checked, and the cost stated, before
  anything runs.
* **What comes back is a draft.** Generated scenarios are a starting point, not
  a specification - the same rule as a captured trace. The resulting evalset is
  validated and handed back for review rather than declared done.

    generate_eval_cases.py <agent-dir> <eval_set_id> --config F
                           [--storage gs://...] [--dry-run] [--json]

Exit codes: 0 generated and valid, 1 the config or the result has errors,
2 usage or environment (no agent, no credential, no runner).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.adk_subprocess import describe_runner, run_adk  # noqa: E402
from adk_skills.env import detect_backend, resolve_runner  # noqa: E402
from adk_skills.evalset import EVAL_SET_ID_RE, load_evalset, stats, validate_evalset  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import (  # noqa: E402
    EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport,
)

#: `ConversationGenerationConfig` - snake_case names, camelCase aliases accepted.
REQUIRED = {"count", "model_name"}
OPTIONAL = {"generation_instruction", "environment_context"}
ALIASES = {
    "modelName": "model_name",
    "generationInstruction": "generation_instruction",
    "environmentContext": "environment_context",
}
_SRC = "adk-python: evaluation/conversation_scenarios.py (ConversationGenerationConfig)"
_SRC_FACADE = "adk-python: evaluation/_vertex_ai_scenario_generation_facade.py"


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    return {ALIASES.get(key, key): value for key, value in raw.items()}


def validate_generation_config(path: Path, report: GapReport) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.add("SIM020", "error", "simulation", f"unreadable generation config: {exc}",
                   path=str(path), source=_SRC)
        return None
    if not isinstance(raw, dict):
        report.add("SIM021", "error", "simulation",
                   "the generation config must be a JSON object",
                   path=str(path), source=_SRC)
        return None

    config = normalize(raw)
    unknown = sorted(set(config) - REQUIRED - OPTIONAL)
    if unknown:
        report.add("SIM022", "error", "simulation",
                   f"unrecognized key(s): {', '.join(unknown)}",
                   path=str(path),
                   fix="ConversationGenerationConfig sets extra=\"forbid\", so ADK rejects "
                       "the file. It has exactly count, model_name, generation_instruction "
                       "and environment_context.",
                   source=_SRC)

    count = config.get("count")
    if count is None:
        report.add("SIM023", "error", "simulation", "`count` is required",
                   path=str(path), fix="how many scenarios to generate", source=_SRC)
    elif not isinstance(count, int) or isinstance(count, bool) or count < 1:
        report.add("SIM024", "error", "simulation",
                   f"`count` must be a positive integer, not {count!r}",
                   path=str(path), source=_SRC)
    elif count > 25:
        report.add("SIM025", "warn", "simulation",
                   f"`count` is {count}: every generated scenario is a multi-turn "
                   "conversation you have to read before trusting it",
                   path=str(path),
                   fix="generate a handful, review them, then generate more")

    model = config.get("model_name")
    if not model:
        report.add("SIM026", "error", "simulation", "`model_name` is required",
                   path=str(path),
                   fix="name the generating model explicitly, e.g. gemini-flash-latest, "
                       "so a model retirement does not silently change the output",
                   source=_SRC)
    elif not isinstance(model, str):
        report.add("SIM027", "error", "simulation", "`model_name` must be a string",
                   path=str(path), source=_SRC)

    instruction = config.get("generation_instruction")
    if not instruction:
        report.add("SIM028", "warn", "simulation",
                   "no `generation_instruction`: the generator has nothing to aim at "
                   "and will produce generic conversations",
                   path=str(path),
                   fix="say what to explore - the branches, the refusals, the pressure "
                       "the agent should hold under",
                   source=_SRC)
    if not config.get("environment_context"):
        report.add("SIM029", "info", "simulation",
                   "no `environment_context`: the generator does not know what the "
                   "agent's world contains (valid ids, cost centres, limits), so "
                   "scenarios may reference things that do not exist",
                   path=str(path), source=_SRC)
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="agent directory or project root")
    parser.add_argument("eval_set_id", help="the eval set to add the generated cases to")
    parser.add_argument("--config", required=True,
                        help="ConversationGenerationConfig JSON "
                             "(gen_sim_config.py --kind generate writes one)")
    parser.add_argument("--storage", help="--eval_storage_uri gs://bucket/prefix")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the config, print the command, and stop")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"error: {config_path} does not exist. Generate one with "
              "gen_sim_config.py --kind generate.", file=sys.stderr)
        return EXIT_USAGE
    if not EVAL_SET_ID_RE.match(args.eval_set_id):
        print(f"error: eval set id {args.eval_set_id!r} must match ^[a-zA-Z0-9_]+$",
              file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(target)
    if not layout.agent_dir:
        extra = (f"; found {len(layout.sibling_agents)} agents, pick one"
                 if layout.sibling_agents else "")
        print(f"error: no ADK agent under {target}{extra}", file=sys.stderr)
        return EXIT_USAGE

    report = GapReport(subject=str(config_path), skill="eval-simulation")
    config = validate_generation_config(config_path, report)

    # 1. The config gate, before anything is spent.
    if report.has_errors():
        print(report.render("json" if args.json else "md"))
        return EXIT_FINDINGS

    count = (config or {}).get("count", 0)
    cost_note = (
        f"cost tier: vertex. {count} scenario(s) generated through the Vertex AI "
        "Gen AI Evaluation Service, billed separately from Gemini API usage. "
        "Running the generated scenarios afterwards costs again: each one is a "
        "multi-turn conversation with a model call for the simulated user and "
        "another for the agent, on every turn."
    )

    command = [
        "eval_set", "generate_eval_cases", str(layout.agent_dir), args.eval_set_id,
        f"--user_simulation_config_file={config_path}",
    ]
    if args.storage:
        command.append(f"--eval_storage_uri={args.storage}")

    if args.dry_run:
        payload = {
            "command": "adk " + " ".join(command),
            "config": config,
            "cost": cost_note,
            "validation": report.to_dict(),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(report.to_md())
            print()
            print(cost_note)
            print()
            print(payload["command"])
        return EXIT_OK

    # 2. The credential gate. The facade reads GOOGLE_API_KEY first and only
    #    then falls back to project + location, raising if neither is there.
    backend = detect_backend(layout.agent_dir)
    if not (backend.has_api_key or (backend.project and backend.location)):
        print(
            "error: scenario generation uses the Vertex AI evaluation SDK, which needs "
            "either GOOGLE_API_KEY or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION "
            "with application default credentials. Neither is set for this agent "
            f"(checked: {', '.join(backend.env_files) or 'no .env found'}).\n"
            f"Source: {_SRC_FACADE}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    runner = resolve_runner(layout.agent_dir, layout.project_root)
    if not runner.available:
        print(f"error: {runner.note}", file=sys.stderr)
        return EXIT_USAGE

    if not args.json:
        print(f"Runner: {describe_runner(runner)}")
        print(cost_note)
        print()

    outcome = run_adk(command, runner=runner, cwd=layout.project_root or target,
                      timeout=args.timeout)
    if not outcome.ok:
        print(f"error: generation failed:\n{outcome.tail()}", file=sys.stderr)
        return EXIT_FINDINGS

    # 3. Find what it wrote and validate it, the same way a hand-written set is
    #    validated. ADK writes the set beside the agent unless storage is remote.
    written: str | None = None
    for candidate in (
        Path(layout.agent_dir) / f"{args.eval_set_id}.evalset.json",
        Path(layout.agent_dir).parent / f"{args.eval_set_id}.evalset.json",
    ):
        if candidate.is_file():
            written = str(candidate)
            break

    result_report = GapReport(subject=written or args.eval_set_id, skill="eval-simulation")
    summary: dict[str, Any] = {}
    if written:
        loaded = load_evalset(written)
        validate_evalset(
            loaded, report=result_report,
            expected_app_name=layout.expected_app_name(),
            reference_metrics=False,
        )
        if loaded.fmt == "evalset" and isinstance(loaded.doc, dict):
            summary = stats(loaded.doc)
    elif args.storage:
        result_report.add("SIM030", "info", "simulation",
                          f"the set was written to {args.storage}, so it cannot be "
                          "validated locally; review it in the dev UI")
    else:
        result_report.add("SIM031", "warn", "simulation",
                          f"no {args.eval_set_id}.evalset.json was found after the run; "
                          "check the command output for where it went")

    payload = {
        "agent_dir": layout.agent_dir,
        "eval_set_id": args.eval_set_id,
        "config": config,
        "runner": describe_runner(runner),
        "evalset": written,
        "stats": summary,
        "command": outcome.to_dict(),
        "validation": result_report.to_dict(),
        "next_steps": [
            "read every generated scenario before committing it: a plan that describes "
            "a topic rather than behaviour proves nothing",
            "score them with a simulation config - reference-based metrics cannot "
            "score a conversation that has no fixed reference",
            "validate with /adk:eval-review, then run with /adk:eval-run",
        ],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(outcome.combined)
        print()
        if summary:
            print(f"{written}: {summary['cases']} case(s), "
                  f"{summary['scenario_cases']} scenario case(s)")
        if result_report.findings:
            print()
            print(result_report.to_md())
        print()
        print("Next:")
        for step in payload["next_steps"]:
            print(f"  - {step}")

    return EXIT_FINDINGS if result_report.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
