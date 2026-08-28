#!/usr/bin/env python3
"""Check every expected tool call in an evalset against the agent's real tools.

Trajectory matching compares tool name and arguments exactly. An expected call
that names a tool the agent does not have, or passes an argument its schema
rejects, describes behaviour that can never happen - so the case fails forever
and tells you nothing about the agent.

Tools that take a single Pydantic model are called with nested arguments, so a
tool defined as `submit_claim(req: ClaimRequest)` expects
`{"req": {"claim_id": ...}}`. This script resolves that nesting before checking.

    crosscheck_tools.py <evalset | directory> --agent-dir DIR [--format json|md]

Exit codes: 0 clean, 1 findings, 2 bad target.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.evalset import iter_cases, iter_tool_uses, load_evalset  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.tools_ast import (  # noqa: E402
    ModuleFacts, ToolSpec, extract_module, resolve_tool, tool_param_constraints,
)

_SRC = "adk-python: evaluation/trajectory_evaluator.py"


def _flatten(args: Any, tool: ToolSpec) -> dict[str, Any]:
    """Flatten arguments into the dotted keys a constraint map uses.

    Only a parameter typed as a Pydantic model is descended into: that nesting
    is part of the declaration, so `req.claim_id` is a real field name. A
    parameter simply typed `dict` is opaque - its keys belong to the caller, and
    flattening them invents parameters the tool never declared and then reports
    each one as unknown.
    """
    flat: dict[str, Any] = {}
    if not isinstance(args, dict):
        return flat
    models = {p.name for p in tool.schema_params if p.model}
    for key, value in args.items():
        if key in models and isinstance(value, dict):
            for inner_key, inner_value in value.items():
                flat[f"{key}.{inner_key}"] = inner_value
        else:
            flat[key] = value
    return flat


def check_call(
    report: GapReport, facts: ModuleFacts, name: str, args: Any, path: str, locator: str
) -> None:
    # One resolver decides what EVS036 means here and in validate_evalset, so
    # the two never disagree about the same project.
    binding = resolve_tool(facts, name)
    if binding.status == "unknown":
        report.add("EVS036", "error", "evalset",
                   f"expected call to {name!r}, which the agent does not define",
                   path=path, locator=locator,
                   fix="fix the spelling, or add the tool; a name that never fires "
                       "can never match",
                   source=_SRC)
        return
    if binding.spec is None:
        report.add("EVS038", "info", "evalset",
                   f"tool {name!r} is real but has no readable definition here "
                   f"({binding.reason or binding.status}), so its arguments were not checked",
                   path=path, locator=locator)
        return

    tool = binding.spec
    constraints = tool_param_constraints(tool, facts.models)
    supplied = _flatten(args, tool)

    # A tool taking one Pydantic model expects its arguments nested under that
    # parameter name; flat arguments will not validate.
    nested_params = [p.name for p in tool.schema_params if p.model]
    if nested_params and args and not any(key.startswith(f"{p}.") for p in nested_params for key in supplied):
        report.add("EVS039", "error", "evalset",
                   f"{name!r} takes a model parameter, so arguments must be nested under "
                   f"{nested_params[0]!r}",
                   path=path, locator=locator,
                   fix=f'write "args": {{"{nested_params[0]}": {{...}}}}',
                   source="adk-python: tools/function_tool.py")
        return

    for key, spec in constraints.items():
        if spec.required and key not in supplied:
            report.add("EVS042", "warn", "evalset",
                       f"{name!r} requires {key!r}, which this call omits",
                       path=path, locator=locator,
                       fix="ADK turns a missing argument into an error response rather than "
                           "an exception, so the agent continues and the case fails later",
                       source="adk-python: flows/llm_flows/functions.py")

    for key, value in supplied.items():
        spec = constraints.get(key)
        if spec is None:
            if tool.has_var_kwargs:
                # `**kwargs` means the declaration is open, so an argument this
                # map does not list is not an argument the tool rejects.
                continue
            report.add("EVS043", "warn", "evalset",
                       f"{name!r} has no parameter {key!r}",
                       path=path, locator=locator,
                       fix=f"expected one of: {', '.join(sorted(constraints)) or '(none)'}",
                       source=_SRC)
            continue
        if spec.literal_values and value not in spec.literal_values:
            report.add("EVS044", "error", "evalset",
                       f"{key}={value!r} is not one of {spec.literal_values}",
                       path=path, locator=locator,
                       fix="the schema restricts this field, so the call would be rejected",
                       source=_SRC)
        if spec.pattern and isinstance(value, str) and not re.match(spec.pattern, value):
            report.add("EVS045", "error", "evalset",
                       f"{key}={value!r} does not match {spec.pattern}",
                       path=path, locator=locator,
                       fix="fix the value, or relax the pattern in the tool's schema",
                       source=_SRC)
        for bound, symbol, ok in (
            (spec.ge, ">=", lambda v, b: v >= b),
            (spec.le, "<=", lambda v, b: v <= b),
            (spec.gt, ">", lambda v, b: v > b),
            (spec.lt, "<", lambda v, b: v < b),
        ):
            if bound is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
                if not ok(value, bound):
                    report.add("EVS046", "error", "evalset",
                               f"{key}={value!r} breaks the constraint {symbol} {bound}",
                               path=path, locator=locator, source=_SRC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="evalset file or directory")
    parser.add_argument("--agent-dir", help="agent directory (inferred when omitted)")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    agent_dir = args.agent_dir
    if not agent_dir:
        layout = detect_layout(target if target.is_dir() else target.parent)
        agent_dir = layout.agent_dir
    if not agent_dir or not Path(agent_dir).exists():
        print("error: no agent found; pass --agent-dir", file=sys.stderr)
        return EXIT_USAGE

    facts = extract_module(agent_dir)
    evalsets = [target] if target.is_file() else [
        p for p in sorted(target.rglob("*")) if p.name.endswith((".evalset.json", ".test.json"))
    ]
    if not evalsets:
        print(f"error: no evalset files under {target}", file=sys.stderr)
        return EXIT_USAGE

    report = GapReport(subject=str(target), skill="eval-review")
    report.discovered = {
        "agent_dir": agent_dir,
        "tools": sorted(facts.bound_tool_names),
        "opaque_tools": sorted(facts.opaque_tools),
        "evalsets": [str(p) for p in evalsets],
    }

    checked = 0
    for path in evalsets:
        loaded = load_evalset(path)
        if loaded.fmt != "evalset" or not isinstance(loaded.doc, dict):
            continue
        for case in iter_cases(loaded.doc):
            eval_id = case.get("eval_id", "?")
            for turn, tool_use in iter_tool_uses(case):
                checked += 1
                check_call(
                    report, facts,
                    str(tool_use.get("name") or "?"), tool_use.get("args"),
                    str(path), f"{eval_id} turn {turn + 1}",
                )
    report.discovered["calls_checked"] = checked

    print(report.render(args.format))
    return EXIT_FINDINGS if report.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
