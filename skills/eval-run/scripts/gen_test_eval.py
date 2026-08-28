#!/usr/bin/env python3
"""Give an agent that already exists a pytest gate that can actually fail.

`adk eval` returns 0 whether the suite passed or not, so a project whose only
evaluation step is that command has a gate that has never once blocked anything.
`AgentEvaluator` raises an `AssertionError` instead, which makes pytest's exit
code real. The cheapest way to get an honest gate on an existing project is
therefore a small `test_eval.py` — and writing one by hand is where the two
recurring mistakes happen:

* the eval config is left to auto-discovery, which only works when a single
  evalset sits beside `test_config.json`, so most projects silently evaluate
  against ADK's defaults rather than their own thresholds;
* `num_runs` is raised to "make it more reliable", which averages the scores
  across runs and hides exactly the variance that was being looked for.

This renders a file that does neither, parametrized over every evalset found so
a failure names the file it came from, with `num_runs` taken from
`ADK_EVAL_NUM_RUNS` (default 1) and an optional `--agent-name` for evaluating a
single sub-agent — something `adk eval` cannot do at all.

    gen_test_eval.py [path] [--out F] [--agent-name N] [--dry-run] [--force] [--json]

Exit codes: 0 written (or planned), 1 refused, 2 usage or nothing to evaluate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.templating import WritePlan, render  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "templates" / "test_eval.py.tmpl"

#: `adk eval` accepts both suffixes; a project almost always uses one of them.
GLOBS = {".evalset.json": "*.evalset.json", ".test.json": "*.test.json"}


def relative(target: Path | str, start: Path) -> str:
    """A `..`-style path from the test file to something else in the project."""
    return os.path.relpath(str(Path(target).resolve()), str(start.resolve()))


def choose_evalset_dir(evalsets: list[str]) -> tuple[Path, str]:
    """The directory holding the most evalsets, and the glob that matches them.

    A project that keeps its evalsets in one directory is the common case; when
    they are scattered, the biggest group is the one a single glob can serve and
    the rest are reported so the choice is visible.
    """
    by_parent: dict[str, list[str]] = {}
    for path in evalsets:
        by_parent.setdefault(str(Path(path).parent), []).append(path)
    parent = max(by_parent, key=lambda key: (len(by_parent[key]), key))
    members = by_parent[parent]
    suffix = ".test.json" if all(m.endswith(".test.json") for m in members) else ".evalset.json"
    return Path(parent), GLOBS[suffix]


def choose_config(configs: list[str], evalset_dir: Path) -> str | None:
    """The config nearest the evalsets, because that is the one that describes
    them; a config elsewhere in the tree usually belongs to another tier."""
    if not configs:
        return None
    for candidate in configs:
        if Path(candidate).parent == evalset_dir:
            return candidate
    for candidate in configs:
        if Path(candidate).parent == evalset_dir.parent:
            return candidate
    return configs[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="agent directory or project root")
    parser.add_argument("--out", help="where to write the test (default: <project>/tests/eval/test_eval.py)")
    parser.add_argument("--agent-name", help="evaluate this sub-agent instead of the root agent")
    parser.add_argument("--dry-run", action="store_true", help="show the write plan and stop")
    parser.add_argument("--force", action="store_true", help="replace an existing test file")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(target)
    if not layout.agent_dir:
        extra = (f"; found {len(layout.sibling_agents)} agents, pick one"
                 if layout.sibling_agents else "")
        print(f"error: no ADK agent under {target}{extra}", file=sys.stderr)
        return EXIT_USAGE
    if not layout.evalsets:
        print("error: no evalsets found; a pytest gate with nothing to assert is "
              "worse than none. Write cases with /adk:eval-create first.", file=sys.stderr)
        return EXIT_USAGE

    evalset_dir, glob = choose_evalset_dir(layout.evalsets)
    config = choose_config(layout.configs, evalset_dir)
    if not config:
        print("error: no eval config found. The generated test passes the config "
              "explicitly rather than relying on auto-discovery, so write one with "
              "/adk:eval-criteria first.", file=sys.stderr)
        return EXIT_USAGE

    out = Path(args.out) if args.out else Path(layout.project_root or target) / "tests" / "eval" / "test_eval.py"
    here = out.parent

    mapping = {
        # The agents directory is what `adk eval` puts on sys.path, and what the
        # module path is relative to - not the project root, which differs for a
        # flat layout.
        "PROJECT_ROOT_REL": relative(layout.agents_dir or layout.project_root or target, here),
        "AGENT_DIR_REL": relative(layout.agent_dir, here),
        "EVALSET_DIR_REL": relative(evalset_dir, here),
        "EVALSET_GLOB": glob,
        "CONFIG_REL": relative(config, here),
        "MODULE_PATH": layout.module_path or Path(layout.agent_dir).name,
        "AGENT_NAME_LITERAL": json.dumps(args.agent_name) if args.agent_name else "None",
    }

    content = render(TEMPLATE.read_text(encoding="utf-8"), mapping)
    plan = WritePlan()
    plan.add(out, content, force=args.force,
             reason="pytest gate over every evalset, with the config passed explicitly")

    matched = sorted(Path(p).name for p in layout.evalsets if Path(p).parent == evalset_dir)
    skipped = sorted(Path(p).name for p in layout.evalsets if Path(p).parent != evalset_dir)
    next_steps = [
        f"run it: python3 {Path(__file__).parent.name}/run_pytest_eval.py {layout.project_root}",
        "keep num_runs at 1; measure reliability with /adk:eval-run --runs N",
    ]
    if skipped:
        next_steps.append(
            f"{len(skipped)} evalset(s) outside {evalset_dir.name}/ are not covered "
            f"({', '.join(skipped)}); generate a second test with --out, or move them"
        )
    if args.agent_name:
        next_steps.append(
            f"--agent-name {args.agent_name} is honoured by this pytest path only; "
            "`adk eval` has no equivalent flag"
        )

    written = [] if args.dry_run else plan.apply()
    refused = [item for item in plan.items if item.action == "skip"]

    payload = {
        "out": str(out),
        "agent_dir": layout.agent_dir,
        "module_path": mapping["MODULE_PATH"],
        "agent_name": args.agent_name,
        "evalset_dir": str(evalset_dir),
        "evalset_glob": glob,
        "evalsets_matched": matched,
        "evalsets_not_matched": skipped,
        "config": config,
        "write_plan": plan.to_dict(),
        "written": written,
        "next_steps": next_steps,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"agent module: {mapping['MODULE_PATH']}"
              + (f"  (sub-agent {args.agent_name})" if args.agent_name else ""))
        print(f"evalsets:     {evalset_dir}/{glob} -> {', '.join(matched)}")
        print(f"config:       {config}")
        print()
        print(plan.to_md())
        if args.dry_run:
            print()
            print("--dry-run: nothing was written")
        for path in written:
            print(f"wrote {path}")
        print()
        print("Next:")
        for step in next_steps:
            print(f"  - {step}")

    if refused and not args.dry_run:
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
