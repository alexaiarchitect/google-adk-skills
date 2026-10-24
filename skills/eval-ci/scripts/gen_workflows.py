#!/usr/bin/env python3
"""Generate cost-tiered GitHub Actions workflows for ADK evals.

Two tiers, because one gate cannot be both cheap enough to run on every change
and thorough enough to trust for a release:

* **PR tier** — deterministic metrics only. No model calls, seconds to run, and
  it catches the failures that matter most: a skipped validation, a reordered
  pipeline, a wrong argument.
* **Nightly tier** — judge metrics, groundedness, safety, and repeated runs for
  pass@k. Costs model calls per invocation per sample, so it runs on a schedule
  with a reliability report in the job summary.

Both gate on the wrapper script rather than bare `adk eval`, which exits 0 on
every version through 2.12.0 and exits 1 on a failure from 2.13.0; the wrapper
reads the result files and gates (`--min-pass-rate`), so its exit code is the
one to use.

    gen_workflows.py <repo-root> --tier pr|nightly|both [--auth wif|api_key]
                     [--agent-dir app] [--python 3.12] [--dry-run]

Exit codes: 0 written (warnings are reported, not fatal), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills import ADK_TARGET, DEFAULT_MODEL  # noqa: E402
from adk_skills.eval_config import load_config  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.metrics import cost_tier, pr_tier_violations  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.templating import WritePlan, render  # noqa: E402

TEMPLATES = Path(__file__).resolve().parents[1] / "assets" / "templates"

VERTEX_ENV = """          GOOGLE_GENAI_USE_VERTEXAI: "TRUE"
          GOOGLE_CLOUD_PROJECT: ${{ vars.GOOGLE_CLOUD_PROJECT }}
          GOOGLE_CLOUD_LOCATION: ${{ vars.GOOGLE_CLOUD_LOCATION }}"""

API_KEY_ENV = """          GOOGLE_GENAI_USE_VERTEXAI: "FALSE"
          GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}"""


def relative(path: str | None, root: Path) -> str:
    if not path:
        return "."
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="repository root")
    parser.add_argument("--tier", choices=("pr", "nightly", "both"), default="both")
    parser.add_argument("--auth", choices=("wif", "api_key"), default="wif")
    parser.add_argument("--agent-dir", help="relative to the repo root (detected when omitted)")
    parser.add_argument("--pr-config", help="deterministic config (detected when omitted)")
    parser.add_argument("--nightly-config", help="judged config (defaults to eval_config.nightly.json)")
    parser.add_argument("--python", default="3.12")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--cron", default="0 3 * * *")
    parser.add_argument("--conformance", action="store_true", help="also a conformance replay job")
    parser.add_argument("--scripts-path", default=".github/adk-skills/skills",
                        help="where this plugin's skills live in CI")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return EXIT_USAGE

    layout = detect_layout(root)
    agent_dir = args.agent_dir or relative(layout.agent_dir, root)
    if agent_dir == "." and not layout.agent_dir:
        print(f"error: no ADK agent under {root}; pass --agent-dir", file=sys.stderr)
        return EXIT_USAGE

    pr_config = args.pr_config
    if not pr_config:
        for candidate in layout.configs:
            if Path(candidate).name in ("eval_config.json", "test_config.json"):
                pr_config = relative(candidate, root)
                break
    if not pr_config:
        print("error: no eval config found; create one with /adk:eval-criteria "
              "or pass --pr-config", file=sys.stderr)
        return EXIT_USAGE

    # The per-change tier must not contain a metric that pays a model.
    warnings: list[str] = []
    resolved_pr_config = root / pr_config
    if resolved_pr_config.exists():
        loaded = load_config(resolved_pr_config)
        offenders = pr_tier_violations(loaded.metric_names())
        if offenders:
            warnings.append(
                f"{pr_config} contains {', '.join(offenders)}, which costs model calls on "
                "every change. Generate a deterministic config for the PR tier with "
                "`/adk:eval-criteria --tier pr`."
            )

    nightly_config = args.nightly_config or str(
        Path(pr_config).with_name("eval_config.nightly.json")
    )
    if args.tier in ("nightly", "both") and not (root / nightly_config).exists():
        warnings.append(
            f"{nightly_config} does not exist yet; create it with "
            "`/adk:eval-criteria --tier nightly`."
        )

    auth_step = ""
    backend_env = API_KEY_ENV
    id_token = ""
    if args.auth == "wif":
        auth_step = (TEMPLATES / "wif_auth.yml.frag").read_text(encoding="utf-8")
        backend_env = VERTEX_ENV
        id_token = "  id-token: write\n"
    else:
        auth_step = (TEMPLATES / "api_key_auth.yml.frag").read_text(encoding="utf-8")

    mapping = {
        "AGENT_DIR": agent_dir,
        "PR_CONFIG": pr_config,
        "NIGHTLY_CONFIG": nightly_config,
        "PYTHON_VERSION": args.python,
        "ADK_VERSION": ADK_TARGET,
        "MODEL_NAME": DEFAULT_MODEL,
        "DEFAULT_BRANCH": args.branch,
        "CRON": args.cron,
        "AUTH_STEP": auth_step,
        "BACKEND_ENV": backend_env,
        "ID_TOKEN_PERMISSION": id_token,
        "SCRIPTS_PATH": args.scripts_path,
        "CONFORMANCE_DIR": relative(
            layout.conformance_dirs[0] if layout.conformance_dirs else "tests/conformance", root
        ),
    }

    plan = WritePlan()
    workflows = root / ".github" / "workflows"
    if args.tier in ("pr", "both"):
        plan.add(workflows / "adk-evals-pr.yml",
                 render((TEMPLATES / "workflows/adk-evals-pr.yml.tmpl").read_text(encoding="utf-8"), mapping),
                 force=args.force)
    if args.tier in ("nightly", "both"):
        plan.add(workflows / "adk-evals-nightly.yml",
                 render((TEMPLATES / "workflows/adk-evals-nightly.yml.tmpl").read_text(encoding="utf-8"), mapping),
                 force=args.force)
    if args.conformance:
        plan.add(workflows / "adk-conformance.yml",
                 render((TEMPLATES / "workflows/adk-conformance.yml.tmpl").read_text(encoding="utf-8"), mapping),
                 force=args.force)

    setup: list[str] = [
        f"the workflows call this plugin's scripts at {args.scripts_path}; vendor the plugin "
        "there, or change --scripts-path to wherever it lives in CI",
    ]
    if args.auth == "wif":
        setup.extend([
            "set the repository variables WIF_PROVIDER and WIF_SERVICE_ACCOUNT",
            "set GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION as repository variables",
            "grant the service account Vertex AI User on the project",
        ])
    else:
        setup.append("set the GOOGLE_API_KEY repository secret "
                     "(it cannot serve safety_v1 or the multi_turn_* metrics)")
    setup.append("optionally set the MODEL_NAME repository variable to override the default")

    written = [] if args.dry_run else plan.apply()

    if args.json:
        print(json.dumps({
            "agent_dir": agent_dir, "pr_config": pr_config, "nightly_config": nightly_config,
            "auth": args.auth, "write_plan": plan.to_dict(), "written": written,
            "warnings": warnings, "setup": setup,
            "pr_cost_tier": cost_tier(
                load_config(resolved_pr_config).metric_names()
                if resolved_pr_config.exists() else []
            ),
        }, indent=2))
        return EXIT_FINDINGS if warnings else EXIT_OK

    print(plan.to_md())
    for path in written:
        print(f"wrote {path}")
    if warnings:
        print()
        print("Before this gate means anything:")
        for warning in warnings:
            print(f"  - {warning}")
    print()
    print("Repository setup:")
    for step in setup:
        print(f"  - {step}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
