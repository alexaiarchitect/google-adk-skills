#!/usr/bin/env python3
"""Scaffold an eval-ready ADK agent.

"Eval-ready" is a set of concrete choices, not a style:

* **Arguments have contracts.** Each tool takes a Pydantic model, so an eval case
  has something exact to assert and a wrong value is caught at the boundary.
* **Tools return errors instead of raising.** A validation failure comes back as
  a result the model can read and correct; an exception ends the invocation.
* **The instruction is ordered and explicit.** Numbered steps and stated
  prohibitions are what a coverage matrix can count and an eval case can check.
* **The model id comes from the environment.** `MODEL_NAME`, defaulting to an
  alias, so a model retirement does not rewrite the agent.
* **An `App` is exported.** The loader prefers it, and plugins and caching only
  reach an eval run through one.

Tools are given as a compact spec::

    --tools "lookup_policy:category=literal(travel,meals);check_funds:cost_centre=str,amount_usd=float"

    scaffold_agent.py --name claims_agent --dir ./claims --layout recipes \
        --tools "..." --description "..." --sop-file sop.md [--dry-run]

`--workflow` swaps the single prompted agent for a graph: two deterministic
function nodes and one `Agent` node, wired in a fixed order. It composes with
either layout. Reach for it when the order of operations is a rule rather than a
preference - a step enforced by an edge needs no eval case to defend it, because
the model was never asked.

`--with-cache` splits the instruction for context caching: the procedure (the
SOP) becomes a literal `static_instruction`, the byte-identical prefix the
cache stores, a short `instruction` carries the per-turn part, and the `App`
gets `context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=600,
cache_intervals=5)`. The flat layout gains an `App` for it. Caching starts on a
session's second turn, so it only pays off for multi-turn conversations with a
long procedure; it does not combine with `--workflow`.

Exit codes: 0 written, 1 refused (name or existing files), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.layout import APP_NAME_RE  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.templating import WritePlan, render  # noqa: E402

TEMPLATES = Path(__file__).resolve().parents[1] / "assets" / "templates"
#: The pytest gate is the same file whether it is scaffolded with a new agent
#: or added to one that exists, so eval-run owns the single copy and this
#: skill reads it rather than keeping a second one that can drift.
EVAL_RUN_TEMPLATES = (
    Path(__file__).resolve().parents[2] / "eval-run" / "assets" / "templates"
)

TYPE_MAP = {"str": "str", "int": "int", "float": "float", "bool": "bool"}

DEFAULT_INSTRUCTION = """You are {description}

Follow these steps in order:
{steps}

{prohibitions}

When you refuse, name the constraint you hit and the value that broke it.

If a tool returns a schema validation error, read the rule in the message,
correct the argument, and retry once. Do not report a validation error to the
user as a refusal."""


def parse_tools(spec: str) -> list[dict[str, Any]]:
    """`name:field=type,field=type;name:...` into tool descriptors."""
    tools: list[dict[str, Any]] = []
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, fields_text = chunk.partition(":")
        name = name.strip()
        if not name.isidentifier():
            raise ValueError(f"{name!r} is not a valid Python function name")
        fields: list[dict[str, Any]] = []
        for field_text in (f for f in fields_text.split(",") if f.strip()):
            field_name, _, type_text = field_text.strip().partition("=")
            field_name = field_name.strip()
            type_text = (type_text or "str").strip()
            if not field_name.isidentifier():
                raise ValueError(f"{field_name!r} is not a valid field name")
            literal = re.match(r"literal\((.*)\)$", type_text, re.I)
            pattern = re.match(r"pattern\((.*)\)$", type_text, re.I)
            if literal:
                values = [v.strip() for v in literal.group(1).split("|") if v.strip()]
                if not values:
                    values = [v.strip() for v in literal.group(1).split(",") if v.strip()]
                fields.append({"name": field_name, "kind": "literal", "values": values})
            elif pattern:
                fields.append({"name": field_name, "kind": "pattern", "pattern": pattern.group(1)})
            elif type_text in TYPE_MAP:
                fields.append({"name": field_name, "kind": "scalar", "type": TYPE_MAP[type_text]})
            else:
                raise ValueError(
                    f"unknown type {type_text!r} for {field_name!r}; use "
                    "str, int, float, bool, literal(a|b), or pattern(^RE$)"
                )
        tools.append({"name": name, "fields": fields, "model": model_name_for(name)})
    return tools


def model_name_for(tool_name: str) -> str:
    return "".join(part.title() for part in tool_name.split("_")) + "Request"


def render_schema(tool: dict[str, Any]) -> str:
    lines = [f'class {tool["model"]}(BaseModel):',
             f'    """Arguments for `{tool["name"]}`."""', ""]
    if not tool["fields"]:
        lines.append("    pass")
        return "\n".join(lines)
    for field in tool["fields"]:
        if field["kind"] == "literal":
            values = ", ".join(json.dumps(v) for v in field["values"])
            lines.append(f'    {field["name"]}: Literal[{values}] = Field(')
            lines.append(f'        description="One of: {", ".join(field["values"])}."')
            lines.append("    )")
        elif field["kind"] == "pattern":
            lines.append(f'    {field["name"]}: str = Field(')
            lines.append(f'        pattern=r"{field["pattern"]}",')
            lines.append(f'        description="Must match {field["pattern"]}.",')
            lines.append("    )")
        else:
            extra = "ge=0.0, " if field["type"] == "float" else ""
            lines.append(f'    {field["name"]}: {field["type"]} = Field(')
            lines.append(f'        {extra}description="TODO: say what this means to the model.",')
            lines.append("    )")
    return "\n".join(lines)


def render_tool(tool: dict[str, Any]) -> str:
    field_names = ", ".join(f["name"] for f in tool["fields"]) or "no arguments"
    return f'''def {tool["name"]}(req: {tool["model"]}) -> dict[str, Any]:
    """TODO: one line on what this does.

    TODO: say when the model should call it, and when it should not. This text is
    the entire description the model gets, so vagueness here shows up as the
    wrong tool being called.

    Args:
        req: A {tool["model"]} ({field_names}).

    Returns:
        A dict with `status` ("ok" or "error") and the result fields. A schema
        failure is returned, not raised, so the model can correct and retry.
    """
    try:
        req = {tool["model"]}.model_validate(req)
    except ValidationError as exc:
        return {{"status": "error", "error": f"schema validation failed: {{exc}}"}}

    # TODO: the real work.
    return {{"status": "ok"}}'''


def build_instruction(description: str, tools: list[dict[str, Any]], sop: str | None) -> str:
    if sop:
        return sop.strip()
    steps = "\n".join(
        f"{index}. Call `{tool['name']}` when TODO: state the condition."
        for index, tool in enumerate(tools, start=1)
    ) or "1. TODO: state the first step."
    prohibitions = "\n".join(
        f"Do NOT call `{tool['name']}` when TODO: state the condition."
        for tool in tools[1:]
    ) or "Do NOT act when TODO: state the condition."
    return DEFAULT_INSTRUCTION.format(description=description, steps=steps, prohibitions=prohibitions)


def starter_evalset(agent_name: str, app_name: str, tools: list[dict[str, Any]],
                    *, multi_turn: bool = False) -> str:
    calls = [{"name": tool["name"], "args": {"req": {
        field["name"]: (field["values"][0] if field["kind"] == "literal"
                        else "TODO" if field["kind"] == "pattern"
                        else (0 if field["type"] in ("int", "float") else "TODO"))
        for field in tool["fields"]
    }}} for tool in tools[:2]]
    document = {
        "eval_set_id": "happy",
        "name": f"{agent_name} happy path",
        "description": "Replace the placeholders with a request that should succeed end to end.",
        "eval_cases": [{
            "eval_id": "happy_TODO_full_pipeline",
            "conversation": [{
                "invocation_id": "inv_1",
                "user_content": {"parts": [{"text": "TODO: a request that should succeed."}],
                                 "role": "user"},
                "final_response": {"parts": [{"text": ""}], "role": "model"},
                "intermediate_data": {"intermediate_responses": [], "tool_uses": calls},
            }],
            "session_input": {"app_name": app_name, "user_id": "evaluator", "state": {}},
        }],
    }
    if multi_turn:
        # A cache is never created on a session's first request, so only a case
        # with a second turn can show `cached_tokens` in the results.
        document["eval_cases"].append({
            "eval_id": "happy_TODO_follow_up_turn",
            "conversation": [
                {
                    "invocation_id": "inv_1",
                    "user_content": {"parts": [{"text": "TODO: a request that should succeed."}],
                                     "role": "user"},
                    "final_response": {"parts": [{"text": ""}], "role": "model"},
                    "intermediate_data": {"intermediate_responses": [], "tool_uses": calls},
                },
                {
                    "invocation_id": "inv_2",
                    "user_content": {"parts": [{"text": "TODO: a follow-up question about "
                                                        "the same request."}],
                                     "role": "user"},
                    "final_response": {"parts": [{"text": ""}], "role": "model"},
                    "intermediate_data": {"intermediate_responses": [], "tool_uses": []},
                },
            ],
            "session_input": {"app_name": app_name, "user_id": "evaluator", "state": {}},
        })
    return json.dumps(document, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="the agent's name (a Python identifier)")
    parser.add_argument("--dir", required=True, help="project directory to create")
    parser.add_argument("--layout", choices=("recipes", "flat"), default="recipes")
    parser.add_argument("--workflow", action="store_true",
                        help="scaffold a graph workflow (two function nodes plus an "
                             "Agent node) instead of a single prompted agent")
    parser.add_argument("--with-cache", action="store_true",
                        help="put the SOP in static_instruction and enable context caching "
                             "on the App (ContextCacheConfig min_tokens=2048, ttl_seconds=600, "
                             "cache_intervals=5)")
    parser.add_argument("--tools", default="", help="name:field=type,...;name:...")
    parser.add_argument("--description", default="", help="one line; the routing model reads this")
    parser.add_argument("--sop-file", help="a file holding the instruction, instead of a template")
    parser.add_argument("--app-name", help="App name / agent directory name (recipes: defaults to `app`)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.name.isidentifier() or args.name == "user":
        print(f"error: agent name {args.name!r} must be a Python identifier and not \"user\"",
              file=sys.stderr)
        return EXIT_FINDINGS

    if args.with_cache and args.workflow:
        print("error: --with-cache does not combine with --workflow; scaffold the workflow, "
              "then set static_instruction on the Agent node and context_cache_config on "
              "the App by hand", file=sys.stderr)
        return EXIT_USAGE

    try:
        tools = parse_tools(args.tools)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not tools:
        print("error: --tools is required; an agent with no tools has no trajectory to evaluate",
              file=sys.stderr)
        return EXIT_USAGE

    root = Path(args.dir)
    app_name = args.app_name or ("app" if args.layout == "recipes" else root.name)
    if not APP_NAME_RE.match(app_name):
        print(f"error: app name {app_name!r} must start with a letter and use only "
              "letters, digits, underscores and hyphens", file=sys.stderr)
        return EXIT_FINDINGS

    sop = Path(args.sop_file).read_text(encoding="utf-8") if args.sop_file else None
    description = args.description or f"an agent named {args.name}."
    mapping = {
        "AGENT_NAME": args.name,
        "AGENT_DESCRIPTION": description,
        "APP_NAME": app_name,
        "PROJECT_NAME": root.name,
        "INSTRUCTION": build_instruction(description, tools, sop),
        "TOOL_NAMES": ", ".join(t["name"] for t in tools),
        "SCHEMA_NAMES": ", ".join(t["model"] for t in tools),
        "SCHEMA_CLASSES": "\n\n\n".join(render_schema(t) for t in tools),
        "TOOL_FUNCTIONS": "\n\n\n".join(render_tool(t) for t in tools),
        "MODULE_PATH": f"{app_name}.agent" if args.layout == "recipes" else root.name,
        # Paths in the pytest gate are relative to the test file, so the suite
        # runs from any directory. `PROJECT_ROOT_REL` points at the agents
        # directory - what ADK puts on sys.path - not at the project root.
        "PROJECT_ROOT_REL": "../..",
        "AGENT_DIR_REL": f"../../{app_name}",
        "EVALSET_DIR_REL": "evalsets",
        "EVALSET_GLOB": "*.evalset.json",
        "CONFIG_REL": "eval_config.json",
        "AGENT_NAME_LITERAL": "None",
        # The workflow template names one tool in its TODOs, so the scaffold
        # points at a real function rather than a placeholder.
        "FIRST_TOOL": tools[0]["name"],
    }

    plan = WritePlan()

    def add_template(relative: str, destination: Path, *, base: Path = TEMPLATES) -> None:
        source = base / relative
        plan.add(destination, render(source.read_text(encoding="utf-8"), mapping), force=args.force)

    # The workflow module inlines its schemas and tools, so the split
    # tools.py / schemas.py pair would be a second, diverging copy.
    agent_file = "agent_cached.py.tmpl" if args.with_cache else "agent.py.tmpl"
    agent_template = ("workflow/agent_workflow.py.tmpl" if args.workflow
                      else f"flat/{agent_file}"
                      if args.layout == "flat" else f"recipes/app/{agent_file}")

    if args.layout == "recipes":
        add_template("recipes/app/__init__.py.tmpl", root / app_name / "__init__.py")
        add_template(agent_template, root / app_name / "agent.py")
        if not args.workflow:
            add_template("recipes/app/tools.py.tmpl", root / app_name / "tools.py")
            add_template("recipes/app/schemas.py.tmpl", root / app_name / "schemas.py")
        add_template("recipes/.env.example.tmpl", root / ".env.example")
        add_template("recipes/pyproject.toml.tmpl", root / "pyproject.toml")
        add_template("recipes/README.md.tmpl", root / "README.md")
        add_template("recipes/tests/eval/eval_config.json.tmpl", root / "tests/eval/eval_config.json")
        add_template("test_eval.py.tmpl", root / "tests/eval/test_eval.py",
                     base=EVAL_RUN_TEMPLATES)
        plan.add(root / "tests/eval/evalsets/happy.evalset.json",
                 starter_evalset(args.name, app_name, tools, multi_turn=args.with_cache),
                 force=args.force)
    else:
        add_template("flat/__init__.py.tmpl", root / app_name / "__init__.py")
        add_template(agent_template, root / app_name / "agent.py")
        add_template("flat/.env.example.tmpl", root / app_name / ".env.example")
        add_template("flat/test_config.json.tmpl", root / app_name / "test_config.json")
        plan.add(root / app_name / "happy.test.json",
                 starter_evalset(args.name, app_name, tools, multi_turn=args.with_cache),
                 force=args.force)

    next_steps = [
        "fill in the TODOs in the tool docstrings: they are the model's only description",
    ] + ([
        "move the deterministic rules into `apply_policy`: every rule decided in a "
        "node is a branch the eval suite does not have to cover",
        "read references/workflow-graphs.md before adding nodes - a node that sets "
        "its output twice raises",
    ] if args.workflow else []) + ([
        "keep `STATIC_INSTRUCTION` free of anything that varies (dates, names, "
        "f-strings): it is the cached prefix; per-turn context goes in `INSTRUCTION`",
        "add a multi-turn eval case: caching starts on the second turn, and "
        "`cached_tokens` in the results is how you see it working",
        "the cache only engages once the static prefix passes the model floor (2048 "
        "tokens on Gemini 2.5, 4096 on Gemini 3); a short procedure gets AGT067 from "
        "/adk:agent-review, and then caching is overhead rather than a saving",
    ] if args.with_cache else []) + [
        "state the real ordering and refusal rules in the instruction",
        "replace the placeholder eval case with a request that should succeed",
        "then: /adk:eval-create to cover each branch, /adk:eval-criteria to choose metrics",
    ]

    if args.json:
        print(json.dumps({
            "agent": args.name, "app_name": app_name, "layout": args.layout,
            "workflow": args.workflow,
            "with_cache": args.with_cache,
            "tools": [t["name"] for t in tools],
            "write_plan": plan.to_dict(),
            "written": [] if args.dry_run else plan.apply(),
            "next_steps": next_steps,
        }, indent=2))
        return EXIT_OK

    print(plan.to_md())
    if not args.dry_run:
        written = plan.apply()
        print()
        for path in written:
            print(f"wrote {path}")
        skipped = [i for i in plan.items if i.action == "skip"]
        if skipped:
            print(f"{len(skipped)} file(s) left alone because they exist; pass --force to replace")
    print()
    print("Next:")
    for step in next_steps:
        print(f"  - {step}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
