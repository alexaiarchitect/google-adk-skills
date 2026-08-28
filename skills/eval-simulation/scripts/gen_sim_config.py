#!/usr/bin/env python3
"""Generate a user-simulation or environment-simulation configuration.

**User simulation** replaces fixed turns with an LLM playing the user against a
plan. It goes in the eval config as `user_simulator_config`, and it constrains
which metrics can run: a simulated conversation has no fixed reference, so
reference-based metrics are out.

**Environment simulation** fakes the *tools* instead of the user: mocked
responses, injected errors, injected latency. It is how you test failure paths
without breaking anything real, and it is configured on the agent side rather
than in the eval config. The feature is experimental in ADK, and the generated
document validates as `EnvironmentSimulationConfig`.

    gen_sim_config.py --kind user [--model M] [--max-invocations N] [--out F]
    gen_sim_config.py --kind env --tools lookup_policy,submit_claim [--out F]
    gen_sim_config.py --kind generate --count 10 [--out F]

Exit codes: 0 written, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.env import default_judge_model, detect_backend  # noqa: E402
from adk_skills.report import EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.templating import WritePlan, dumps_json  # noqa: E402


def user_config(model: str, max_invocations: int, audio: bool) -> dict:
    return {
        "user_simulator_config": {
            "type": "llm_audio" if audio else "llm_backed",
            "model": model,
            # A simulated conversation has no natural end, so cap it: every turn
            # costs a model call for the simulator and one for the agent.
            "max_allowed_invocations": max_invocations,
            "include_function_calls": False,
        }
    }


def env_config(tools: list[str], model: str) -> dict:
    """An EnvironmentSimulationConfig with every injection switched off.

    `injection_probability` defaults to **1.0**, so an injection nobody disabled
    fires on every call. This writes 0.0 explicitly; a case that is about a
    failure path raises one of them to 1.0.

    `mock_strategy_type` is an enum serialized by *value*: 0
    MOCK_STRATEGY_UNSPECIFIED, 1 MOCK_STRATEGY_TOOL_SPEC, 2
    MOCK_STRATEGY_TRACING (deprecated in ADK; use TOOL_SPEC with tracing input).
    The member *names* are rejected by the real model, so this must stay an int.

    0 is legal here only because `injection_configs` is non-empty. With no
    injection hit ADK logs a warning and returns None, so the real tool runs.
    """
    return {
        "simulation_model": model,
        "tool_simulation_configs": [
            {
                "tool_name": tool,
                "mock_strategy_type": 0,
                "injection_configs": [
                    # Exactly one of injected_error / injected_response per entry.
                    {
                        "injection_probability": 0.0,
                        "injected_error": {
                            "injected_http_error_code": 503,
                            "error_message": f"TODO: the error {tool} should return when the "
                                             "backend is unavailable.",
                        },
                    },
                    # Latency attaches to an error or response entry; a
                    # latency-only entry is rejected. Capped at 120 seconds.
                    {
                        "injection_probability": 0.0,
                        "injected_latency_seconds": 5.0,
                        "injected_response": {"TODO": f"the slow but successful result of {tool}"},
                    },
                ],
            }
            for tool in tools
        ],
    }


def generation_config(count: int, model: str) -> dict:
    return {
        "count": count,
        "model_name": model,
        "generation_instruction": (
            "TODO: what kinds of conversation to generate. Name the behaviours worth "
            "covering, not just the topic: a user who omits a required value, one who "
            "changes their mind, one who asks for something the policy forbids."
        ),
        "environment_context": (
            "TODO: what the simulated user knows about the domain, in a sentence or two."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kind", choices=("user", "env", "generate"), required=True)
    parser.add_argument("--model", help="defaults to MODEL_NAME from the agent's .env")
    parser.add_argument("--agent-dir")
    parser.add_argument("--max-invocations", type=int, default=8)
    parser.add_argument("--audio", action="store_true", help="llm_audio simulator")
    parser.add_argument("--tools", default="", help="comma-separated, for --kind env")
    parser.add_argument("--count", type=int, default=10, help="scenarios to generate")
    parser.add_argument("--out")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    backend = detect_backend(args.agent_dir) if args.agent_dir else None
    model = args.model or default_judge_model(backend)

    if args.kind == "user":
        document = user_config(model, args.max_invocations, args.audio)
        note = ("Merge this into your eval config. Reference-based metrics cannot score "
                "simulated conversations; use rubric-based, hallucinations_v1, safety_v1 "
                "or multi_turn_* instead.")
    elif args.kind == "env":
        tools = [t.strip() for t in args.tools.split(",") if t.strip()]
        if not tools:
            print("error: --kind env needs --tools", file=sys.stderr)
            return EXIT_USAGE
        document = env_config(tools, model)
        note = (
            "This configures the tool side, not the eval config. Attach it with "
            "EnvironmentSimulationFactory.create_plugin(config) in App(plugins=[...]), or "
            "EnvironmentSimulationFactory.create_callback(config) as an agent's "
            "before_tool_callback; the config classes import from "
            "google.adk.tools.environment_simulation.environment_simulation_config. "
            "injection_probability defaults to 1.0, so every entry here sets 0.0 "
            "explicitly - raise one to 1.0 in the case that is about that failure. "
            "mock_strategy_type is an integer (0 unspecified, 1 tool spec, 2 tracing - "
            "deprecated); the member names do not validate. The feature is experimental."
        )
    else:
        document = generation_config(args.count, model)
        note = ("Use with `adk eval_set generate_eval_cases <agent-dir> <eval_set_id> "
                "--user_simulation_config_file <this file>`. It calls the Vertex AI "
                "evaluation SDK, so it needs a project and location.")

    rendered = dumps_json(document)
    plan = WritePlan()
    if args.out:
        plan.add(args.out, rendered, force=args.force)
    written = plan.apply() if args.out else []

    if args.json:
        print(json.dumps({"kind": args.kind, "config": document, "model": model,
                          "written": written, "note": note}, indent=2))
    else:
        print(rendered)
        print(note)
        for path in written:
            print(f"wrote {path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
