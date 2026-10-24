# Custom metrics

A custom metric is a Python function ADK calls instead of one of its own
evaluators. It is the right tool for a **deterministic business invariant** the
catalog cannot express, and the wrong tool for almost everything else.

## When it is the right answer

Reach for one when all three hold:

1. The check is **objective** — two people reading the same trace agree on the
   answer, so a judge is overkill and would only add variance.
2. It is **not already a metric**. Trajectory, exact and fuzzy response match,
   rubric judgement, groundedness and safety are covered; check the catalog
   first.
3. It is about **this agent's domain**, not about agents in general. "The
   submitted claim id equals the one in the user's message" is a real invariant;
   "the answer is good" is a rubric.

Do **not** write a custom metric that calls a model. That is a judge metric with
none of the calibration machinery around it — no sampling, no rubric, no way to
check it against human judgement — and it will drift without anyone noticing.
If judgement is needed, use `rubric_based_*` and write the rubric.

## The call contract

ADK resolves `custom_function_path` with `importlib.import_module` and calls the
result with four positional arguments:

```python
def my_metric(
    eval_metric: EvalMetric,
    actual_invocations: list[Invocation],
    expected_invocations: Optional[list[Invocation]] = None,
    conversation_scenario: Optional[ConversationScenario] = None,
) -> EvaluationResult:
```

| Argument | What it holds |
|---|---|
| `eval_metric` | the metric as configured — see the threshold gotcha below |
| `actual_invocations` | what the agent did, one entry per turn |
| `expected_invocations` | the golden trace, or `None` for a scenario case |
| `conversation_scenario` | the scenario, for user-simulation cases |

The return may be an `EvaluationResult` **or** an awaitable of one:
`_CustomMetricEvaluator` checks `inspect.isawaitable` and awaits it. So an
`async def` works with no extra registration.

## The threshold gotcha

This is the one that costs an afternoon:

```python
eval_metric = self._eval_metric.model_copy(deep=True)
eval_metric.threshold = None          # <- before your function is called
eval_result = self._metric_function(eval_metric, ...)
```

`eval_metric.threshold` is **always `None`** inside a custom metric. The
configured number is on the criterion:

```python
threshold = eval_metric.criterion.threshold
```

Code that reads `eval_metric.threshold` either raises on the comparison or,
worse, treats every case as passing. Always go through `criterion`, and fall
back to a module-level default when the config named the metric without one.

## The return shape

```python
EvaluationResult(
    overall_score=0.75,
    overall_eval_status=EvalStatus.PASSED,
    per_invocation_results=[
        PerInvocationResult(
            actual_invocation=...,
            expected_invocation=...,
            score=1.0,
            eval_status=EvalStatus.PASSED,
        ),
        ...
    ],
)
```

`EvalStatus` is `PASSED = 1`, `FAILED = 2`, `NOT_EVALUATED = 3`.

Two details worth getting right:

- **Emit one `PerInvocationResult` per invocation.** Without them the result
  file can say the case failed but not which turn, which is most of the value of
  a structured result.
- **Return `NOT_EVALUATED` when there is nothing to score**, rather than 0.0.
  A case the metric could not judge and a case the agent failed are different
  facts; collapsing them is how a suite that stopped working looks green. The
  wrapper scripts in this plugin already separate the two, but only if the
  metric tells the truth.

## Registration

Two entries, one in each top-level key: `criteria` decides that the metric runs
and with what threshold, `custom_metrics` says where its function lives.

```json
{
  "criteria": {
    "claim_id_matches_request": 1.0
  },
  "custom_metrics": {
    "claim_id_matches_request": {
      "code_config": {"name": "app.metrics.claim_id_matches_request"},
      "description": "The submitted claim id is the one the user gave."
    }
  }
}
```

`code_config.name` is split at its last dot and imported, so the module part must
import from wherever the evaluation runs. Put the module **inside the agent
package** (`app/metrics.py` → `app.metrics`); a module at the project root only
imports when the working directory happens to be right, which CI will eventually
get wrong. `validate_config.py` resolves the path (CFG055) and says so when it
cannot.

**The old shape fails late.** A `custom_function_path` key inside the `criteria`
entry is accepted (criteria allow extra keys) and ignored, so the config loads
and the run then fails with "not found in registry". Same on 2.9.2 and 2.11.0.
`gen_custom_metric.py` prints the two-entry snippet and also keeps it in the
generated module as `CONFIG_SNIPPET`.

For more than a one-line description, `metric_info` takes a whole `MetricInfo`.
Its value range sits **inside** `metric_value_info.interval`:

```json
"metric_info": {
  "metric_name": "claim_id_matches_request",
  "description": "The submitted claim id is the one the user gave.",
  "metric_value_info": {"interval": {"min_value": 0.0, "max_value": 1.0}}
}
```

Without `metric_info` ADK assumes a 0–1 range. `MetricInfo`, `MetricValueInfo`
and `Interval` all forbid unknown keys, so `min_value` at the top of
`metric_info` fails validation loudly. A key spelled `metric` instead of
`metric_info` is not a field of `CustomMetricConfig`, so it is dropped and the
metric quietly gets default info. Older ADK docstrings showed that wrong shape;
the field has been `metric_info` on every 2.x release. `validate_config.py`
catches both: `CFG057` (warn) for an unrecognized key on a custom metric, with
a pointer to `metric_info.metric_value_info.interval`, and `CFG058` (error) for
a `metric_info` missing `metric_name` or `metric_value_info`, or an interval
without numeric bounds.

## Trajectory precision and recall

`tool_trajectory_avg_score` answers one question per invocation with a 1 or a 0,
and its three match types differ only in what they forgive:

| Built-in | Extra calls | Missing calls | Order |
|---|---|---|---|
| `EXACT` | fail | fail | must match |
| `IN_ORDER` | allowed | fail | must match |
| `ANY_ORDER` | allowed | fail | ignored |

Two things none of them can say: **"no extra calls, in any order"**, and **"how
much of the expected work was done"**. Precision and recall say both:

- **precision** = matched calls / actual calls — falls when the agent does
  something nobody asked for. At threshold 1.0 it is a deterministic restraint
  check.
- **recall** = matched calls / expected calls — falls when the agent skips
  expected work, and gives partial credit for a long trajectory that was mostly
  right.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/gen_custom_metric.py --name trajectory_precision \
    --out app/metrics_precision.py --kind trajectory-precision [--with-args]
```

`--kind trajectory-precision` and `--kind trajectory-recall` write the metric with
one `PerInvocationResult` per turn. Calls match as a **multiset** with order
ignored, by tool name by default and by name and arguments with `--with-args`.
An invocation with no expected trajectory (a user-simulation turn) is
`NOT_EVALUATED`, not failed. Driven over the same synthetic invocation on 2.9.2
and 2.11.0, the two kinds scored 2/3 and 0.5. The generator prints both config
entries.

When to prefer them:

| Need | Use |
|---|---|
| the pipeline order is the rule | `tool_trajectory_avg_score` `EXACT` or `IN_ORDER` |
| no unrequested side effects, order irrelevant | precision at 1.0 (plus recall at 1.0 for "exactly these, any order") |
| long or exploratory trajectories, where all-or-nothing hides progress | recall with a threshold below 1.0, nightly |

They are computation-level metrics: free, deterministic, fit for the PR tier,
and a complement to `tool_trajectory_avg_score`, which stays where order matters.

## Optional: a `MetricInfo` provider function

```python
def my_metric_metric_info() -> MetricInfo:
    return MetricInfo(
        metric_name="my_metric",
        description="One line, read by whoever has to decide whether to enable it.",
        metric_value_info=MetricValueInfo(
            interval=Interval(min_value=0.0, max_value=1.0)
        ),
    )
```

ADK scores the metric without one. It exists so the metric is legible in the dev
UI and in metric listings, which starts to matter the moment somebody other than
the author is choosing criteria.

**`requires_threshold` (2.10+)** is a `MetricInfo` field, default `True`; only
ADK's four efficiency metrics set it `False`, which makes a metric report a value
with an `INFORMATIONAL` status instead of a verdict. A custom metric decides pass
or fail from `criterion.threshold`, so leave the default alone. `False` does not
buy the efficiency metrics' behaviour either: their evaluators reject a
configured threshold outright.

## Cost and tier

A custom metric is deterministic, so it costs nothing and belongs in the
**pull-request tier** alongside `tool_trajectory_avg_score`. That is the real
argument for writing one where you could have written a rubric: the check runs
on every change instead of nightly. Its rule also stays fixed between runs, which makes it the before/after instrument beside adaptive judges (`/adk:eval-analyze` `improvement-loop.md`).

Sources: `adk-python/src/google/adk/evaluation/custom_metric_evaluator.py`
(the call signature, the awaitable handling, and the `threshold = None` line);
`adk-python/src/google/adk/evaluation/evaluator.py` (`EvaluationResult`,
`PerInvocationResult`, `EvalStatus`);
`adk-python/src/google/adk/evaluation/eval_metrics.py` (`EvalMetric`,
`BaseCriterion`, `MetricInfo`, `MetricValueInfo`, `Interval`);
`adk-python/src/google/adk/evaluation/eval_config.py` (`CustomMetricConfig`:
`code_config`, `metric_info`, `description`, identical on 2.9.2 and 2.11.0) and
`docs/guides/evaluation/eval_config/index.md` (the `metric_value_info.interval`
shape, main);
`adk-docs/docs/evaluate/custom_metrics.md`. Verified against live ADK 2.9.2 and
2.10.0 installs by driving a generated metric through `_CustomMetricEvaluator`;
the `custom_metrics` shape validated on 2.11.0. The precision/recall framing for
trajectories is from Vlad Kolesnikov, "From 'vibe checks' to continuous
evaluation: engineering reliable AI agents", Google Cloud blog, 2026-02-28,
https://cloud.google.com/blog/topics/developers-practitioners/from-vibe-checks-to-continuous-evaluation-engineering-reliable-ai-agents.
Background reading: the LinkedIn Learning course "Evaluating AI Agents with
Google ADK", for the criteria methodology these metrics extend.
