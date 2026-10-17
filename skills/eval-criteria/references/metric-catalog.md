# The ADK evaluation metrics: 17 registered, 13 configurable

ADK 2.10 through 2.12 register 17 metrics, and this plugin's catalog has all 17. Thirteen of
them are the ones you put in `criteria` — the table below. The other four are the
efficiency metrics: always on, never configured, informational only (last
section). `metric_info.py` lists them with tier `informational` and a value range
of `0+` (JSON: `[0.0, null]`, unbounded), which is how you tell them apart from a
configurable metric at a glance.

| metric | cost | reference | rubrics | user sim | range | tier |
|---|---|---|---|---|---|---|
| `tool_trajectory_avg_score` | free | trajectory | no | no | 0-1 | pr |
| `response_match_score` | free | response | no | no | 0-1 | pr |
| `response_evaluation_score` | vertex | response | no | no | **1-5** | nightly |
| `final_response_match_v2` | judge | response | no | no | 0-1 | nightly |
| `rubric_based_final_response_quality_v1` | judge | no | required | yes | 0-1 | nightly |
| `rubric_based_tool_use_quality_v1` | judge | no | required | yes | 0-1 | nightly |
| `rubric_based_multi_turn_trajectory_quality_v1` | judge | no | required | yes | 0-1 | nightly |
| `hallucinations_v1` | judge | no | no | yes | 0-1 | nightly |
| `safety_v1` | vertex | no | no | yes | 0-1 | nightly |
| `per_turn_user_simulator_quality_v1` | judge | no | no | yes | 0-1 | nightly |
| `multi_turn_task_success_v1` | vertex | no | no | yes | 0-1 | nightly |
| `multi_turn_trajectory_quality_v1` | vertex | no | no | yes | 0-1 | nightly |
| `multi_turn_tool_use_quality_v1` | vertex | no | no | yes | 0-1 | nightly |

**reference** is what the eval case must supply: `trajectory` means expected
`tool_uses`, `response` means expected `final_response` text.

A simulated conversation has no fixed expected invocation, so ADK cannot score
either kind against one: these come back `NOT_EVALUATED`, and the metrics that
require an expected invocation raise. Nothing in the schema stops you writing the
combination — **the refusal is this plugin's, applied up front**, so you find out
when you generate the config rather than after a paid run produces a page of
blanks.

With **no config at all**, ADK uses `tool_trajectory_avg_score` 1.0 and
`response_match_score` 0.8 — so an unconfigured run silently fails cases on
wording. On 2.10+ the four efficiency metrics are appended to whatever you
configured, including that default pair.

## The deterministic pair

### `tool_trajectory_avg_score`

Compares tool calls, names and arguments, against the expected trajectory. The
backbone metric: no model calls, seconds to run, and the only one that can see a
skipped validation or a reordered pipeline.

| Match type | Passes when |
|---|---|
| `EXACT` | identical calls, identical arguments, identical order, nothing extra |
| `IN_ORDER` | every expected call appears in the expected relative order; extra calls in between are allowed |
| `ANY_ORDER` | every expected call appears, in any order |

`ignore_args` (2.9+, unchanged through 2.12) compares names only. It makes a red suite green without
making the agent better — the right tool with the wrong id passes — so keep it
off wherever the result gates a release.

### `response_match_score`

ROUGE-1 word overlap with the reference text. Cheap, and blind to meaning: a
correct paraphrase fails it. Set it to 0.0 to run trajectory-first, or 0.8 where
the wording really is fixed.

## Judge metrics

### `final_response_match_v2`

A judge decides whether the response means the same as the reference. Use it
instead of `response_match_score` when the wording is free.

### The three rubric metrics

Each takes a list of yes/no rubrics, scores each one per invocation with a
majority vote over `num_samples`, and averages.

| Metric | Judges | Per-case rubric `type` |
|---|---|---|
| `rubric_based_final_response_quality_v1` | the final response | `FINAL_RESPONSE_QUALITY` |
| `rubric_based_tool_use_quality_v1` | the tool calls | `TOOL_USE_QUALITY` |
| `rubric_based_multi_turn_trajectory_quality_v1` | the whole conversation | `TRAJECTORY_QUALITY` |

Criterion-level rubrics are used unfiltered; per-case rubrics are filtered by
`type` and merged on top. The effective list must not be empty or evaluation
raises.

**The tool-use one carries restraint guarantees.** Phrase them as negatives
("does NOT call `submit_claim` when the cost centre is frozen") and set the
threshold to **1.0**: a guarantee that holds 80% of the time is not a guarantee.

### `hallucinations_v1`

Splits the response into sentences and labels each against everything the agent
saw — tool definitions, tool results, the instruction, the user's prompt — as
supported, unsupported, contradictory, disputed or not applicable.

Set `evaluate_intermediate_nl_responses` to `true`, or only the final message is
checked and an invented number in an intermediate message goes unnoticed.

### `per_turn_user_simulator_quality_v1`

Scores the **simulated user**, not the agent. Run it when a simulated suite
starts failing and you need to know whether the harness or the agent moved.

## Vertex-backed metrics

`safety_v1`, `response_evaluation_score` and the three `multi_turn_*` metrics
call the Vertex AI Gen AI Evaluation Service. They need **either** a
`GOOGLE_API_KEY` **or** `GOOGLE_CLOUD_PROJECT` *and* `GOOGLE_CLOUD_LOCATION` with
Application Default Credentials — the facade tries the API key first — and this
holds regardless of `GOOGLE_GENAI_USE_VERTEXAI`.

What makes them the `vertex` tier is not a stricter credential but a different
service: separately billed, and they take no `judge_model_options`, because the
service chooses its own model. Setting judge options on one of these is the
quiet misconfiguration to look for.

Watch the range on `response_evaluation_score`: it is 1 to 5, so any threshold
below 1 can never fail.

## Choosing

| The question you want answered | Metric |
|---|---|
| Did it do the right things, in the right order, with the right values? | `tool_trajectory_avg_score` |
| Did it say the expected thing? | `response_match_score`, or `final_response_match_v2` for meaning |
| Did it refuse when it should have? | `rubric_based_tool_use_quality_v1` at 1.0 |
| Did it invent anything? | `hallucinations_v1` |
| Is the answer well-formed for a user? | `rubric_based_final_response_quality_v1` |
| Is it safe to put in front of people? | `safety_v1` |
| Does it hold up across a real conversation? | `multi_turn_*`, with user simulation |
| How expensive did a turn get? | nothing to configure — the efficiency four are reported for you on 2.10+ |

## Cost

A judge metric costs one model call per invocation per sample. A suite of 20
cases with 2 invocations each, two rubric metrics, `num_samples: 3`, is 240 judge
calls per run — before pass@k multiplies it. That is the whole argument for the
tiering: deterministic metrics on every change, judge metrics nightly.

## Efficiency (informational, 2.10+)

Four metrics that report a number and never pass or fail anything. **ADK 2.10 and later
append all four to every eval automatically** — you do not ask for them, and
there is no way to ask for them.

| metric | unit | cost | reference | threshold |
|---|---|---|---|---|
| `tool_call_count_v1` | tool calls per invocation | free | none | none — rejected |
| `inference_call_count_v1` | model calls per invocation | free | none | none — rejected |
| `token_usage_v1` | tokens per invocation (`total_tokens`) | free | none | none — rejected |
| `invocation_duration_v1` | wall-clock **seconds** per invocation | free | none | none — rejected |

Each is computed per invocation and **averaged** over the eval case; invocations
that report nothing are left out of the average. All four write
`eval_status: 4` (`INFORMATIONAL`), overall and per invocation, so they never
land in a pass or fail column — `/adk:eval-analyze` shows them in their own
table.

**Naming one in `criteria` is an error, not a no-op.** There is no way to write
the name without a threshold (`null` is refused by pydantic), and a threshold is
what the evaluator rejects — on 2.10+, at the moment the run reaches the metric:

```
ValueError: `tool_call_count_v1` does not support a threshold: it reports a value
and never passes or fails. Remove it from `criteria` entirely; it is reported
automatically on every eval.
```

On 2.9 the same config fails at the same point with a different error,
`NotFoundError: tool_call_count_v1 not found in registry.`, because the metric
does not exist there at all. Config-level validation catches neither: `EvalConfig`
accepts any metric name on both versions, which is why this plugin reports it
(`CFG070`) before you run.

`token_usage_v1` also carries `details.token_usage_details`: `total_tokens`,
`input_tokens`, `prompt_tokens`, `cached_tokens`, `tool_use_tokens`,
`output_tokens`, `candidates_tokens`, `reasoning_tokens`.

ADK's own wording, verbatim from `MetricInfo.description`:

> **`tool_call_count_v1`** — "This metric counts the number of tool (function)
> calls the agent made per invocation, averaged across the eval case. It is an
> informational efficiency metric: it reports the value for tracking and does not
> pass or fail the eval case."

> **`inference_call_count_v1`** — "This metric counts the number of inference
> (model) calls the agent made per invocation, averaged across the eval case. It
> is a proxy for the number of attempts or reasoning steps taken, and read
> alongside token usage it separates the two ways a turn gets expensive: more
> calls, or a larger context per call. It is an informational efficiency metric:
> it reports the value for tracking and does not pass or fail the eval case."

> **`token_usage_v1`** — "This metric sums the tokens consumed by the model
> across all model calls in an invocation, averaged across the eval case. The
> score is the total; every token type is reported alongside it as a nested
> breakdown -- total, then input (prompt, of which cached, plus tool use) and
> output (candidates plus reasoning) -- using the same definitions as ADK's
> telemetry token metrics. It is an informational efficiency metric: it reports
> the value for tracking and does not pass or fail the eval case."

> **`invocation_duration_v1`** — "This metric reports the wall-clock seconds an
> invocation took, averaged across the eval case. The duration is measured while
> the agent runs; an invocation not produced by this eval run reports no value.
> Wall-clock time is noisier than the token and call counts, since it moves with
> model-server load and network, so read it as an indication rather than a
> regression signal. It is an informational efficiency metric: it reports the
> value for tracking and does not pass or fail the eval case."

Read them as a trend across runs, not as a gate: a duration that doubles is a
question, not a failure, and the two call counts tell you whether a turn got
expensive through more attempts or a bigger context.

---

Sources: `adk-python: evaluation/eval_metrics.py`,
`evaluation/metric_evaluator_registry.py`, `evaluation/metric_info_providers.py`,
`evaluation/hallucinations_v1.py`, `evaluation/rubric_based_*.py`,
`evaluation/safety_evaluator.py`, `evaluation/response_evaluator.py`,
`evaluation/eval_config.py`; `adk-docs: docs/evaluate/criteria.md`. The
efficiency four: `adk-python: evaluation/_efficiency_evaluators.py` (new in
2.10.0, `_reject_threshold`), `evaluation/metric_info_providers.py` (the
descriptions quoted above, and `requires_threshold`),
`evaluation/metric_evaluator_registry.py` (`_register_standard_metrics`),
`evaluation/eval_config.py` (`get_eval_metrics_from_config` appends them);
measured on installed wheels 2.9.2 and 2.10.0; the 17-metric registry and
`_reject_threshold` re-read on 2.11.0 and 2.12.0.
