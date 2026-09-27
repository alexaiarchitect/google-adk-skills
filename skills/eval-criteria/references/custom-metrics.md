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

```json
{
  "criteria": {
    "claim_id_matches_request": {
      "threshold": 1.0,
      "custom_function_path": "app.metrics.claim_id_matches_request"
    }
  }
}
```

The dotted path must import from wherever the evaluation runs. Put the module
**inside the agent package** (`app/metrics.py` → `app.metrics`); a module at the
project root only imports when the working directory happens to be right, which
CI will eventually get wrong. `validate_config.py` resolves the path (CFG055)
and will say so when it cannot.

## Optional: a `MetricInfo` provider

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

**`requires_threshold` (2.10+)** is a new `MetricInfo` field, default `True`.
ADK's four efficiency metrics are the only things that set it `False`: a metric
that does not require a threshold reports a value and an `INFORMATIONAL` status
instead of a verdict, and ADK's own wording says surfaces that ask a user to pick
metrics and set thresholds should skip those. A custom metric reads
`criterion.threshold` to decide a pass or fail, so leave the default alone;
setting it `False` on a metric whose evaluator still compares against a threshold
only misleads whoever reads the listing. Note that `False` does not buy you the
efficiency metrics' behaviour — theirs is in their evaluators, which *reject* a
configured threshold outright.

## Cost and tier

A custom metric is deterministic, so it costs nothing and belongs in the
**pull-request tier** alongside `tool_trajectory_avg_score`. That is the real
argument for writing one where you could have written a rubric: the check runs
on every change instead of nightly.

Sources: `adk-python/src/google/adk/evaluation/custom_metric_evaluator.py`
(the call signature, the awaitable handling, and the `threshold = None` line);
`adk-python/src/google/adk/evaluation/evaluator.py` (`EvaluationResult`,
`PerInvocationResult`, `EvalStatus`);
`adk-python/src/google/adk/evaluation/eval_metrics.py` (`EvalMetric`,
`BaseCriterion`, `MetricInfo`, `MetricValueInfo`, `Interval`);
`adk-docs/docs/evaluate/custom_metrics.md`. Verified against live ADK 2.9.2 and
2.10.0 installs by driving a generated metric through `_CustomMetricEvaluator`.
Background reading: the LinkedIn Learning course "Evaluating AI Agents with
Google ADK", for the criteria methodology these metrics extend.
