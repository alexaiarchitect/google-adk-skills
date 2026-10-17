# Environment: backends, credentials, and finding a Python that can run ADK

## Two backends

ADK loads `.env` from beside the agent package, so this is per-agent
configuration, not global.

### Vertex AI (this plugin's default)

```dotenv
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=my-project
GOOGLE_CLOUD_LOCATION=us-central1
MODEL_NAME=gemini-flash-latest
```

Authentication is Application Default Credentials: `gcloud auth
application-default login` locally, Workload Identity Federation in CI. Required
for the metrics that call the Vertex AI Gen AI Evaluation Service.

### Gemini API (AI Studio)

```dotenv
GOOGLE_API_KEY=...
MODEL_NAME=gemini-flash-latest
```

Enough for judge-model metrics, and enough for the Vertex-served ones too:
`safety_v1`, `response_evaluation_score` and the `multi_turn_*` metrics go
through the Vertex AI evaluation service, whose facade reads `GOOGLE_API_KEY`
first and only falls back to a project and location. What you give up without a
project is not those metrics but the Vertex-hosted session, memory and artifact
services, Workload Identity in CI, and per-project quota and billing.

## Which metrics need what

| Metric group | Needs |
|---|---|
| `tool_trajectory_avg_score`, `response_match_score` | nothing; no model calls |
| `final_response_match_v2`, `rubric_based_*`, `hallucinations_v1` | any backend that serves the judge model |
| `safety_v1`, `response_evaluation_score`, `multi_turn_*` | the Vertex evaluation service: `GOOGLE_API_KEY`, **or** project + location with ADC (key wins when both are set) |

## Model policy

- Read the id from the environment: `MODEL_NAME`, defaulting to
  `gemini-flash-latest`. A hardcoded dated id pins the agent to a model that
  will be retired.
- `gemini-2.0-flash` and `gemini-2.5-flash` are treated as **retiring** by this
  plugin, which warns on them. The source is `adk-recipes: AGENTS.md`, not ADK —
  ADK still ships `gemini-2.5-flash` as the judge and simulation default. Replace
  them when you touch the file; do not treat one as a broken agent.
- Preview ids (`...-preview`, `...-exp`) disappear without notice; fine for an
  experiment, wrong for anything committed.
- **Always set `judge_model` explicitly in an eval config.** Left unset, the
  judge defaults to `gemini-2.5-flash` — the retiring id above — and to
  `num_samples: 5`, five judge calls per invocation.

## Optional dependency

Evaluation lives behind an extra:

```bash
pip install "google-adk[eval]==2.12.*"
```

`google.adk.evaluation` swallows the `ImportError` when the extra is missing, so
the package imports fine and `AgentEvaluator` simply is not exported. The CLI
reports it properly; a script that imports the module may not.

## Picking an interpreter

Nothing in this plugin assumes the Python running it can import `google.adk`.
Scripts that need ADK resolve a runner in this order:

1. **`$ADK_PYTHON`** — an explicit interpreter path. Set this when you know best.
2. **A virtualenv at or above the agent** — `.venv/bin/python`, `venv/bin/python`.
3. **The interpreter that owns an `adk` on `PATH`**.
4. **`uv run --with "google-adk[eval]==2.12.*" python`** — installs into a
   throwaway environment. Needs `uv` (`brew install uv`).

If none resolve, do not guess: report it and give the fix.

```bash
# verify by hand
python3 -c "import google.adk; print(google.adk.version.__version__)"

# a project venv, when uv is unavailable
python3 -m venv .venv && .venv/bin/pip install "google-adk[eval]==2.12.*"
ADK_PYTHON=.venv/bin/python python3 <script>
```

A candidate found by path alone is reported as **unverified** until ADK has
actually been imported with it. Treat unverified as unknown.

## Eval result storage

Local by default: `<agent_dir>/.adk/eval_history/*.evalset_result.json`, which
`adk create` gitignores along with `.env`. `adk eval --eval_storage_uri
gs://bucket/prefix` moves eval sets and results to Cloud Storage instead — useful
when CI and humans need to read the same history, and it needs the same ADC as
any other GCS access.

---

Sources: `adk-python: cli/utils/envs.py`, `evaluation/vertex_ai_eval_facade.py`
(credential order), `evaluation/constants.py`,
`evaluation/__init__.py`, `evaluation/eval_metrics.py` (`JudgeModelOptions`),
`cli/cli_create.py`, `cli/utils/evals.py`, `pyproject.toml` (`[eval]` extra);
`adk-docs: docs/evaluate/criteria.md`, `docs/get-started/*`;
`adk-recipes: AGENTS.md` (model deprecations), recipe `.env.example` files.
