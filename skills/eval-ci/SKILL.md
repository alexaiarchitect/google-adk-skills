---
name: eval-ci
description: >-
  Generates cost-tiered GitHub Actions workflows for Google ADK evaluations and
  assesses where a project's evaluation practice stands. Produces a PR-tier
  workflow that runs only deterministic metrics and gates on a real exit code,
  and a nightly tier for judge metrics, groundedness, safety and repeated runs
  with a reliability report; wires Workload Identity to Vertex AI or an API-key
  secret; uploads eval history as an artifact; and lints existing workflows for
  the mistake that matters most, running bare `adk eval`, which exits 0
  whatever happened through ADK 2.12.0 and, from 2.13.0, exits 1 on a failure
  but still cannot gate on a pass rate, regressions or budgets.
  Also reports an evaluation maturity level from L0 to L4 with the next concrete
  step. Use when asked to "add evals to CI", "gate merges on evals", "set up a
  nightly eval run", "why does my eval workflow always pass", or "how mature is
  our evaluation". Don't use to run evals locally (use `/adk:eval-run`) or to
  choose metrics (use `/adk:eval-criteria`).
argument-hint: "[repo-root] [--tier pr|nightly|both]"
allowed-tools: Read, Grep, Glob, Write, Edit, AskUserQuestion, Skill, Bash(python3 *)
---

# Gate changes on ADK evals

One fact shapes every workflow here: **`adk eval` exits 0 whatever happened on
every release through 2.12.0.** A CI step that calls it directly on such a pin is
green whether the cases passed or not, and that is the most common way an eval
suite ends up proving nothing. From 2.13.0 it exits 1 when a case fails or none
ran, yet it still cannot gate on a pass rate, on regressions against the default
branch or on a budget, so the workflows here call `run_evals.py` on every
version. `lint_workflows.py` reports bare `adk eval` as `CI001`: an error when the
pin is below 2.13 or unknown, a warning from 2.13 (`--adk-version` overrides the
pin it detects in `pyproject.toml`, `requirements*.txt` or the workflow's
`--with`).

The second fact is cost. A judge metric costs model calls per invocation per
sample, so a suite that is thorough enough to trust for a release is too
expensive to run on every push. Hence two tiers.

`${CLAUDE_SKILL_DIR}` is set by the skill runtime to this skill's own
directory. Use it verbatim in every command below; a sibling skill's scripts
are at `${CLAUDE_SKILL_DIR}/../<skill>/scripts/`.

## Flow

1. **Discover.** What exists, and where the project stands:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/assess_maturity.py <repo-root>
   python3 ${CLAUDE_SKILL_DIR}/scripts/lint_workflows.py <repo-root>     # if workflows exist
   ```

2. **Report.** The maturity level with its evidence, any existing workflow that
   does not actually gate, and what the configs contain.

3. **Ask where this plugin's scripts will live in the CI checkout.** The
   generated workflow gates by calling `run_evals.py` **by path**, so this is
   not a detail: a wrong path fails the job at the wrong step, and looks like a
   broken gate rather than a missing file. Three usual answers — vendored under
   `.github/adk-skills/` (the default), a git submodule, or installed by a step
   in the job — and `--scripts-path P` writes whichever they pick. See
   [workflows.md](references/workflows.md), "Where the scripts live in CI".

4. **Options.** Usually: add the PR tier only (a good first step), add both tiers,
   add conformance replay, or fix an existing workflow that passes vacuously.

5. **Confirm**, generate with `--dry-run` first, write, then lint what you wrote.

## The two tiers

| | PR tier | Nightly tier |
|---|---|---|
| Triggers | pull request, push to the default branch | schedule, plus manual |
| Metrics | deterministic only | judge, groundedness, safety |
| Runs | 1 | 5, or more on request |

Both gate on the wrapper's exit code; the nightly tier adds a reliability
report. Timings, what belongs in each, the release tier and the
`NOT_EVALUATED` rule are in
[tiered-pipeline.md](references/tiered-pipeline.md).

The split is about cost and speed, not importance. A slow, paid gate on every push
gets disabled, and a disabled gate is worse than a cheap one.

## What the generated workflows do

- install a pinned `google-adk[eval]`;
- authenticate with Workload Identity (or pass an API-key secret);
- run `run_evals.py`, not `adk eval`, so the exit code is real and can carry a
  pass-rate, regression and budget gate;
- fail on a case that was never scored, because a suite that silently stops
  scoring is worse than one that fails;
- upload `.adk/eval_history/` with `if: always()`, so a failure can be examined;
- on the nightly tier, write a pass@k table and any failure explanation into the
  job summary, and compare with the baseline committed under `eval_baseline/` so a drop in a scored
  metric's mean fails the run and raises the alert (`compare_runs.py
  --max-mean-drop 0.05`).

## Repository setup

With Workload Identity (recommended):

| Kind | Name |
|---|---|
| variable | `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT` |
| variable | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` |
| variable | `MODEL_NAME` (optional; overrides the default) |
| permission | `id-token: write` on the job, or auth cannot mint a token |
| IAM | Vertex AI User on the project |

With an API key: the `GOOGLE_API_KEY` secret. It serves every metric, the
Vertex-backed ones included — the evaluation facade reads the key before any
project. What you give up is Workload Identity: a long-lived secret instead of a
short-lived minted token.

## The maturity ladder

| Level | Reached when |
|---|---|
| L0 Vibe checks | nothing replayable states what the agent should do |
| L1 Golden traces | cases exist and replay deterministically |
| L2 Automated gates | they run without anyone remembering, and failure blocks |
| L3 Statistical reliability | repeated runs give a pass rate |
| L4 Continuous observability | production failures become eval cases |

Each level needs the one below it, and each is checkable rather than a matter of
opinion. Most projects sit between L0 and L1 while believing they are higher,
because someone ran the agent and it looked right.

## Decision rules

- **Never gate on bare `adk eval`.** The linter treats it as an error, and it is
  the check most worth running on a workflow you inherited.
- **Keep judge metrics out of the per-change tier.** If they are there, the tier
  will eventually be turned off.
- **Fail on NOT_EVALUATED in CI.** A never-scored case means the gate has stopped
  working, which is more urgent than a failing case.
- **Always upload the eval history.** A red pipeline with no artifact makes
  everyone re-run locally to learn anything.
- **Reliability belongs in the nightly tier.** Repeated runs are too slow to gate
  a push and too valuable to skip entirely.
- **Pin the ADK version.** An eval suite that changes behaviour because a
  dependency floated is a suite nobody trusts.
- **Cheap gates first.** Lints and validators (`lint_agent.py`,
  `validate_evalset.py`, `validate_config.py`, `lint_workflows.py`) call no model;
  run them before anything judged, and trigger the expensive suites on purpose
  (`workflow_dispatch`, a PR comment or label, `paths:` on the agent's files).
- **Evidence must be fresh.** An eval-affecting PR links a report from a run on
  its latest commit; one from an earlier commit does not count.
- **Alert on a drop, not on a low score.** The nightly run compares with the
  baseline and fails on the drop; a schedule without that exit code is a log,
  not a gate.
- **Merge on two rules, not one.** A global pass threshold
  (`run_evals.py --min-pass-rate 0.95`) and zero regressions against the default
  branch (`compare_runs.py --min-pass-rate 0.95 --max-regressions 0`), plus
  token, latency and tool-call budgets where cost matters. Freeze the golden
  suite (`"frozen": true`) so tuning cannot delete its way to green.

## References

| Question | File |
|---|---|
| Designing the tiers, and what belongs in each | [tiered-pipeline.md](references/tiered-pipeline.md) |
| The workflows, annotated, plus auth and secrets | [workflows.md](references/workflows.md) |
| The maturity ladder and how to climb it | [maturity.md](references/maturity.md) |
| Merge rules, frozen baselines, cost budgets, Day 0/1/2 | [merge-gates.md](references/merge-gates.md) |
| Evaluating a Cloud Run revision before it takes traffic (pattern only, no template) | [shadow-rollout.md](references/shadow-rollout.md) |

Templates: `assets/templates/workflows/`.

## Scripts

| Command | Does | Exit codes |
|---|---|---|
| `scripts/gen_workflows.py <repo-root> [--tier pr\|nightly\|both] [--auth wif\|api_key] [--agent-dir D] [--scripts-path P] [--python V] [--cron EXPR] [--baseline-dir DIR] [--conformance] [--dry-run]` | writes the workflows; the nightly one compares with result files committed under `--baseline-dir` (default `eval_baseline`, step skipped until they exist) via `compare_runs.py --max-mean-drop 0.05` | 0, 1 written with warnings, 2 usage |
| `scripts/assess_maturity.py <repo-root> [--json]` | the level, evidence and next step | 0 |
| `scripts/lint_workflows.py <repo-root> [--adk-version V] [--json]` | checks existing workflows gate; `CI001` (bare `adk eval`) is version-aware, the pin auto-detected unless `--adk-version` is given; `CI007` (info) a scheduled eval run with no `--max-mean-drop` drift alert | 0 clean, 1 findings, 2 none found |

## Completion checklist

- [ ] The PR tier contains only deterministic metrics.
- [ ] Nothing gates on bare `adk eval`.
- [ ] Authentication is wired, with `id-token: write` where Workload Identity is used.
- [ ] No credential is hardcoded in a workflow.
- [ ] Eval history is uploaded on failure as well as success.
- [ ] The nightly tier has a run count and a reliability report.
- [ ] `--scripts-path` matches where the plugin actually lives in the CI checkout.
- [ ] The linter exits 0 on what was generated.
