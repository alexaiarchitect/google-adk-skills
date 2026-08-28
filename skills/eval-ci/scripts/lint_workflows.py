#!/usr/bin/env python3
"""Check that CI workflows actually gate on the evals they run.

Four mistakes, all of which produce a green pipeline that proves nothing:

* **bare `adk eval`** — the command always exits 0, so the step passes whether or
  not cases failed. This is the one worth checking for above all others.
* **judge metrics in the per-change tier** — slow and paid on every push, which
  ends with someone disabling the workflow.
* **missing `id-token: write`** — Workload Identity authentication cannot mint a
  token, so the job fails at auth rather than at eval.
* **a hardcoded key** — a credential in a workflow file is a credential in the
  repository history.

    lint_workflows.py <repo-root> [--format json|md]

Exit codes: 0 clean, 1 findings, 2 nothing to check.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.metrics import pr_tier_violations  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402

ADK_EVAL = re.compile(r"^\s*(?:-\s*)?(?:run:\s*)?.*\badk\s+eval\b", re.M)
GATED = ("run_evals.py", "run_registry.py", "pytest", "run_pytest_eval.py")
PER_CHANGE = ("pull_request", "push:")
SECRET_LOOKING = re.compile(
    r"""(?:GOOGLE_API_KEY|GEMINI_API_KEY|api[_-]?key)\s*[:=]\s*["']?(?!\$\{\{)[A-Za-z0-9_\-]{12,}""",
    re.I,
)
CONFIG_FLAG = re.compile(r"--config(?:_file_path)?[= ]+(\S+)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--json", action="store_const", const="json", dest="format")
    args = parser.parse_args(argv)

    root = Path(args.path)
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        print(f"error: no workflows under {directory}", file=sys.stderr)
        return EXIT_USAGE

    report = GapReport(subject=str(directory), skill="eval-ci")
    checked: list[str] = []

    for workflow in sorted(directory.glob("*.y*ml")):
        try:
            text = workflow.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "adk" not in text.lower() and "eval" not in text.lower():
            continue
        checked.append(workflow.name)
        name = workflow.name

        if ADK_EVAL.search(text) and not any(marker in text for marker in GATED):
            report.add(
                "CI001", "error", "ci",
                "runs `adk eval` directly, which always exits 0: this step passes even when "
                "cases fail",
                path=str(workflow),
                fix="route it through run_evals.py or run_registry.py, or use pytest with "
                    "AgentEvaluator, which raises",
                source="adk-python: cli/cli_tools_click.py (the eval command sets no exit code)",
            )

        per_change = any(trigger in text for trigger in PER_CHANGE)
        for match in CONFIG_FLAG.finditer(text):
            config_path = root / match.group(1).strip("'\"")
            if not config_path.exists():
                continue
            loaded = load_config(config_path)
            if not loaded.ok:
                continue
            offenders = pr_tier_violations(loaded.metric_names())
            if offenders and per_change:
                report.add(
                    "CI002", "warn", "ci",
                    f"the per-change tier uses {', '.join(offenders)}, which cost model calls "
                    "on every push",
                    path=str(workflow),
                    fix="keep judge metrics in a scheduled workflow and gate changes on the "
                        "deterministic config",
                )

        if "google-github-actions/auth" in text and "id-token: write" not in text:
            report.add(
                "CI003", "error", "ci",
                "authenticates with Workload Identity but does not request `id-token: write`",
                path=str(workflow),
                fix="add `permissions:` with `id-token: write`, or the job fails at auth",
            )

        if SECRET_LOOKING.search(text):
            report.add(
                "CI004", "error", "ci",
                "what looks like a hardcoded credential",
                path=str(workflow),
                fix="use ${{ secrets.NAME }}; a key in a workflow file is in the repository "
                    "history for good",
            )

        if "upload-artifact" not in text and any(marker in text for marker in GATED):
            report.add(
                "CI005", "info", "ci",
                "eval results are not uploaded, so a failure cannot be examined after the fact",
                path=str(workflow),
                fix="upload `<agent>/.adk/eval_history/` with `if: always()`",
            )

        if "--not-evaluated" not in text and any(m in text for m in ("run_evals.py", "run_registry.py")):
            report.add(
                "CI006", "info", "ci",
                "no explicit `--not-evaluated` policy; the default fails on a case that was "
                "never scored, which is usually right in CI",
                path=str(workflow),
            )

    if not checked:
        print(f"error: no eval-related workflows under {directory}", file=sys.stderr)
        return EXIT_USAGE

    report.discovered = {"workflows_checked": checked}
    print(report.render(args.format))
    return EXIT_FINDINGS if report.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
