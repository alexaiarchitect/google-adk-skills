#!/usr/bin/env python3
"""Convert a legacy ADK eval file to the current EvalSet schema.

The legacy shape is a top-level JSON list::

    [{"name": "...", "data": [{"query": "...", "expected_tool_use": [...],
      "reference": "..."}], "initial_session": {...}}]

ADK still reads it, converting on the fly and assigning a **random**
`eval_set_id` each time. That is the reason to migrate: a stable id means stable
result filenames, a readable diff, and a file you can point `--config_file_path`
at with confidence.

Conversion preserves every query, expected tool call and reference answer. Ids
come from the legacy `name` rather than a UUID, so the diff stays reviewable.

    migrate_legacy.py old.test.json [--out new.evalset.json] [--eval-set-id id]
                      [--app-name app] [--dry-run] [--force]

Exit codes: 0 done, 1 the result did not validate, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.evalset import (  # noqa: E402
    LoadedEvalSet, load_evalset, migrate_legacy, sanitize_eval_set_id, stats, validate_evalset,
)
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402
from adk_skills.templating import WritePlan, dumps_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="the legacy file")
    parser.add_argument("--out", help="output path (default: <stem>.evalset.json beside the input)")
    parser.add_argument("--eval-set-id", help="default: the input filename stem")
    parser.add_argument("--app-name", help="session_input.app_name when the legacy file has none")
    parser.add_argument("--user-id", default="evaluator")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.path)
    if not source.exists():
        print(f"error: {source} does not exist", file=sys.stderr)
        return EXIT_USAGE

    loaded = load_evalset(source)
    if loaded.fmt == "invalid":
        print(f"error: {loaded.error}", file=sys.stderr)
        return EXIT_USAGE
    if not loaded.is_legacy:
        print(f"error: {source} is already in the EvalSet schema; nothing to migrate",
              file=sys.stderr)
        return EXIT_USAGE

    stem = source.name.replace(".test.json", "").replace(".json", "")
    eval_set_id = args.eval_set_id or sanitize_eval_set_id(stem)
    app_name = args.app_name
    if not app_name:
        layout = detect_layout(source.parent)
        app_name = layout.expected_app_name() or eval_set_id

    # `load_evalset` already normalized the document; `raw` is the legacy list.
    migrated = migrate_legacy(
        loaded.raw, eval_set_id=eval_set_id, app_name=app_name, user_id=args.user_id
    )

    report = GapReport(subject=str(source), skill="eval-create")
    validate_evalset(
        LoadedEvalSet(str(source), migrated, migrated, "evalset"),
        report=report, expected_app_name=app_name, reference_metrics=False,
    )

    out = Path(args.out) if args.out else source.with_name(f"{stem}.evalset.json")
    rendered = dumps_json(migrated)
    plan = WritePlan()
    plan.add(out, rendered, force=args.force)

    summary = stats(migrated)
    summary["legacy_shape"] = loaded.legacy_shape
    if loaded.is_flat_legacy:
        report.add("EVS051", "warn", "evalset",
                   "flat-turn legacy file: `adk eval` cannot read it (KeyError 'data'); "
                   "only AgentEvaluator.evaluate() could, so migrating is the fix",
                   path=str(source), source="adk-python: evaluation/local_eval_sets_manager.py")
    written = [] if args.dry_run else plan.apply()

    if args.json:
        print(json.dumps({
            "source": str(source),
            "eval_set_id": eval_set_id,
            "app_name": app_name,
            "stats": summary,
            "write_plan": plan.to_dict(),
            "written": written,
            "validation": report.to_dict(),
        }, indent=2))
    else:
        print(f"{source.name}: {summary['cases']} case(s), {summary['turns']} turn(s) "
              f"-> eval_set_id {eval_set_id!r}, app_name {app_name!r}")
        print()
        print(plan.to_md())
        if report.findings:
            print()
            print(report.to_md())

    if not args.json:
        for path in written:
            print(f"wrote {path}")
        if not written and not args.dry_run:
            print("nothing written (the file exists; pass --force to replace it)")
        elif written and source.name.endswith(".test.json"):
            print("note: keep the .test.json suffix if you rely on "
                  "AgentEvaluator.evaluate(<dir>), which only scans that pattern")

    return EXIT_FINDINGS if report.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
