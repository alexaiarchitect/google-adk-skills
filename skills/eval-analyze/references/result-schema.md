# The result file

## Where

```
<agent_dir>/.adk/eval_history/<app_name>_<eval_set_id>_<timestamp>.evalset_result.json
```

`adk create` gitignores `.adk/`. With `--eval_storage_uri gs://...` the same
content goes to Cloud Storage instead. Unchanged on 2.10, 2.11 and 2.12: the history still lands
inside the **agent module** directory, not at the project root, so a recipes
layout writes to `<project>/app/.adk/eval_history/`.

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
| 4 | `INFORMATIONAL` | 2.10+: the metric reports a value and never passes or fails |

Case-level roll-up: any `PASSED` makes the case passed, a `FAILED` short-circuits
to failed, and `NOT_EVALUATED` metrics are skipped — so a case whose every metric
was never evaluated stays `NOT_EVALUATED`.

`adk eval`'s printed summary counts anything that is not `PASSED` in its failed
column. The file does not.

`INFORMATIONAL` (4) is the status the four efficiency metrics write, overall and
per invocation. It is not a verdict: this plugin's analyzer lists those entries in
a separate **Efficiency** table (lower is better) and never counts them in the
pass, fail or not-evaluated totals — a case with nothing but informational
results has been scored by nothing.

## A failed case with no metrics at all

Verified against ADK 2.9.2 and re-measured on 2.10.0, 2.11.0 and 2.12.0: when inference itself fails — a bad API key, a model
that is not available, a tool that raised — the case is written with
`final_eval_status: 2` and an **empty** `overall_eval_metric_results`, and no
per-invocation entries.

That is not a score regression, and reading it as one sends you debugging an
agent whose behaviour was never observed. The fix is the environment. The
analyzer reports this case separately for exactly that reason.

## Efficiency entries (2.10+)

An efficiency metric's entry carries a score, `eval_status: 4`, and **no**
`threshold` and no `criterion` (both are None and dropped on serialization):

```json
{"metric_name": "tool_call_count_v1", "score": 1.0, "eval_status": 4, "details": {}}
{"metric_name": "invocation_duration_v1", "score": 2.5, "eval_status": 4, "details": {}}
{"metric_name": "token_usage_v1", "score": 312.0, "eval_status": 4,
 "details": {"token_usage_details": {
    "total_tokens": 312.0,
    "input_tokens": 257.0, "prompt_tokens": 250.0, "cached_tokens": 40.0,
    "tool_use_tokens": 7.0,
    "output_tokens": 55.0, "candidates_tokens": 50.0, "reasoning_tokens": 5.0}}}
```

`token_usage_details` is `EvalMetricResultDetails.token_usage_details`; the same
`TokenUsageDetails` shape also hangs off `PerInvocationResult.token_usage_details`
and `EvaluationResult.overall_token_usage_details`. The eight counts **nest**
rather than add up as a flat list: `total_tokens` contains `input_tokens`
(`prompt_tokens`, of which `cached_tokens`, plus `tool_use_tokens`) and
`output_tokens` (`candidates_tokens` plus `reasoning_tokens`). A count is `null`
when no model call reported it — unavailable, not zero.

Two new source fields feed them: `Invocation.duration` (wall-clock seconds, set
only when this eval run performed the inference, `null` for a hand-written or
replayed case) and, on each `InvocationEvent`, `usage_metadata` and
`model_version`.

A case whose inference failed produces none of this: no invocations, no
efficiency entries. The four metrics report only what a completed run observed.

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

## Coming after 2.12.0 (unreleased main)

Three result-file changes sit on `adk-python` main after the `v2.12.0` tag and
are in no release yet. A parser should tolerate them now; this plugin's does.

- **`details.duration_details`** on the `invocation_duration_v1` entry (overall
  and per invocation): `{total_seconds, model_seconds, tool_seconds,
  framework_seconds}`. The parts partition the turn, so they add back up to the
  total; overlapping model calls or tool runs count once, and
  `framework_seconds` is the remainder. A field is `null` when the invocation was
  read back rather than run (commit `e3797a4`).
- **Vertex rubric verdicts** folded into `details.rubric_scores`, with an extra
  entry whose `rubric_id` is `"explanation"` carrying the judge's overall
  explanation (commit `bc220d2`). Do not count it as a rubric.
- **Per-metric `eval_status`** honoured by `AgentEvaluator.evaluate` (commit
  `5bf7f12`): the overall verdict takes its direction from the evaluator's own
  per-invocation statuses, so a lower-is-better metric (cost, latency) is no
  longer failed by a hard-coded `score >= threshold`. This touches the pytest
  path only; the files `adk eval` writes are unchanged by it.

Main also makes bare `adk eval` exit 1 on a failure (`c2ff634`). On every released
version the exit code of `run_evals.py`, computed from these files, is the
authoritative verdict.

## Deprecated fields

`eval_set_file` (use `eval_set_id`) and `eval_metric_results` (use
`overall_eval_metric_results`) still appear in older files.

---

Sources: `adk-python: evaluation/eval_result.py`, `evaluation/eval_metrics.py`
(`EvalStatus.INFORMATIONAL = 4`, `TokenUsageDetails`,
`EvalMetricResultDetails.token_usage_details`), `evaluation/evaluator.py`
(`PerInvocationResult.token_usage_details`,
`EvaluationResult.overall_token_usage_details`), `evaluation/eval_case.py`
(`Invocation.duration`, `InvocationEvent.usage_metadata` and `model_version`),
`evaluation/_efficiency_evaluators.py`, `evaluation/eval_rubrics.py`,
`evaluation/local_eval_set_results_manager.py`,
`evaluation/local_eval_service.py`, `cli/cli_tools_click.py`. Result files
re-measured on installed 2.9.2, 2.10.0, 2.11.0 and 2.12.0 wheels (on 2.12.0 a
credential-less run still writes `final_eval_status: 2` with no metrics); the efficiency entry shape was
read off the evaluators driven over a synthetic invocation. Post-2.12 shapes:
`evaluation/eval_metrics.py` (`DurationDetails`) on main `a6ed859`.
