#!/usr/bin/env python3
"""Run several eval suites from one manifest, and gate on the whole batch.

Once a project has more than one agent, or more than one tier of evals, the
question "is everything still green?" needs a single command and a single exit
code. The manifest says what to run:

    {
      "schema": "adk-skills.registry/1",
      "suites": [
        {"name": "trajectory_pr", "tier": "pr", "agent_dir": "app",
         "evalsets": ["tests/eval/evalsets/happy.evalset.json"],
         "config": "tests/eval/eval_config.json", "runs": 1},
        {"name": "judge_nightly", "tier": "nightly", "agent_dir": "app",
         "evalsets": ["tests/eval/evalsets/*.evalset.json"],
         "config": "tests/eval/eval_config.nightly.json", "runs": 5}
      ]
    }

Paths are relative to the manifest. `--tier pr` runs only the cheap
deterministic suites, which is what a per-change gate should do.

A suite with `"frozen": true` is a golden baseline: `manage_cases.py` refuses
to rename, move, split, remove, dedupe or re-point its cases (EVS120) unless
given `--unfreeze`. Freezing changes nothing about how a suite runs; it stops
the cases being tuned until they pass. `--init` writes `"frozen": false` on
every suite, so freezing one is a deliberate edit.

    run_registry.py --registry eval_registry.json [--tier pr|nightly] [--json]

Writing that manifest by hand for a repository with several agents is tedious
and easy to get subtly wrong, so `--init` builds one by discovery: every
directory the ADK loader would accept becomes a suite, its evalsets and config
are filled in from what is actually there, and the tier follows the cost of the
config - a deterministic config gates pull requests, a judged one goes nightly.

    run_registry.py --init [--root DIR] [--out F] [--dry-run] [--force] [--json]

Exit codes: 0 all clean, 1 something failed, 2 the manifest or environment is unusable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.adk_subprocess import describe_runner, run_adk_eval  # noqa: E402
from adk_skills.env import resolve_runner  # noqa: E402
from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.layout import detect_layout, find_agent_dirs  # noqa: E402
from adk_skills.metrics import cost_tier  # noqa: E402
from adk_skills.registry import SCHEMA, resolve_paths  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.templating import WritePlan, dumps_json  # noqa: E402

def build_manifest(root: Path, out: Path) -> tuple[dict, list[str]]:
    """A registry describing every agent found under `root`.

    Paths are stored relative to the manifest, because that is what makes a
    registry committable and usable from a CI checkout at a different absolute
    path.
    """
    base = out.parent.resolve()
    suites: list[dict] = []
    notes: list[str] = []

    for agent_dir in find_agent_dirs(root):
        layout = detect_layout(agent_dir)
        name = Path(agent_dir).name
        if not layout.evalsets:
            notes.append(f"{name}: no evalsets, so no suite was written "
                         "(write cases with /adk:eval-create)")
            continue

        config = None
        for candidate in layout.configs:
            if Path(candidate).name in ("test_config.json", "eval_config.json"):
                config = candidate
                break

        metrics: list[str] = []
        if config:
            loaded = load_config(config)
            if loaded.ok:
                metrics = loaded.metric_names()
        tier_cost = cost_tier(metrics) if metrics else "free"
        # A judged suite is too slow and too expensive to gate every change on,
        # so it goes nightly and the deterministic one guards pull requests.
        tier = "pr" if tier_cost == "free" else "nightly"

        suites.append({
            "name": name,
            "tier": tier,
            "agent_dir": relative_to(agent_dir, base),
            "evalsets": [relative_to(path, base) for path in layout.evalsets],
            **({"config": relative_to(config, base)} if config else {}),
            "runs": 1 if tier == "pr" else 5,
            "cost_tier": tier_cost,
            # Set it to true once the cases are the golden baseline; then
            # manage_cases.py refuses to edit them without --unfreeze.
            "frozen": False,
        })
        if not config:
            notes.append(f"{name}: no eval config, so ADK's defaults apply "
                         "(tool_trajectory_avg_score 1.0, response_match_score 0.8)")

    return {"schema": SCHEMA, "suites": suites}, notes


def relative_to(path: Path | str, base: Path) -> str:
    return os.path.relpath(str(Path(path).resolve()), str(base))


def init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return EXIT_USAGE

    out = Path(args.out) if args.out else root / "eval_registry.json"
    manifest, notes = build_manifest(root, out)
    if not manifest["suites"]:
        print(f"error: no agent with evalsets found under {root}; there is nothing "
              "to put in a registry yet", file=sys.stderr)
        return EXIT_USAGE

    plan = WritePlan()
    plan.add(out, dumps_json(manifest), force=args.force,
             reason=f"{len(manifest['suites'])} suite(s) discovered under {root}")
    written = [] if args.dry_run else plan.apply()
    refused = [item for item in plan.items if item.action == "skip"]

    if args.json:
        print(json.dumps({"root": str(root), "out": str(out), "manifest": manifest,
                          "notes": notes, "write_plan": plan.to_dict(),
                          "written": written}, indent=2))
    else:
        print(f"{'suite':<24}{'tier':<10}{'cost':<8}{'runs':>5}  evalsets")
        for suite in manifest["suites"]:
            print(f"{suite['name']:<24}{suite['tier']:<10}{suite['cost_tier']:<8}"
                  f"{suite['runs']:>5}  {len(suite['evalsets'])}")
        print()
        print('every suite is written with "frozen": false; set it to true on the golden '
              "suite so manage_cases.py refuses to edit its cases (EVS120)")
        print()
        print(plan.to_md())
        for note in notes:
            print(f"note: {note}")
        if args.dry_run:
            print()
            print("--dry-run: nothing was written")
        for path in written:
            print(f"wrote {path}")

    return EXIT_FINDINGS if refused and not args.dry_run else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", help="the manifest to run (omit with --init)")
    parser.add_argument("--init", action="store_true",
                        help="discover the agents under --root and write a manifest")
    parser.add_argument("--root", default=".", help="repository root to scan with --init")
    parser.add_argument("--out", help="where --init writes the manifest")
    parser.add_argument("--force", action="store_true", help="--init: replace an existing manifest")
    parser.add_argument("--tier", choices=("pr", "nightly"), help="only suites of this tier")
    parser.add_argument("--suite", action="append", default=[], help="only these suites, by name")
    parser.add_argument("--not-evaluated", choices=("fail", "warn"), default="fail")
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.init:
        return init(args)
    if not args.registry:
        print("error: pass --registry <manifest>, or --init to generate one",
              file=sys.stderr)
        return EXIT_USAGE

    registry_path = Path(args.registry)
    try:
        manifest = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read the registry: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if manifest.get("schema") != SCHEMA:
        print(f"error: expected schema {SCHEMA!r}, found {manifest.get('schema')!r}",
              file=sys.stderr)
        return EXIT_USAGE

    base = registry_path.parent
    suites = manifest.get("suites") or []
    if args.tier:
        suites = [s for s in suites if s.get("tier", "pr") == args.tier]
    if args.suite:
        suites = [s for s in suites if s.get("name") in args.suite]
    if not suites:
        print("error: no suites match", file=sys.stderr)
        return EXIT_USAGE

    if args.dry_run:
        for suite in suites:
            agent_dir = (base / suite["agent_dir"]).resolve()
            for evalset in resolve_paths(base, suite.get("evalsets") or []):
                command = ["adk", "eval", str(agent_dir), evalset]
                if suite.get("config"):
                    command.append(f"--config_file_path={(base / suite['config']).resolve()}")
                print(f"[{suite.get('name')}] " + " ".join(command))
        return EXIT_OK

    runner = resolve_runner(base)
    if not runner.available:
        print(f"error: {runner.note}", file=sys.stderr)
        return EXIT_USAGE

    rows: list[dict] = []
    worst = EXIT_OK

    for suite in suites:
        name = suite.get("name", "?")
        agent_dir = (base / suite["agent_dir"]).resolve()
        config = str((base / suite["config"]).resolve()) if suite.get("config") else None
        evalsets = resolve_paths(base, suite.get("evalsets") or [])
        runs = int(suite.get("runs", 1))

        metrics = []
        if config and Path(config).exists():
            loaded = load_config(config)
            metrics = loaded.metric_names()

        passed = failed = skipped = 0
        result_files: list[str] = []
        for _ in range(runs):
            for evalset in evalsets:
                outcome = run_adk_eval(
                    agent_dir, [evalset], runner=runner, config=config, timeout=args.timeout
                )
                passed += outcome.passed
                failed += outcome.failed
                skipped += outcome.not_evaluated
                result_files.extend(outcome.new_result_files)

        status = "pass"
        if failed:
            status, worst = "fail", EXIT_FINDINGS
        elif skipped:
            status = "not evaluated"
            if args.not_evaluated == "fail":
                worst = EXIT_FINDINGS

        rows.append({
            "suite": name, "tier": suite.get("tier", "pr"), "runs": runs,
            "evalsets": [Path(e).name for e in evalsets],
            "cost_tier": cost_tier(metrics) if metrics else "free",
            "passed": passed, "failed": failed, "not_evaluated": skipped,
            "status": status, "result_files": result_files,
        })

    payload = {
        "registry": str(registry_path), "runner": describe_runner(runner),
        "tier": args.tier, "suites": rows, "exit_code": worst,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Runner: {payload['runner']}")
        print()
        print(f"{'suite':<24}{'tier':<10}{'cost':<8}{'passed':>7}{'failed':>8}{'skipped':>9}  status")
        for row in rows:
            print(f"{row['suite']:<24}{row['tier']:<10}{row['cost_tier']:<8}"
                  f"{row['passed']:>7}{row['failed']:>8}{row['not_evaluated']:>9}  {row['status']}")
        print()
        print("clean" if worst == EXIT_OK else "one or more suites did not pass")

    return worst


if __name__ == "__main__":
    raise SystemExit(main())
