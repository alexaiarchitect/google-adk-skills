#!/usr/bin/env python3
"""Check that CI workflows actually gate on the evals they run.

Four mistakes, all of which produce a green pipeline that proves nothing:

* **bare `adk eval`** — through ADK 2.12.0 the command exits 0 whatever the
  result, so the step passes whether or not cases failed (an error). From
  2.13.0 it exits 1 on a failure, but it still cannot gate on a pass rate,
  regressions or budgets and does not read the result files (a warning). The
  version comes from `--adk-version`, else from the lowest `google-adk` pin in
  the project's `pyproject.toml` / `requirements*.txt` (from the workflow's
  directory up to the repo root) and in the workflow itself; unknown counts as
  older than 2.13.
* **judge metrics in the per-change tier** — slow and paid on every push, which
  ends with someone disabling the workflow.
* **missing `id-token: write`** — Workload Identity authentication cannot mint a
  token, so the job fails at auth rather than at eval.
* **a hardcoded key** — a credential in a workflow file is a credential in the
  repository history.

Also noted: a scheduled judged run with no drift alert (CI007, info): without
`compare_runs.py --max-mean-drop` against a baseline it only fails below an
absolute floor, so a slow slide stays green. `compare_runs.py` counts as a gate
(it exits 1 on a regression, a floor, a budget or a mean drop).

    lint_workflows.py <repo-root> [--adk-version X.Y] [--format json|md]

Exit codes: 0 clean, 1 findings, 2 nothing to check.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.env import pinned_adk_version, version_at_least  # noqa: E402
from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.metrics import pr_tier_violations  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport  # noqa: E402

ADK_EVAL = re.compile(r"^\s*(?:-\s*)?(?:run:\s*)?.*\badk\s+eval\b", re.M)
GATED = ("run_evals.py", "run_registry.py", "pytest", "run_pytest_eval.py", "compare_runs.py")
#: compare_runs.py flags that make it fail a job; `--max-regressions` (default 0)
#: is always on. A scheduled job without the drift flag gates only on floors.
COMPARE_GATE_FLAGS = ("--max-regressions", "--min-pass-rate", "--max-mean-prompt-tokens",
                      "--max-mean-seconds", "--max-mean-tool-calls", "--max-mean-drop")
DRIFT_FLAG = "--max-mean-drop"
SCHEDULED = re.compile(r"^\s*schedule:", re.M)
PER_CHANGE = ("pull_request", "push:")
SECRET_LOOKING = re.compile(
    r"""(?:GOOGLE_API_KEY|GEMINI_API_KEY|api[_-]?key)\s*[:=]\s*["']?(?!\$\{\{)[A-Za-z0-9_\-]{12,}""",
    re.I,
)
CONFIG_FLAG = re.compile(r"--config(?:_file_path)?[= ]+(\S+)")
#: The release from which bare `adk eval` exits 1 on a failing case (c2ff634).
EXIT_CODE_SINCE = "2.13"
PIN_FILES = ("pyproject.toml", "requirements*.txt")


def project_pin_texts(workflow: Path, root: Path) -> list[str]:
    """Contents of every pin file from the workflow's directory up to `root`."""
    texts: list[str] = []
    root = root.resolve()
    probe = workflow.resolve().parent
    while True:
        for pattern in PIN_FILES:
            for candidate in sorted(probe.glob(pattern)):
                try:
                    texts.append(candidate.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    continue
        if probe == root or probe == probe.parent:
            break
        probe = probe.parent
    return texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--adk-version",
                        help="the ADK version CI installs (default: the lowest google-adk pin "
                             "in the project files and the workflow; unknown counts as < 2.13)")
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
    versions: dict[str, str] = {}

    for workflow in sorted(directory.glob("*.y*ml")):
        try:
            text = workflow.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "adk" not in text.lower() and "eval" not in text.lower():
            continue
        checked.append(workflow.name)
        name = workflow.name

        version = args.adk_version or pinned_adk_version(*project_pin_texts(workflow, root), text)
        if version:
            versions[name] = version
        if ADK_EVAL.search(text) and not any(marker in text for marker in GATED):
            if version_at_least(version, EXIT_CODE_SINCE):
                report.add(
                    "CI001", "warn", "ci",
                    "runs `adk eval` directly: from ADK 2.13.0 it exits 1 on a failure, but it "
                    "cannot gate on a pass rate, regressions or budgets and does not read the "
                    "result files; prefer run_evals.py",
                    path=str(workflow),
                    fix="route it through run_evals.py (--min-pass-rate) or run_registry.py, "
                        "or use pytest with AgentEvaluator",
                    source="adk-python 2.13.0: cli/cli_tools_click.py (exit 1 on a failed "
                           f"case, c2ff634); ADK version {version}",
                )
            else:
                report.add(
                    "CI001", "error", "ci",
                    "runs `adk eval` directly, which exits 0 whatever the result on every ADK "
                    "version through 2.12.0" + (f" (pinned: {version})" if version else
                                                 " (no google-adk pin found)")
                    + ": this step passes even when cases fail",
                    path=str(workflow),
                    fix="route it through run_evals.py or run_registry.py, or use pytest with "
                        "AgentEvaluator, which raises; pinning google-adk>=2.13 gives a failing "
                        "exit code but still no pass-rate or regression gate",
                    source="adk-python: cli/cli_tools_click.py (no exit code through 2.12.0; "
                           "exit 1 on a failure from 2.13.0)",
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

        if (SCHEDULED.search(text) and any(m in text for m in ("run_evals.py", "run_registry.py"))
                and not ("compare_runs.py" in text and DRIFT_FLAG in text)):
            report.add(
                "CI007", "info", "ci",
                "a scheduled eval run with no drift alert: it fails only below an absolute "
                "floor, so a metric that slides a little every night stays green",
                path=str(workflow),
                fix="add a step running compare_runs.py <baseline> <agent>/.adk/eval_history "
                    "--latest --max-mean-drop 0.05 against committed baseline result files "
                    "(gen_workflows.py --tier nightly writes one)",
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

    report.discovered = {"workflows_checked": checked, "adk_versions": versions}
    print(report.render(args.format))
    return EXIT_FINDINGS if report.has_errors() else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
