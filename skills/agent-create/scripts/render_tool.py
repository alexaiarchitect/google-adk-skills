#!/usr/bin/env python3
"""Add one tool to an agent that already exists, with its argument contract.

A tool added by hand usually arrives without the two things that make it
evaluable: a Pydantic model for its arguments, and a docstring written as an
interface rather than a note to self. Without the model, an eval case can only
assert that the tool was called, never that it was called with the right claim
id. Without the docstring, the model is guessing what the tool is for, because
the docstring is the entire description it receives.

So this renders the same shape the scaffold produces - schema class, validating
function, `{"status": ...}` return - into the files the project already uses,
and binds the name into the agent's `tools=[...]` list when that list is a
literal it can edit safely.

It refuses to touch a name that exists. Redefining a tool in place is how an
agent silently changes behaviour that eval cases were written against; adding a
second tool with a clearer name is the reversible move.

    render_tool.py [path] --tool "name:field=type,field=type" [--dry-run] [--json]

Types: `str`, `int`, `float`, `bool`, `literal(a|b|c)`, `pattern(^RE$)`.

Exit codes: 0 written (or planned), 1 refused (name taken, nowhere to put it),
2 usage error.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
# The tool-spec grammar has exactly one definition, next door in the scaffold.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.templating import WritePlan  # noqa: E402
from adk_skills.tools_ast import extract_module  # noqa: E402
from scaffold_agent import parse_tools, render_schema  # noqa: E402
from scaffold_agent import render_tool as render_tool_source  # noqa: E402


def defined_names(paths: list[str]) -> dict[str, str]:
    """Every module-level function and class in the package, to its file."""
    found: dict[str, str] = {}
    for path in paths:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.setdefault(node.name, path)
    return found


def find_tools_list(path: Path) -> tuple[int, int, bool] | None:
    """Locate a literal ``tools=[...]`` so the new name can be spliced in.

    Returns the offsets of the closing bracket plus whether the list is empty,
    or None when the call does not use a literal list - `tools=TOOLS` or a
    computed list is left alone rather than rewritten by guesswork.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    hits: list[tuple[int, int, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "tools" or not isinstance(keyword.value, ast.List):
                continue
            listing = keyword.value
            if listing.end_lineno is None or listing.end_col_offset is None:
                continue
            hits.append((listing.end_lineno, listing.end_col_offset, not listing.elts))
    return hits[0] if len(hits) == 1 else None


def splice_tool_name(text: str, position: tuple[int, int, bool], name: str) -> str:
    """Insert `name` just before the list's closing bracket."""
    end_lineno, end_col, empty = position
    lines = text.splitlines(keepends=True)
    line = lines[end_lineno - 1]
    insert_at = end_col - 1                     # just inside the `]`
    addition = name if empty else f", {name}"
    lines[end_lineno - 1] = line[:insert_at] + addition + line[insert_at:]
    return "".join(lines)


def splice_import(text: str, module: str, name: str) -> str | None:
    """Add `name` to an existing `from <module> import ...` line.

    Only a plain, single-line import is rewritten. A parenthesised or aliased
    import is left for a person, because a half-correct edit to an import line
    is worse than a note saying what to add.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module != module.lstrip("."):
            continue
        if any(alias.asname for alias in node.names):
            return None
        if node.end_lineno is None or node.lineno != node.end_lineno:
            return None
        if name in {alias.name for alias in node.names}:
            return text
        lines = text.splitlines(keepends=True)
        line = lines[node.lineno - 1]
        stripped = line.rstrip("\n")
        newline = line[len(stripped):]
        lines[node.lineno - 1] = f"{stripped}, {name}{newline}"
        return "".join(lines)
    return None


def missing_imports(text: str, needed: tuple[str, ...]) -> list[str]:
    """Names the rendered tool uses that the target file does not import."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    available: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            available.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            available.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return [name for name in needed if name not in available]


def append_block(path: Path, block: str) -> str:
    body = path.read_text(encoding="utf-8").rstrip("\n")
    return f"{body}\n\n\n{block}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent directory or project root")
    parser.add_argument("--tool", required=True, help="name:field=type,field=type")
    parser.add_argument("--no-bind", action="store_true",
                        help="write the tool but do not add it to tools=[...]")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    try:
        specs = parse_tools(args.tool)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if len(specs) != 1:
        print("error: --tool takes exactly one tool; run it again for the next one",
              file=sys.stderr)
        return EXIT_USAGE
    tool = specs[0]

    layout = detect_layout(target)
    if not layout.agent_dir:
        extra = (f"; found {len(layout.sibling_agents)} agents, pick one"
                 if layout.sibling_agents else "")
        print(f"error: no ADK agent under {target}{extra}", file=sys.stderr)
        return EXIT_USAGE

    agent_dir = Path(layout.agent_dir)
    existing = defined_names(layout.python_files)
    clashes = [name for name in (tool["name"], tool["model"]) if name in existing]
    if clashes:
        print(
            "error: " + ", ".join(f"{name} is already defined in {existing[name]}"
                                  for name in clashes)
            + ".\nRedefining a tool in place changes behaviour that existing eval "
              "cases were written against. Add a differently named tool, or change "
              "that one deliberately.",
            file=sys.stderr,
        )
        return EXIT_FINDINGS

    tools_py = agent_dir / "tools.py"
    schemas_py = agent_dir / "schemas.py"
    agent_py = Path(layout.agent_module_file or (agent_dir / "agent.py"))
    if agent_py.suffix != ".py":
        print(f"error: {agent_py} is not a Python module; this agent is "
              f"{layout.style}-style and has to be edited by hand", file=sys.stderr)
        return EXIT_FINDINGS

    split = tools_py.is_file() and schemas_py.is_file()
    schema_target = schemas_py if split else agent_py
    tool_target = tools_py if split else agent_py

    plan = WritePlan()
    notes: list[str] = []
    contents: dict[Path, str] = {}

    schema_block = render_schema(tool)
    tool_block = render_tool_source(tool)

    if schema_target == tool_target:
        contents[tool_target] = append_block(tool_target, f"{schema_block}\n\n\n{tool_block}")
    else:
        contents[schema_target] = append_block(schema_target, schema_block)
        # tools.py imports its models from schemas.py in this layout, so the
        # model has to reach it before the function that annotates with it.
        with_import = splice_import(
            tools_py.read_text(encoding="utf-8"), "schemas", tool["model"]
        )
        base = with_import if with_import is not None else tools_py.read_text(encoding="utf-8")
        contents[tool_target] = base.rstrip("\n") + f"\n\n\n{tool_block}\n"
        if with_import is None:
            notes.append(f"import {tool['model']} into {tools_py.name} by hand "
                         "(its import line is not a plain single-line form)")

    bound = False
    if not args.no_bind:
        # The agent construction may be in a different file from the tools.
        bind_target = agent_py
        position = find_tools_list(bind_target)
        if position:
            base = contents.get(bind_target) or bind_target.read_text(encoding="utf-8")
            spliced = splice_tool_name(base, position, tool["name"])
            if split:
                with_import = splice_import(spliced, "tools", tool["name"])
                if with_import is not None:
                    spliced = with_import
                else:
                    notes.append(f"import {tool['name']} into {agent_py.name} by hand "
                                 "(its import line is not a plain single-line form)")
            contents[bind_target] = spliced
            bound = True
        else:
            notes.append(
                f"bind it yourself: add `{tool['name']}` to the agent's `tools=[...]` "
                "(no single literal list was found, so nothing was rewritten)"
            )

    for path, body in contents.items():
        plan.add(path, body, force=True,
                 reason=f"adds {tool['name']}" + (" and binds it" if bound and path == agent_py else ""))

    # The rendered body uses both of these; a file that scaffolded its tools
    # inline may not have imported them.
    absent = missing_imports(contents.get(tool_target, ""), ("Any", "ValidationError"))
    for name in absent:
        source = "typing" if name == "Any" else "pydantic"
        notes.append(f"add `from {source} import {name}` to {Path(tool_target).name}: "
                     "the generated tool body uses it")

    notes.append("replace the TODOs in the docstring: it is the whole description "
                 "the model gets")
    notes.append("then add an eval case for the new branch: /adk:eval-create")

    written = [] if args.dry_run else plan.apply()
    after = extract_module(agent_dir) if written else None

    payload = {
        "agent_dir": str(agent_dir),
        "tool": tool["name"],
        "model": tool["model"],
        "fields": [f["name"] for f in tool["fields"]],
        "schema_file": str(schema_target),
        "tool_file": str(tool_target),
        "bound": bound,
        "write_plan": plan.to_dict(),
        "written": written,
        "bound_tools_after": sorted(after.bound_tool_names) if after else [],
        "next_steps": notes,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"+ {tool['name']}({', '.join(payload['fields']) or 'no arguments'}) "
              f"-> {tool['model']}")
        print(f"  schema: {schema_target}")
        print(f"  tool:   {tool_target}")
        print(f"  bound:  {'yes' if bound else 'no'}")
        print()
        print(plan.to_md())
        if args.dry_run:
            print()
            print("--dry-run: nothing was written")
        for path in written:
            print(f"wrote {path}")
        print()
        print("Next:")
        for note in notes:
            print(f"  - {note}")

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
