# What the Vertex-backed metrics need

## Which ones

`safety_v1`, `response_evaluation_score`, `multi_turn_task_success_v1`,
`multi_turn_trajectory_quality_v1`, `multi_turn_tool_use_quality_v1`.

They are scored by the **Vertex AI Gen AI Evaluation Service**, not by a judge
model you configure. Consequences:

- They need **either** a `GOOGLE_API_KEY` **or** `GOOGLE_CLOUD_PROJECT` *and*
  `GOOGLE_CLOUD_LOCATION` with Application Default Credentials. The facade reads
  the API key **first**; only if it is absent does it fall back to the project
  pair, and it raises if exactly one of that pair is set. This holds
  **regardless of `GOOGLE_GENAI_USE_VERTEXAI`**, which controls how the *agent*
  reaches its model, not how evaluation is scored.
- `judge_model_options` does nothing on them: the service picks its own model.
  That is what the `vertex` cost tier marks — a separate service on a separate
  bill, not a stricter credential.
- They fail differently from a judge metric: no usable credential at all shows up
  as `NOT_EVALUATED`, not as a low score.
- **`safety_v1` now asks the service for a pinned spec version.** 2.10 requests
  `PrebuiltMetric.SAFETY(version="v1")` where 2.9.2 requested `SAFETY` and took
  whatever the service considered current. The version string is internal — it
  appears in no config and no result file, and the score still means 1.0 for no
  policy violation and 0.0 for one — but it is the reason a `safety_v1` score can
  differ between the two releases without anything in your suite changing.

## Local setup

With a project (the fuller option — it is also what session, memory and artifact
services need):

```dotenv
# <agent>/.env
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=my-project
GOOGLE_CLOUD_LOCATION=us-central1
MODEL_NAME=gemini-flash-latest
```

```bash
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com --project my-project
```

With an API key alone, which is enough for every metric on this page:

```dotenv
# <agent>/.env
GOOGLE_API_KEY=...
MODEL_NAME=gemini-flash-latest
```

Set one or the other deliberately. With both present the key wins for evaluation,
which surprises people who set a project and then wonder why their quota and
billing landed somewhere else.

Check before running a suite:

```bash
python3 ${CLAUDE_SKILL_DIR}/../adk/scripts/check_env.py <agent-dir> --config <config>
```

It reports whether the configured criteria can actually be served, rather than
letting you discover it as a page of `NOT_EVALUATED`.

## CI

Use Workload Identity Federation rather than a key file:

```yaml
permissions:
  contents: read
  id-token: write
steps:
  - uses: google-github-actions/auth@v2
    with:
      workload_identity_provider: ${{ vars.WIF_PROVIDER }}
      service_account: ${{ vars.WIF_SERVICE_ACCOUNT }}
```

The service account needs Vertex AI User on the project. `/adk:eval-ci`
generates this.

## Cost and placement

Every Vertex metric is a billed API call per invocation. Keep them out of the
per-change tier: run the deterministic config on pull requests, and a nightly
config that adds safety and the multi-turn metrics.

## When you have no Vertex project

An API key covers every metric here, so the gap is narrower than it looks. What a
project buys on top: Vertex-hosted session, memory and artifact services,
Workload Identity in CI instead of a long-lived secret, and per-project quota and
billing. If you have neither credential, this is what is still available:

| Want | Without Vertex |
|---|---|
| trajectory correctness | `tool_trajectory_avg_score` (free) |
| response meaning | `final_response_match_v2` (judge) |
| response quality | `rubric_based_final_response_quality_v1` (judge) |
| restraint | `rubric_based_tool_use_quality_v1` (judge) |
| groundedness | `hallucinations_v1` (judge) |
| harmful content | no direct equivalent; use a safety callback or plugin in the agent, and rubrics that assert refusal |

With a Gemini API key you have all of the above **and** the five Vertex-served
metrics. With no credential at all you have only the free row.

## GCS eval storage

Unrelated to metrics, same credentials: `adk eval --eval_storage_uri
gs://bucket/prefix` keeps eval sets and results in Cloud Storage instead of
`.adk/eval_history/`, which is how CI and humans end up reading the same history.

---

Sources: `adk-python: evaluation/vertex_ai_eval_facade.py` (`__init__` reads
`GOOGLE_API_KEY` before the project pair),
`evaluation/safety_evaluator.py` (`_VERTEX_SAFETY_SPEC_VERSION = "v1"`, new in
2.10.0), `evaluation/response_evaluator.py`,
`cli/utils/evals.py`; `adk-docs: docs/evaluate/criteria.md`,
`docs/evaluate/index.md`.
