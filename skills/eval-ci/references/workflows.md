# The workflows

## PR tier

```yaml
name: ADK evals (PR)

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: adk-evals-pr-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read
  id-token: write          # required for Workload Identity

jobs:
  deterministic:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install "google-adk[eval]==2.9.*"
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ vars.WIF_PROVIDER }}
          service_account: ${{ vars.WIF_SERVICE_ACCOUNT }}
      - env:
          MODEL_NAME: ${{ vars.MODEL_NAME || 'gemini-flash-latest' }}
          GOOGLE_GENAI_USE_VERTEXAI: "TRUE"
          GOOGLE_CLOUD_PROJECT: ${{ vars.GOOGLE_CLOUD_PROJECT }}
          GOOGLE_CLOUD_LOCATION: ${{ vars.GOOGLE_CLOUD_LOCATION }}
        run: |
          python3 .github/adk-skills/skills/eval-run/scripts/run_evals.py app \
            --config tests/eval/eval_config.json \
            --not-evaluated fail
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: eval-history-pr
          path: app/.adk/eval_history/
          if-no-files-found: ignore
```

The parts that matter, and why:

| Line | Why |
|---|---|
| `run_evals.py`, not `adk eval` | the command always exits 0 |
| `--not-evaluated fail` | a never-scored case means the gate broke |
| `id-token: write` | without it, Workload Identity auth fails before any eval runs |
| `upload-artifact` with `if: always()` | a red pipeline with no artifact teaches nobody anything |
| `concurrency` with `cancel-in-progress` | superseded runs stop costing time |
| a pinned ADK version | otherwise behaviour changes without a commit |

## Nightly tier

Same shape, plus:

```yaml
on:
  schedule:
    - cron: "0 3 * * *"
  workflow_dispatch:
    inputs:
      runs:
        description: "Runs per eval set (5 for a check, 20+ before a release)"
        default: "5"

# ...
      - run: |
          python3 .../run_evals.py app \
            --config tests/eval/eval_config.nightly.json \
            --runs "${{ github.event.inputs.runs || '5' }}" \
            --not-evaluated fail

      - if: always()
        run: |
          python3 .../pass_at_k.py --dir app/.adk/eval_history >> "$GITHUB_STEP_SUMMARY" || true

      - if: failure()
        run: |
          python3 .../summarize_results.py app --latest >> "$GITHUB_STEP_SUMMARY" || true
```

The reliability table and the failure explanation go into the **job summary**,
where they are visible without downloading anything. `|| true` on those steps
keeps a reporting failure from masking the eval result.

## Authentication

**Workload Identity Federation** is the right default: no key material, and the
token is scoped to the run.

| Kind | Name | Value |
|---|---|---|
| variable | `WIF_PROVIDER` | `projects/N/locations/global/workloadIdentityPools/POOL/providers/PROVIDER` |
| variable | `WIF_SERVICE_ACCOUNT` | the service account email |
| variable | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | your project and region |

The service account needs **Vertex AI User**. The job needs `id-token: write`.

**An API key** is simpler: put it in the `GOOGLE_API_KEY` secret. It serves
judge metrics *and* the Vertex-backed ones — `safety_v1`,
`response_evaluation_score`, `multi_turn_*` — because the evaluation facade reads
the key before any project. What it gives up is Workload Identity itself: a
long-lived secret in the repository rather than a short-lived minted token, plus
the Vertex-hosted session, memory and artifact services.

Never inline a key. A credential in a workflow file is in the repository history
permanently; the linter treats it as an error.

## Where the scripts live in CI

The workflows call this plugin's scripts by path, defaulting to
`.github/adk-skills/skills`. Either vendor the plugin there (a submodule or a
copied directory), or pass `--scripts-path` pointing at wherever it lives.

If you would rather not vendor anything, use the project's own pytest suite
instead — `pytest tests/eval` gates correctly on its own, because
`AgentEvaluator` raises.

## Conformance

An optional third workflow replays recorded transcripts to catch drift. Worth
having on an agent that is stable, noisy on one under active development, since
every intentional change needs the recordings regenerated.

---

Sources: this plugin's workflow templates;
`google-github-actions/auth` documentation; `adk-python: cli/cli_tools_click.py`.
