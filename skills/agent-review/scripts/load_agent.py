#!/usr/bin/env python3
"""Load the agent the way ADK does, and report what came back.

The static review catches most problems, but only an import proves the agent
actually loads: a missing dependency, a Pydantic validator that rejects a field,
a circular import, a name that is defined but not what ADK expects.

This mirrors ADK's own resolution order — `app` (an `App`), then `root_agent`,
then `get_agent_async` — and runs it through an interpreter that has ADK, which
may not be the one running this script.

    load_agent.py <agent-dir> [--json]

Exit codes: 0 loaded, 1 it did not load, 2 no runner or no agent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.adk_subprocess import describe_runner, run_command  # noqa: E402
from adk_skills.env import resolve_runner  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402

PROBE = r'''
import asyncio, importlib, inspect, json, os, sys, traceback

agents_dir, agent_name, module_path = sys.argv[1:4]
result = {"module": module_path, "agent_name_arg": agent_name}
try:
    import google.adk as adk
    result["adk_version"] = adk.version.__version__
    from google.adk.apps.app import App

    loaded = None
    loader_error = None
    # The same loader `adk web`, `adk run` and `adk eval` use: it covers a
    # package with `app`/`root_agent`, an `agent.py` submodule, and a
    # `root_agent.yaml` config, and it loads the agent's .env the same way.
    try:
        from google.adk.cli.utils.agent_loader import AgentLoader
        loaded = AgentLoader(agents_dir).load_agent(agent_name)
        result["loader"] = "AgentLoader"
    except ValueError as exc:
        loader_error = exc

    agent = None
    if isinstance(loaded, App):
        result["export"] = "app"
        result["app_name"] = loaded.name
        result["plugins"] = [type(p).__name__ for p in (loaded.plugins or [])]
        agent = loaded.root_agent
    elif loaded is not None:
        yaml_config = os.path.join(agents_dir, agent_name, "root_agent.yaml")
        result["export"] = "root_agent.yaml" if os.path.isfile(yaml_config) else "root_agent"
        agent = loaded
    else:
        # Only `adk eval` accepts a `get_agent_async` factory; the loader does not.
        module = importlib.import_module(module_path)
        factory = getattr(module, "get_agent_async", None)
        if factory is None:
            raise loader_error or AttributeError(
                "module exposes none of: app, root_agent, get_agent_async"
            )
        result["export"] = "get_agent_async"
        result["loader"] = "get_agent_async (adk eval only; adk web/run will not load this)"
        produced = factory()
        if inspect.isawaitable(produced):
            produced = asyncio.run(produced)
        agent = produced[0] if isinstance(produced, tuple) else produced

    if agent is not None:
        result["agent_name"] = getattr(agent, "name", None)
        result["agent_type"] = type(agent).__name__
        model = getattr(agent, "model", None)
        result["model"] = model if isinstance(model, str) else type(model).__name__
        tools = getattr(agent, "tools", None) or []
        names = []
        for tool in tools:
            names.append(getattr(tool, "__name__", None) or getattr(tool, "name", None)
                         or type(tool).__name__)
        result["tools"] = names
        result["sub_agents"] = [getattr(s, "name", "?") for s in (getattr(agent, "sub_agents", None) or [])]
        instruction = getattr(agent, "instruction", "")
        result["instruction_chars"] = len(instruction) if isinstance(instruction, str) else -1
    result["loaded"] = True
except Exception as exc:
    result["loaded"] = False
    result["error_type"] = type(exc).__name__
    result["error"] = str(exc)
    result["traceback"] = traceback.format_exc().splitlines()[-12:]

print(json.dumps(result))
'''

HINTS = {
    "ModuleNotFoundError": "a dependency is missing from the interpreter that ran this, "
                           "or the agent package is not importable from the working directory",
    "ImportError": "check the import paths; several ADK symbols moved in 2.x (see the "
                   "migration reference). A toolset that imports cleanly elsewhere may need "
                   "an optional extra in this interpreter: `mcp` for McpToolset, "
                   "`google-adk[a2a]` for remote agents",
    "ValidationError": "a field was rejected at construction: a reserved name, a sampling "
                       "kwarg passed directly, or generate_content_config carrying tools, "
                       "system_instruction or response_schema",
    "AttributeError": "the module loads but exposes nothing ADK recognises; export "
                      "`app = App(...)` or `root_agent`",
    "ValueError": "the ADK loader found no `app`, `root_agent` or `root_agent.yaml` under the "
                  "agent directory; export one, or check the directory name is a valid "
                  "identifier",
    "TypeError": "an argument does not match the constructor; check what moved in ADK 2.x",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(target)
    if not layout.agent_dir or not layout.module_path:
        print(f"error: no ADK agent under {target}", file=sys.stderr)
        return EXIT_USAGE

    runner = resolve_runner(layout.agent_dir, layout.project_root)
    if not runner.available:
        print(f"error: {runner.note}", file=sys.stderr)
        return EXIT_USAGE

    outcome = run_command(
        [*runner.python_argv(), "-c", PROBE, str(layout.agents_dir), Path(layout.agent_dir).name, layout.module_path],
        cwd=layout.agents_dir, timeout=args.timeout,
    )

    payload: dict = {"runner": describe_runner(runner), "module": layout.module_path}
    line = next((l for l in reversed(outcome.stdout.splitlines()) if l.startswith("{")), None)
    if line:
        try:
            payload.update(json.loads(line))
        except json.JSONDecodeError:
            pass

    if "loaded" not in payload:
        payload["loaded"] = False
        payload["error"] = outcome.tail(15) or "the probe produced no output"

    if not payload.get("loaded") and payload.get("error_type") in HINTS:
        payload["hint"] = HINTS[payload["error_type"]]

    if args.json:
        print(json.dumps(payload, indent=2))
    elif payload.get("loaded"):
        print(f"Loaded   {payload['module']} via `{payload.get('export')}`")
        print(f"Runner   {payload['runner']}")
        if payload.get("app_name"):
            print(f"App      {payload['app_name']}"
                  + (f"  plugins: {', '.join(payload['plugins'])}" if payload.get("plugins") else ""))
        print(f"Agent    {payload.get('agent_name')} ({payload.get('agent_type')})")
        print(f"Model    {payload.get('model')}")
        print(f"Tools    {', '.join(payload.get('tools') or []) or '(none)'}")
        if payload.get("sub_agents"):
            print(f"Sub      {', '.join(payload['sub_agents'])}")
    else:
        print(f"Did not load: {payload.get('error_type', 'error')}: {payload.get('error')}")
        if payload.get("hint"):
            print(f"Likely:  {payload['hint']}")
        for line in payload.get("traceback", [])[-6:]:
            print(f"  {line}")

    return EXIT_OK if payload.get("loaded") else EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
