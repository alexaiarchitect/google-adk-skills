#!/usr/bin/env python3
"""Review an ADK agent's source without running it.

Everything here is read from the syntax tree, so a broken dependency, a missing
virtualenv or a side effect at import time cannot affect the review, and it works
on a project you cannot yet run.

Ten dimensions:

1. loader and naming — will ADK find and accept this agent
2. model policy — deprecated ids, preview ids, hardcoded ids
3. agent configuration — the kwargs that raise at construction
4. tool contracts — docstrings, type hints, return shapes, raising versus returning
5. state — writes that never persist
6. App wiring — plugins, caching, resumability reach a run through an App;
   a cached prefix that changes per request (AGT060), a large uncached
   instruction (AGT061), an out-of-range ContextCacheConfig (AGT062), caching
   with eval cases that each make one model request (AGT063), a cached prefix
   too small to engage (AGT067), private plugin imports (AGT065)
7. ADK 2.x drift — deprecated workflow shells, `global_instruction`, YAML config
8. environment — backend configured, model id available
9. eval readiness — does the instruction have branches a case can cover
10. blocking calls — synchronous work that freezes the event loop
11. skills — what a `SkillToolset` registers (inline `models.Skill`,
    `load_skill_from_dir`, `load_skills_from_dir`): frontmatter ADK rejects
    (AGT070), an oversized body (AGT071), an instruction that repeats a skill
    (AGT072), a description too short to activate on (AGT073), lifecycle modes
    without ADK_ENABLE_SKILL_LIFECYCLE (AGT074), max_active_skills < 1 (AGT075)
12. context — an EventsCompactionConfig ADK rejects (AGT076), long eval
    conversations with no compaction (AGT077), a transfer target that reads the
    whole history (AGT078), a tool returning a large inline blob (AGT079)
13. state keys — an instruction placeholder `{key}` that no tool, callback,
    `output_key`, evalset `session_input.state` or `initial_state.json` ever
    writes (AGT080: ADK raises KeyError when it builds the instruction); agent
    names shared anywhere in the tree (AGT015: ADK 2.13 warns for siblings,
    cousins and ancestor/descendant clashes)
14. streaming tools — an `async def` tool with `yield` reached outside
    `run_live` (AGT081): warn on a text model (2.12+ aborts on the second
    `yield`, 2.9.2 never runs the body), info on a live model whose evals have
    no `live_model_config`

Report only. It never edits.

    lint_agent.py <agent-dir> [--format json|md] [--strict]

Exit codes: 0 clean, 1 findings, 2 no agent found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.coverage import branch_count, extract_sop_rules  # noqa: E402
from adk_skills.env import classify_model_id, detect_backend, dotenv_flag, dotenv_value  # noqa: E402
from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.evalset import load_evalset  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.tools_ast import (  # noqa: E402
    LLM_AGENT_CALLS, cache_suite_findings, compaction_suite_findings, extract_module,
    skill_lifecycle_findings, state_placeholder_findings, streaming_tool_findings,
)


def _cache_config(facts) -> dict | None:
    """The exported App's ContextCacheConfig kwargs; `{}` for an unreadable one."""
    app = next((a for a in facts.apps if a.context_cache_set), None)
    if app is None:
        return None
    return dict(app.context_cache_config or {})


def _compaction_config(facts) -> dict | None:
    """The exported App's EventsCompactionConfig kwargs; `{}` for an unreadable one."""
    app = next((a for a in facts.apps if a.events_compaction_set), None)
    if app is None:
        return None
    return dict(app.events_compaction_config or {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent directory or project root")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--no-backend", action="store_true", help="skip the .env checks")
    parser.add_argument("--adk-version",
                        help="the ADK release the agent runs on, when known (e.g. 2.9.2); "
                             "AGT074 then says when SkillLifecycleConfig does not exist yet")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(target)
    if not layout.agent_dir:
        message = f"error: no ADK agent under {target}"
        if layout.sibling_agents:
            message += f"; found {len(layout.sibling_agents)} agents, pick one"
        else:
            message += " (expected agent.py, a loadable __init__.py, <name>.py or root_agent.yaml)"
        print(message, file=sys.stderr)
        return EXIT_USAGE

    report = GapReport(subject=str(target), skill="agent-review")

    if layout.style == "yaml":
        report.add("AGT050", "warn", "agent",
                   "this is a YAML config agent: the feature flag is stable from 2.13; the "
                   "YAML loader still prints an [EXPERIMENTAL] warning through 2.13.0 (main "
                   "after 2.13.0 drops it) and the config classes are deprecated",
                   path=layout.agent_module_file,
                   fix="migrate to a Python agent; `adk create` still offers YAML, but the "
                       "config classes behind it are deprecated (AgentConfig, LlmAgentConfig). "
                       "ADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS=1 silences the loader "
                       "warning on releases that print it",
                   source="adk-python: agents/config_agent_utils.py")
        report.discovered = {"layout": layout.style, "agent_dir": layout.agent_dir}
        print(report.render(args.format))
        return EXIT_FINDINGS

    facts = extract_module(layout.agent_dir)
    agent = facts.root_agent()

    report.extend(facts.findings)
    for error in facts.syntax_errors:
        report.add("AGT000", "error", "agent", f"could not parse: {error}")

    # 1. loader and naming
    exports = layout.exports
    if exports and not exports.loadable:
        report.add("AGT051", "error", "agent",
                   "nothing loadable is exported: ADK looks for `app`, then `root_agent`, "
                   "then `get_agent_async`",
                   path=layout.agent_module_file,
                   source="adk-python: cli/utils/agent_loader.py")
    mismatch = layout.app_name_mismatch()
    if mismatch:
        declared, directory = mismatch
        report.add("ENV013", "warn", "agent",
                   f'App(name="{declared}") does not match the agent directory "{directory}"',
                   path=layout.agent_module_file,
                   fix=f"`app:` state, memory and the results file all use \"{directory}\"; "
                       "rename one so the two agree",
                   source="adk-python: runners.py")

    # 9. eval readiness
    rules = extract_sop_rules(agent.spec_text if agent else None, facts.bound_tool_names)
    branches = branch_count(rules)
    dynamic = bool(agent and getattr(agent, "instruction_is_dynamic", False))
    llm_root = bool(agent and agent.call in LLM_AGENT_CALLS)
    if dynamic:
        report.add("AGT054", "info", "agent",
                   "the instruction is built at runtime (an InstructionProvider); rule "
                   "coverage cannot be read statically", path=agent.file)
    elif llm_root and (agent.has_instruction or agent.has_static_instruction) and not rules:
        report.add("AGT052", "warn", "agent",
                   "the instruction contains no testable rule",
                   path=agent.file,
                   fix="state the order of operations and the refusals explicitly; each one "
                       "becomes an eval case and a restraint rubric")
    elif llm_root and branches == 0 and rules:
        report.add("AGT053", "info", "agent",
                   "the instruction has no conditional or prohibition, so there is only one "
                   "path to test",
                   path=agent.file,
                   fix="if the agent should ever refuse, say so in the instruction")

    # 6. caching against the suite: a cache only shows up from turn two
    docs = [load_evalset(path).doc for path in layout.evalsets]
    report.extend(cache_suite_findings(facts, docs))
    # 12. context: long conversations want compaction
    report.extend(compaction_suite_findings(facts, docs))
    # 13. state keys: a required placeholder nothing writes raises KeyError
    report.extend(state_placeholder_findings(facts, docs, layout.agent_dir))
    # 11. skills: lifecycle modes lean on an experimental flag for unload_skill
    report.extend(skill_lifecycle_findings(
        facts, env_enabled=dotenv_flag(layout.agent_dir, "ADK_ENABLE_SKILL_LIFECYCLE"),
        adk_version=args.adk_version))

    # 14. streaming tools: an async generator streams only under run_live
    configs = [load_config(path).doc for path in layout.configs]
    report.extend(streaming_tool_findings(
        facts, configs, env_model=dotenv_value(layout.agent_dir, "MODEL_NAME")))

    if not layout.evalsets:
        report.add_gap("GAP-EVALSET", "No evalset",
                       "nothing states what this agent is supposed to do",
                       "write the first cases with /adk:eval-create")

    # 8. environment
    backend = None
    if not args.no_backend:
        backend = detect_backend(layout.agent_dir)
        if backend.backend == "unknown":
            report.add("ENV003", "warn", "environment",
                       "no GenAI backend configured for this agent",
                       fix="set GOOGLE_GENAI_USE_VERTEXAI=TRUE with GOOGLE_CLOUD_PROJECT and "
                           "GOOGLE_CLOUD_LOCATION in the agent's .env, or GOOGLE_API_KEY")
        elif backend.missing:
            report.add("ENV004", "error", "environment",
                       f"backend is {backend.backend} but missing: {', '.join(backend.missing)}",
                       fix="add the missing variables to the agent's .env")
        if agent and agent.model_from_env and not backend.model_name:
            report.add("ENV006", "info", "environment",
                       "the agent reads its model from the environment, and MODEL_NAME is not "
                       "set here, so the code default applies",
                       fix="set MODEL_NAME in .env to be explicit")

    report.discovered = {
        "layout": layout.style,
        "agent_dir": layout.agent_dir,
        "export": exports.preferred_export if exports else None,
        "agent": agent.name if agent else None,
        "agent_type": agent.call if agent else None,
        # A null `agent`, `model` or empty `tools` means one of two very
        # different things: absent, or unreadable. This says which.
        "agent_unresolved": facts.agent_unresolved,
        "agent_unresolved_reason": facts.agent_unresolved_reason,
        "model": agent.model if agent else None,
        "model_policy": classify_model_id(agent.model) if agent else "unknown",
        "model_from_env": agent.model_from_env if agent else None,
        "tools": sorted(facts.bound_tool_names),
        "tools_unresolved": facts.tools_unresolved,
        "tools_defined_here": sorted(set(facts.tools) & facts.bound_tool_names),
        "opaque_tools": dict(sorted(facts.opaque_tools.items())),
        "pydantic_models": sorted(facts.models),
        "static_instruction_chars": agent.static_instruction_len if agent else 0,
        "static_instruction_dynamic": bool(agent and agent.static_instruction_dynamic),
        "cache_prefix_tokens_estimate": agent.cache_prefix_tokens_estimate if agent else 0,
        "app_exported": facts.app is not None,
        "plugins": facts.app.plugins if facts.app else [],
        "context_cache_config": _cache_config(facts),
        "events_compaction_config": _compaction_config(facts),
        "skills": [skill.to_dict() for skill in facts.skills],
        "skill_toolsets": [
            {"var": t.label, "discovery_mode": t.discovery_mode or "LAZY",
             "lifecycle_config": t.lifecycle_config, "registry": t.registry,
             "skills_unresolved": t.skills_unresolved,
             "unresolved_reason": t.unresolved_reason, "tools": t.tool_names}
            for t in facts.skill_toolsets
        ],
        "sop_rules": len(rules),
        "branches": branches,
        "evalsets": len(layout.evalsets),
        "backend": backend.backend if backend else "not checked",
    }

    print(report.render(args.format))
    if report.has_errors():
        return EXIT_FINDINGS
    if args.strict and report.count("warn"):
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
