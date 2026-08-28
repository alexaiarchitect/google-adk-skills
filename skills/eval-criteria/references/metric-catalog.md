# The 13 ADK evaluation metrics

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
wording.

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

`ignore_args` (2.9) compares names only. It makes a red suite green without
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

## Cost

A judge metric costs one model call per invocation per sample. A suite of 20
cases with 2 invocations each, two rubric metrics, `num_samples: 3`, is 240 judge
calls per run — before pass@k multiplies it. That is the whole argument for the
tiering: deterministic metrics on every change, judge metrics nightly.

---

Sources: `adk-python: evaluation/eval_metrics.py`,
`evaluation/metric_evaluator_registry.py`, `evaluation/metric_info_providers.py`,
`evaluation/hallucinations_v1.py`, `evaluation/rubric_based_*.py`,
`evaluation/safety_evaluator.py`, `evaluation/response_evaluator.py`,
`evaluation/eval_config.py`; `adk-docs: docs/evaluate/criteria.md`.
