# The result file

## Where

```
<agent_dir>/.adk/eval_history/<app_name>_<eval_set_id>_<timestamp>.evalset_result.json
```

`adk create` gitignores `.adk/`. With `--eval_storage_uri gs://...` the same
content goes to Cloud Storage instead (`gs://` only through 2.13.0; main after
2.13.0 also takes `file://` and `ADK_EVAL_STORAGE_URI`, writing the same
`<app>/.adk/eval_history/` tree under that directory). Unchanged on 2.10 through 2.13: the history still lands
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
wrong question". The list is the voted result (per invocation, with the rationale
of the first sample on the winning side; per case, the mean): the individual
samples are not in the file (2.9.2 and 2.13.0), so judge stability cannot be read
back from it, only re-measured. A rubric the judge skipped or answered without a
yes/no verdict is simply missing from the list through 2.13.0 (and the metric's
score averages the rest); compare the ids against the config.

## In 2.13.0

Three result-file changes shipped in 2.13.0 (they sat on main after 2.12.0). A
parser has to tolerate both shapes; this plugin's does.

- **`details.duration_details`** on the `invocation_duration_v1` entry (overall
  and per invocation): `{total_seconds, model_seconds, tool_seconds,
  framework_seconds}`. The parts partition the turn, so they add back up to the
  total; overlapping model calls or tool runs count once, and
  `framework_seconds` is the remainder. A field is `null` when the invocation was
  read back rather than run (commit `e3797a4`). Measured on 2.13.0: only the
  `invocation_duration_v1` entry carries it (`duration_details` is `null` on the
  other metrics), and on 2.12.0 the `details` object has no such key at all.
- **Vertex rubric verdicts** folded into `details.rubric_scores` as ordinary
  `RubricScore` rows — no new field — plus an entry whose `rubric_id` is
  `"explanation"` carrying the judge's overall explanation and, when the service
  failed, one whose `rubric_id` is `"error"` (commit `bc220d2`; Vertex-served
  metrics only). Count neither as a rubric.
- **Per-metric `eval_status`** honoured by `AgentEvaluator.evaluate` (commit
  `5bf7f12`): the overall verdict takes its direction from the evaluator's own
  per-invocation statuses, so a lower-is-better metric (cost, latency) is no
  longer failed by a hard-coded `score >= threshold`. This touches the pytest
  path only; the files `adk eval` writes are unchanged by it.

2.13.0 also makes bare `adk eval` exit 1 when a case fails or none ran (`c2ff634`; 0 whatever happened through 2.12.0), and rejects a drive-qualified `eval_set_id` (`c:pass`) when it saves these files, after inference. The exit code of `run_evals.py`, computed from these files, stays the recommended verdict on every version: it separates failed from never-scored and carries the pass-rate gate.

## Coming after 2.13.0 (unreleased main)

`adk-python` main after the `v2.13.0` tag (`7f3517e`) adds `repetition_index` to `EvalCaseResult` and `InferenceResult`, set when `adk eval --num_runs N` or `EvalConfig.multi_run_config` repeats a case; the cross-run verdict comes from `cross_run_scoring` (`mean_over_invocations` default, `mean_of_runs`, `min_over_runs`, or a `pass_rate` rule with a `threshold`) and `cross_run_criteria`, with `max_crashed_runs` default 0. `summarize_results.py` already groups repeated runs of one `eval_id` when the field is present. Main also serialises prompt-cache creation tokens on `LlmResponse`, has a tool argument matcher not yet used by any evaluator, and lets a plugin set compaction per session. Released versions drop `multi_run_config` silently, so `pass_at_k.py` and `run_evals.py --runs` remain the way to batch today.

Three scores move on main without a shape change, so a baseline recorded on
2.13.0 and a candidate on main can differ for no behavioural reason:

- **`token_usage_v1`** (`5e52b21a8`): through 2.13.0 each count is averaged over every invocation, so a turn that reported no usage pulls the mean down; main averages only invocations that reported input or output tokens.
- **`response_match_score`** (`571670d9f`): main ignores thought parts when computing ROUGE-1; 2.13.0 joins every text part, thoughts included, so a thinking model scores lower than its visible answer deserves.
- **`final_response_match_v2`** (`4c66a0091`): through 2.13.0 any judge label other than `true`/`invalid` counts as VALID; main maps `almost` and `partially…` to INVALID and an unknown label to NOT_FOUND.

Pin both runs to one ADK version before reading a delta on these metrics.

Checked again at `ee8720162` (2026-11-02), two more:

- **Unscored rubric samples** (`d1f2add4f`): a judge response that leaves a criterion rubric without a yes/no verdict makes that sample unscored (no `rubric_scores`, score None) instead of averaging the answered rubrics. The vote skips such samples; if all are unscored the metric's `score` is null and `eval_status` 3 (`NOT_EVALUATED`). A case that passed on 2.13.0 because a rubric went missing can read `NOT_EVALUATED` or `FAILED` on main.
- **Live usage** (`8cd3b12c8`): for evals under `live_model_config`, standalone Gemini Live usage reports now reach `token_usage_v1`'s `token_usage_details` (and the call count); through 2.13.0 they were dropped, so live token figures were zero or partial. Do not compare live token usage across the two.

Checked again at `70e1cbde7` (2026-11-04), two more:

- **Reloaded tool declarations** (`425576020`): through 2.13.0, `app_details.agent_details.<name>.tool_declarations` read back from a result or history file stay plain dicts. A live run is unaffected; code that reloads saved invocations and judges them again is not: `rubric_based_multi_turn_trajectory_quality_v1` raises `AttributeError: 'dict' object has no attribute 'function_declarations'` (`rubric_based_multi_turn_trajectory_evaluator.py:268`), and the other rubric and hallucination judges receive a tool block padded with every unset field as `null` (measured: about six times longer). Main restores them as `genai.types.Tool` objects. This plugin's readers never touch the field.
- **Silent turns** (`94eb6c4fc`): a case with a turn the agent answered with no event now appears with an invocation whose `final_response` is null; through 2.13.0 no result file is written at all (`/adk:eval-run` `cli-reference.md`).

Checked again at `175682204` (2026-11-05):

- **Per-turn rubric scores** (`e4f6c3a9c`): through 2.13.0 an earlier turn's verdicts on rubrics only that turn carries are missing from its `rubric_scores` (dropped as not found), so a turn and its case can read PASSED with a failing rubric; main scores each turn on its own list. Read `eval-criteria` `rubric-authoring.md`; `validate_evalset.py` warns (`EVS121`).
- **BigQuery eval views** (`16898f30c`, `10b716235`, `5e83dd5b2`): a turn whose trace holds a truncated payload is no longer eval-ready; a failed tool call gets an `{"error": ...}` function response, and with `eval_view_mode="full"` (itself main only) TOOL_ERROR becomes a required event; transfer and human-in-the-loop requests stop counting as tool calls or errors; `build_eval_session_query_sql` / `build_eval_turn_query_sql` are exported from `google.adk.plugins` and need no BigQuery extra.

**From production traces (in 2.13.0).** The BigQuery agent analytics plugin can build two views over logged sessions, `v_eval_session` and `v_eval_turn` (`create_eval_views`); they hold session-level counts and an eval-ready trace, not these result files. `/adk:agent-create` `app-and-plugins.md` has the setup; use them to choose which sessions to turn into cases.

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
read off the evaluators driven over a synthetic invocation. The 2.13.0 shapes
(`DurationDetails`, `duration_details` on `invocation_duration_v1` only) were
measured on the installed 2.13.0 wheel with a deterministic fake model; the
rubric-verdict rows (`bc220d2`) were read in source, not measured, because they
need Vertex credentials. `repetition_index` is `evaluation/eval_result.py` on main
`12c7bed76`, post-tag; the three score changes are post-tag at `dfd534a88`
(2.13.0 behaviour read in the installed wheel's `_efficiency_evaluators.py`,
`response_evaluator.py` and `final_response_match_v2.py`); the unscored sample
and live usage are post-tag at `ee8720162` (`rubric_based_evaluator.py`,
`evaluation_generator.py`); the absence of per-sample verdicts was read off the
2.9.2 and 2.13.0 result models (`local_eval_service.py` writes the voted list).
Tool declarations and silent turns: `evaluation/app_details.py` and
`evaluation_generator.py` at `70e1cbde7`, measured on 2.9.2 and 2.13.0 by a JSON
round trip and a two-turn case; eval views: `plugins/bigquery_agent_analytics_plugin.py`
on 2.13.0, `adk-docs: docs/integrations/bigquery-agent-analytics.md`; per-turn rubrics and the eval-view changes read at main `175682204` (rubric parsing measured with a stub judge on 2.13.0 and main).
