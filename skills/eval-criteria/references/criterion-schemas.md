# Config and criterion shapes

## The config file

```json
{
  "criteria": { "<metric name>": <threshold> | { ... } },
  "custom_metrics": { "<metric name>": {"code_config": {"name": "pkg.module.fn"}} },
  "user_simulator_config": {"type": "llm_backed", "model": "gemini-flash-latest"},
  "live_model_config": {"timeout_seconds": 300}
}
```

Two things to know before editing one:

- **It must be JSON.** ADK reads it with `EvalConfig.model_validate_json`, so a
  YAML file cannot be passed to `--config_file_path` no matter what it contains.
- **Both key styles parse.** `judge_model_options` and `judgeModelOptions` are
  the same field. Pick one per project; this plugin writes snake_case, which is
  what the docs and the CLI use.

## Criterion values

A bare number is a threshold:

```json
"response_match_score": 0.8
```

An object carries the threshold plus whatever that metric supports:

```json
"tool_trajectory_avg_score": {"threshold": 1.0, "match_type": "IN_ORDER", "ignore_args": false}
```

| Criterion class | Used by | Fields beyond `threshold` |
|---|---|---|
| `BaseCriterion` | simple metrics | `include_intermediate_responses_in_final` — **and nothing else**, in particular no `judge_model_options` |
| `ToolTrajectoryCriterion` | `tool_trajectory_avg_score` | `match_type`, `ignore_args` |
| `LlmAsAJudgeCriterion` | `final_response_match_v2` | `judge_model_options` |
| `RubricsBasedCriterion` | the three rubric metrics | `judge_model_options`, `rubrics` |
| `HallucinationsCriterion` | `hallucinations_v1` | `judge_model_options`, `evaluate_intermediate_nl_responses` |
| `LlmBackedUserSimulatorCriterion` | `per_turn_user_simulator_quality_v1` | `judge_model_options`, `stop_signal` |
| **none** | the four efficiency metrics (2.10+) | they take no criterion and no threshold: a `criteria` entry for one is refused at run time. Leave them out — ADK reports them anyway |

### Why a misspelled field never raises

`EvalConfig.criteria` is typed `dict[str, Threshold | BaseCriterion]`, so **every
criterion is first validated as a bare `BaseCriterion`** — whose only declared
fields are `threshold` and `include_intermediate_responses_in_final`, and whose
model config is `extra="allow"`. `match_type`, `judge_model_options`, `rubrics`
and every typo alike are simply *stored* as extras at this point. Nothing is
checked, because nothing yet knows which metric this is.

The subclass appears later: when the metric runs, its evaluator calls
`criterion_type.model_validate(criterion.model_dump())` and re-reads those extras
as real fields. That is the moment a genuine `match_type` takes effect — and the
moment a misspelling quietly does not, because it is still just an extra on the
new object too.

```json
"tool_trajectory_avg_score": {"threshold": 1.0, "match_types": "IN_ORDER"}
```

This validates cleanly, runs, and evaluates as `EXACT`. There is no warning
anywhere in ADK. It is the single most common silent misconfiguration, and the
only reason you find out is that this plugin's validator compares your keys
against each criterion class's real field set.

## Judge model options

```json
"judge_model_options": {
  "judge_model": "gemini-flash-latest",
  "num_samples": 3,
  "parallelism_limit": 1
}
```

| Field | Default | Notes |
|---|---|---|
| `judge_model` | `gemini-2.5-flash` | always set this. This plugin's policy treats that id as retiring and the reviewer warns on it; the source is `adk-recipes: AGENTS.md`, not ADK, which still ships it as the default |
| `num_samples` | 5 | judge calls per invocation, majority vote. 3 is a good balance; 1 lets one unlucky sample decide |
| `parallelism_limit` | 1 | concurrent judge calls |

Vertex-served metrics (`safety_v1`, `response_evaluation_score`, `multi_turn_*`)
ignore these: the evaluation service picks its own model.

## Rubrics

```json
"rubrics": [
  {
    "rubric_id": "names_the_failed_constraint",
    "rubric_content": {"text_property": "The final response names the specific constraint that blocked the request."}
  }
]
```

`rubric_id` appears in the per-rubric scores, so make it readable. On an eval
case, a rubric also takes a `type` and is only merged into criteria that expect
that type; criterion-level rubrics are not filtered.

## Custom metrics

```json
{
  "criteria": {"claim_amount_accuracy": 0.9},
  "custom_metrics": {
    "claim_amount_accuracy": {
      "description": "Fraction of submitted claims whose amount matches the request.",
      "code_config": {"name": "app.eval_metrics.claim_amount_accuracy"}
    }
  }
}
```

The function must be importable from the directory the eval runs in, and the
metric must appear in **both** blocks: listed only under `custom_metrics`, it
never runs.

Signature:

```python
def claim_amount_accuracy(eval_metric, actual_invocations, expected_invocations, conversation_scenario):
    threshold = eval_metric.criterion.threshold   # not eval_metric.threshold
    ...
```

ADK blanks `eval_metric.threshold` before calling, so read the criterion. The
function returns an evaluation result with a score and per-invocation results,
and may be sync or async.

## User simulation and live mode

`user_simulator_config` switches cases with a `conversation_scenario` to a
simulated user; see `/adk:eval-simulation`. `live_model_config` switches the run
to the live/bidi path, which changes how inference is performed. Its only field is
`timeout_seconds` (default 300, no bound enforced by ADK; `SIM032` warns at ≤ 0).
From 2.13.0, `judge_model_options.judge_model` and the simulator's `model` also
accept a `BaseLlm` instance on the programmatic path; a JSON config still names a
model id.

## Coming after 2.13.0 (unreleased main)

Main after the `v2.13.0` tag adds a fifth top-level key, `multi_run_config`
(`7f3517e`):

```json
{"criteria": {"tool_trajectory_avg_score": 1.0},
 "multi_run_config": {
   "num_runs": 5,
   "cross_run_scoring": "mean_over_invocations",
   "cross_run_scoring_by_metric": {"tool_trajectory_avg_score": "min_over_runs"},
   "cross_run_criteria": [{"method": "pass_rate", "threshold": 0.9}],
   "max_crashed_runs": 0}}
```

Methods are `mean_over_invocations` (default), `mean_of_runs`, `min_over_runs`,
a `{"method": "pass_rate", "threshold": ...}` rule, or a name from
`custom_methods` (each with a `code_config` and `uses_scores`); every key of
`cross_run_scoring_by_metric` must also be in `criteria`. Main also ships
`google.adk.tools.tool_args_matcher` (exact, casefold, numeric, contains and
ignore matchers; `ToolArgsMatcher(allow_extra_args=...)`), which no criterion
uses yet, so `tool_trajectory_avg_score` still compares arguments exactly; plus
per-session compaction from a plugin's `before_run_callback` and prompt-cache
creation tokens on `LlmResponse`. **Released versions drop `multi_run_config`
silently** — `EvalConfig` ignores unknown keys, measured on 2.9.2, 2.12.0 and
2.13.0 — so `pass_at_k.py` and `--runs` remain the way to batch today.
`validate_config.py` reports the key as `CFG072` (info) and a malformed one as
`CFG073` (error).

---

Sources: `adk-python: evaluation/eval_config.py` (the
`Threshold | BaseCriterion` union, and `get_eval_metrics_from_config`, which on
2.10+ appends the four efficiency metrics with no threshold),
`evaluation/_efficiency_evaluators.py` (`_reject_threshold`),
`evaluation/eval_metrics.py`
(`BaseCriterion` and its subclasses, `extra="allow"`),
`evaluation/llm_as_judge.py` and `evaluation/hallucinations_v1.py`
(`criterion_type.model_validate(...)`, the subclass re-validation),
`evaluation/eval_rubrics.py`, `evaluation/custom_metric_evaluator.py`,
`evaluation/rubric_based_evaluator.py`; `MultiRunConfig` and
`tools/tool_args_matcher/` on main `12c7bed76` (post-tag);
`adk-docs: docs/evaluate/criteria.md`, `docs/evaluate/custom_metrics.md`.
The `gemini-2.5-flash` judgement is this plugin's policy, from
`adk-recipes: AGENTS.md`.
