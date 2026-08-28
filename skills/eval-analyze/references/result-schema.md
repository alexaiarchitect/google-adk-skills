# The result file

## Where

```
<agent_dir>/.adk/eval_history/<app_name>_<eval_set_id>_<timestamp>.evalset_result.json
```

`adk create` gitignores `.adk/`. With `--eval_storage_uri gs://...` the same
content goes to Cloud Storage instead.

Both the app name and the eval set id can contain underscores, so the filename
alone cannot be split reliably — the authoritative `eval_set_id` is inside the
file. The timestamp may also be fractional
(`app_happy_1789993034.622729.evalset_result.json`).

## Shape

```json
{
  "eval_set_result_id": "app_expense_claims_1730000000",
  "eval_set_id": "expense_claims",
  "creation_timestamp": 1730000000.0,
  "eval_case_results": [
    {
      "eval_id": "happy_travel_within_policy",
      "final_eval_status": 2,
      "session_id": "___eval___session___...",
      "user_id": "evaluator",
      "overall_eval_metric_results": [
        {"metric_name": "tool_trajectory_avg_score", "threshold": 1.0,
         "score": 0.667, "eval_status": 2, "criterion": {"threshold": 1.0}},
        {"metric_name": "response_match_score", "threshold": 0.8,
         "score": 0.91, "eval_status": 1}
      ],
      "eval_metric_result_per_invocation": [
        {"actual_invocation": {...}, "expected_invocation": {...},
         "eval_metric_results": [...]}
      ]
    }
  ]
}
```

Written by field name, so keys are snake_case on disk. The same models are served
with camelCase aliases over the dev-server API, so a parser should accept both.

## Statuses

| Value | Name | Meaning |
|---|---|---|
| 1 | `PASSED` | every evaluated metric met its threshold |
| 2 | `FAILED` | at least one metric scored below its threshold |
| 3 | `NOT_EVALUATED` | the metric never produced a score |

Case-level roll-up: any `PASSED` makes the case passed, a `FAILED` short-circuits
to failed, and `NOT_EVALUATED` metrics are skipped — so a case whose every metric
was never evaluated stays `NOT_EVALUATED`.

`adk eval`'s printed summary counts anything that is not `PASSED` in its failed
column. The file does not.

## A failed case with no metrics at all

Verified against ADK 2.9.2: when inference itself fails — a bad API key, a model
that is not available, a tool that raised — the case is written with
`final_eval_status: 2` and an **empty** `overall_eval_metric_results`, and no
per-invocation entries.

That is not a score regression, and reading it as one sends you debugging an
agent whose behaviour was never observed. The fix is the environment. The
analyzer reports this case separately for exactly that reason.

## Per-invocation detail

`eval_metric_result_per_invocation` holds both sides of the comparison:
`actual_invocation` (what the agent did) and `expected_invocation` (what the case
asked for), each with `user_content`, `final_response` and
`intermediate_data.tool_uses`.

This is what makes a divergence point computable: the two trajectories side by
side, in order.

## Rubric scores

A rubric metric's `details.rubric_scores` is a list of
`{rubric_id, score, rationale}`. Score is 1.0 or 0.0 after the majority vote over
`num_samples`. The `rationale` is the judge's reason, and it is usually the
fastest way to tell "the agent did the wrong thing" from "the rubric asks the
wrong question".

## Deprecated fields

`eval_set_file` (use `eval_set_id`) and `eval_metric_results` (use
`overall_eval_metric_results`) still appear in older files.

---

Sources: `adk-python: evaluation/eval_result.py`, `evaluation/eval_metrics.py`,
`evaluation/eval_rubrics.py`, `evaluation/local_eval_set_results_manager.py`,
`evaluation/local_eval_service.py`, `cli/cli_tools_click.py`.
