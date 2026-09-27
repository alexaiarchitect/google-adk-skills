"""The ADK 2.x evaluation-criteria catalog.

Seventeen metrics ship with ADK 2.10 (thirteen in 2.9, plus the four efficiency
metrics). Choosing among them is the decision that shapes an eval suite's cost,
speed and what it can actually prove, so the facts that drive that choice live
here in one table:

* **reference_based** — needs an expected `final_response` or `tool_uses` in the
  eval case. This plugin refuses to combine them with user simulation: a
  simulated conversation has no fixed reference, so ADK returns
  ``NOT_EVALUATED`` or raises rather than scoring anything. The schema permits
  the combination; it just cannot produce a verdict, and a suite full of
  unscored cases reads as passing.
* **needs_judge** — spends model calls on an LLM judge.
* **needs_vertex** — calls the Vertex AI Gen AI Evaluation Service, which needs
  either ``GOOGLE_API_KEY`` or ``GOOGLE_CLOUD_PROJECT`` +
  ``GOOGLE_CLOUD_LOCATION`` with ADC (``vertex_ai_eval_facade.py`` reads the API
  key first), whatever ``GOOGLE_GENAI_USE_VERTEXAI`` says. It stays its own cost
  tier because it is a separate service on a separate bill.
* **requires_rubrics** — scores nothing unless the effective rubric list is
  non-empty (criterion-level rubrics plus type-matching per-case rubrics).
* **ci_tier** — ``pr`` for deterministic metrics cheap enough to gate every
  change, ``nightly`` for anything that pays a model.
* **informational** — the four efficiency metrics added in 2.10. They report a
  value (lower is better) and never pass or fail a case, they take no threshold
  at all, and ADK runs all four on every 2.10 eval whether or not they are
  mentioned. Naming one in ``criteria`` is an error, not a no-op:
  ``MetricEvaluatorRegistry.get_evaluator`` raises ``ValueError`` on 2.10 and
  ``NotFoundError`` on 2.9. They are catalogued so the plugin can explain them
  and read them back out of a result file, never so a config can request them.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable

#: `adk eval` falls back to these when no config is supplied.
DEFAULT_CRITERIA: dict[str, float] = {
    "tool_trajectory_avg_score": 1.0,
    "response_match_score": 0.8,
}

#: `JudgeModelOptions` defaults in the library. The judge model is a version
#: this plugin's policy treats as deprecated, so always set it explicitly.
JUDGE_DEFAULTS = {"judge_model": "gemini-2.5-flash", "num_samples": 5, "parallelism_limit": 1}

MATCH_TYPES = ("EXACT", "IN_ORDER", "ANY_ORDER")

#: The efficiency metrics have no upper bound (`MetricValueInfo` carries no
#: `Interval`), so the catalog records the range as unbounded rather than
#: inventing a ceiling.
INFINITY = float("inf")


@dataclass(frozen=True)
class MetricSpec:
    name: str
    summary: str
    criterion_class: str
    value_range: tuple[float, float]
    reference_based: bool
    #: What the reference is: the expected ``tool_uses`` ("trajectory"), the
    #: expected ``final_response`` text ("response"), or nothing.
    reference_kind: str | None
    requires_rubrics: bool
    needs_judge: bool
    needs_vertex: bool
    user_sim_compatible: bool
    default_threshold: float
    ci_tier: str
    extra_fields: tuple[str, ...] = ()
    rubric_type: str | None = None
    when: str = ""
    #: Reports a value and never passes or fails a case (`EvalStatus`
    #: ``INFORMATIONAL`` = 4). Always on, and impossible to configure.
    informational: bool = False
    #: First ADK release that registers the metric, when it is not 2.9.
    since: str | None = None
    #: `MetricInfo.requires_threshold`: False for the efficiency metrics, which
    #: reject a threshold outright.
    requires_threshold: bool = True

    @property
    def cost(self) -> str:
        if self.needs_vertex:
            return "vertex"
        if self.needs_judge:
            return "judge"
        return "free"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # An unbounded maximum is written as null: `Infinity` is not valid JSON,
        # and a consumer that sees null knows there is no ceiling.
        data["value_range"] = [
            None if bound == float("inf") else bound for bound in self.value_range
        ]
        data["extra_fields"] = list(self.extra_fields)
        data["cost"] = self.cost
        return data


def _spec(**kwargs: Any) -> MetricSpec:
    return MetricSpec(**kwargs)


METRIC_CATALOG: dict[str, MetricSpec] = {
    "tool_trajectory_avg_score": _spec(
        name="tool_trajectory_avg_score",
        summary="Compares the agent's tool calls (name and arguments) with the expected trajectory.",
        criterion_class="ToolTrajectoryCriterion",
        value_range=(0.0, 1.0),
        reference_based=True, reference_kind="trajectory", requires_rubrics=False, needs_judge=False, needs_vertex=False,
        user_sim_compatible=False, default_threshold=1.0, ci_tier="pr",
        extra_fields=("match_type", "ignore_args"),
        when="The default gate. Deterministic, no model calls, runs in seconds; "
             "the only metric that can catch a skipped validation or a reordered pipeline.",
    ),
    "response_match_score": _spec(
        name="response_match_score",
        summary="ROUGE-1 unigram overlap between the final response and the reference text.",
        criterion_class="BaseCriterion",
        value_range=(0.0, 1.0),
        reference_based=True, reference_kind="response", requires_rubrics=False, needs_judge=False, needs_vertex=False,
        user_sim_compatible=False, default_threshold=0.8, ci_tier="pr",
        when="Cheap wording check. Brittle: paraphrases fail it. Set it to 0.0 to disable "
             "scoring on text while you gate on trajectory.",
    ),
    "response_evaluation_score": _spec(
        name="response_evaluation_score",
        summary="Vertex AI coherence rating of the agent's response.",
        criterion_class="BaseCriterion",
        value_range=(1.0, 5.0),
        reference_based=True, reference_kind="response", requires_rubrics=False, needs_judge=True, needs_vertex=True,
        user_sim_compatible=False, default_threshold=4.0, ci_tier="nightly",
        when="Fluency and coherence rather than correctness. Note the 1-5 range: a "
             "threshold below 1.0 can never fail.",
    ),
    "final_response_match_v2": _spec(
        name="final_response_match_v2",
        summary="LLM judge decides whether the response is semantically equivalent to the reference.",
        criterion_class="LlmAsAJudgeCriterion",
        value_range=(0.0, 1.0),
        reference_based=True, reference_kind="response", requires_rubrics=False, needs_judge=True, needs_vertex=False,
        user_sim_compatible=False, default_threshold=0.8, ci_tier="nightly",
        extra_fields=("judge_model_options",),
        when="Use instead of response_match_score when the wording is free but the meaning "
             "is fixed.",
    ),
    "rubric_based_final_response_quality_v1": _spec(
        name="rubric_based_final_response_quality_v1",
        summary="LLM judge scores the final response against your rubrics, yes/no per rubric.",
        criterion_class="RubricsBasedCriterion",
        value_range=(0.0, 1.0),
        reference_based=False, reference_kind=None, requires_rubrics=True, needs_judge=True, needs_vertex=False,
        user_sim_compatible=True, default_threshold=0.8, ci_tier="nightly",
        extra_fields=("judge_model_options", "rubrics"),
        rubric_type="FINAL_RESPONSE_QUALITY",
        when="No reference answer exists but the response has properties you can state as "
             "observable yes/no claims (tone, cites the constraint it hit, no invented data).",
    ),
    "rubric_based_tool_use_quality_v1": _spec(
        name="rubric_based_tool_use_quality_v1",
        summary="LLM judge scores the agent's tool use against your rubrics.",
        criterion_class="RubricsBasedCriterion",
        value_range=(0.0, 1.0),
        reference_based=False, reference_kind=None, requires_rubrics=True, needs_judge=True, needs_vertex=False,
        user_sim_compatible=True, default_threshold=1.0, ci_tier="nightly",
        extra_fields=("judge_model_options", "rubrics"),
        rubric_type="TOOL_USE_QUALITY",
        when="The place for restraint guarantees: rubrics phrased as \"does NOT call X "
             "when Y\". Trajectory matching proves what happened; this proves what "
             "must never happen. Keep the threshold at 1.0.",
    ),
    "rubric_based_multi_turn_trajectory_quality_v1": _spec(
        name="rubric_based_multi_turn_trajectory_quality_v1",
        summary="LLM judge scores the whole multi-turn trajectory against your rubrics.",
        criterion_class="RubricsBasedCriterion",
        value_range=(0.0, 1.0),
        reference_based=False, reference_kind=None, requires_rubrics=True, needs_judge=True, needs_vertex=False,
        user_sim_compatible=True, default_threshold=0.8, ci_tier="nightly",
        extra_fields=("judge_model_options", "rubrics"),
        rubric_type="TRAJECTORY_QUALITY",
        when="Long conversations where the shape of the whole exchange matters more than "
             "any single call.",
    ),
    "hallucinations_v1": _spec(
        name="hallucinations_v1",
        summary="Splits the response into sentences and labels each as supported, unsupported, "
                "contradictory, disputed or not applicable against the context the agent saw.",
        criterion_class="HallucinationsCriterion",
        value_range=(0.0, 1.0),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=True, needs_vertex=False,
        user_sim_compatible=True, default_threshold=0.8, ci_tier="nightly",
        extra_fields=("judge_model_options", "evaluate_intermediate_nl_responses"),
        when="Groundedness, which is not correctness: a true fact the tools never returned "
             "still fails. Set evaluate_intermediate_nl_responses to true or only the last "
             "message is checked.",
    ),
    "safety_v1": _spec(
        name="safety_v1",
        summary="Vertex AI safety rating of the agent's response.",
        criterion_class="BaseCriterion",
        value_range=(0.0, 1.0),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=True, needs_vertex=True,
        user_sim_compatible=True, default_threshold=0.8, ci_tier="nightly",
        when="Any agent that talks to end users. Needs the Vertex evaluation service.",
    ),
    "per_turn_user_simulator_quality_v1": _spec(
        name="per_turn_user_simulator_quality_v1",
        summary="LLM judge checks whether the simulated user itself behaved as instructed.",
        criterion_class="LlmBackedUserSimulatorCriterion",
        value_range=(0.0, 1.0),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=True, needs_vertex=False,
        user_sim_compatible=True, default_threshold=0.8, ci_tier="nightly",
        extra_fields=("judge_model_options", "stop_signal"),
        when="Validates the test harness rather than the agent. Run it when a simulated "
             "suite starts failing and you suspect the simulator.",
    ),
    "multi_turn_task_success_v1": _spec(
        name="multi_turn_task_success_v1",
        summary="Did the conversation achieve the user's goal?",
        criterion_class="BaseCriterion",
        value_range=(0.0, 1.0),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=True, needs_vertex=True,
        user_sim_compatible=True, default_threshold=0.8, ci_tier="nightly",
        when="Outcome-level scoring for simulated conversations.",
    ),
    "multi_turn_trajectory_quality_v1": _spec(
        name="multi_turn_trajectory_quality_v1",
        summary="Overall quality of the conversation's trajectory.",
        criterion_class="BaseCriterion",
        value_range=(0.0, 1.0),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=True, needs_vertex=True,
        user_sim_compatible=True, default_threshold=0.8, ci_tier="nightly",
        when="Simulated conversations where you have no expected trajectory to compare with.",
    ),
    "multi_turn_tool_use_quality_v1": _spec(
        name="multi_turn_tool_use_quality_v1",
        summary="Quality of the function calls made during a conversation.",
        criterion_class="BaseCriterion",
        value_range=(0.0, 1.0),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=True, needs_vertex=True,
        user_sim_compatible=True, default_threshold=0.8, ci_tier="nightly",
        when="Simulated conversations, when tool use matters but is not scripted.",
    ),
    # ----------------------------------------------------------------------
    # Efficiency metrics, ADK 2.10. Reference-free, informational, always on:
    # `get_eval_metrics_from_config` appends all four to every run, and each
    # evaluator rejects a threshold rather than ignoring it. Descriptions are
    # the `MetricInfo.description` strings from
    # `evaluation/metric_info_providers.py`, condensed to one sentence.
    # ----------------------------------------------------------------------
    "tool_call_count_v1": _spec(
        name="tool_call_count_v1",
        summary="Counts the tool (function) calls the agent made per invocation, averaged "
                "across the eval case.",
        criterion_class="(none)",
        value_range=(0.0, INFINITY),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=False, needs_vertex=False,
        user_sim_compatible=True, default_threshold=0.0, ci_tier="pr",
        informational=True, since="2.10", requires_threshold=False,
        when="Read it, do not gate on it: it reports the value for tracking and does not "
             "pass or fail the eval case. Nothing to configure - ADK 2.10 reports it on "
             "every run.",
    ),
    "inference_call_count_v1": _spec(
        name="inference_call_count_v1",
        summary="Counts the inference (model) calls the agent made per invocation, averaged "
                "across the eval case.",
        criterion_class="(none)",
        value_range=(0.0, INFINITY),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=False, needs_vertex=False,
        user_sim_compatible=True, default_threshold=0.0, ci_tier="pr",
        informational=True, since="2.10", requires_threshold=False,
        when="A proxy for attempts or reasoning steps. Read alongside token usage it "
             "separates the two ways a turn gets expensive: more calls, or a larger "
             "context per call.",
    ),
    "token_usage_v1": _spec(
        name="token_usage_v1",
        summary="Sums the tokens consumed by the model across all model calls in an "
                "invocation, averaged across the eval case.",
        criterion_class="(none)",
        value_range=(0.0, INFINITY),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=False, needs_vertex=False,
        user_sim_compatible=True, default_threshold=0.0, ci_tier="pr",
        informational=True, since="2.10", requires_threshold=False,
        extra_fields=(),
        when="The score is the total; the breakdown (total, input/prompt/cached/tool-use, "
             "output/candidates/reasoning) arrives in the result's "
             "`details.token_usage_details`, using ADK telemetry's definitions.",
    ),
    "invocation_duration_v1": _spec(
        name="invocation_duration_v1",
        summary="Reports the wall-clock seconds an invocation took, averaged across the "
                "eval case.",
        criterion_class="(none)",
        value_range=(0.0, INFINITY),
        reference_based=False, reference_kind=None, requires_rubrics=False, needs_judge=False, needs_vertex=False,
        user_sim_compatible=True, default_threshold=0.0, ci_tier="pr",
        informational=True, since="2.10", requires_threshold=False,
        when="Noisier than the counts, since it moves with model-server load and network: "
             "read it as an indication rather than a regression signal. An invocation this "
             "eval run did not itself produce reports no value.",
    ),
}

METRIC_NAMES = tuple(METRIC_CATALOG)
REFERENCE_BASED = tuple(n for n, s in METRIC_CATALOG.items() if s.reference_based)
JUDGE_METRICS = tuple(n for n, s in METRIC_CATALOG.items() if s.needs_judge)
VERTEX_METRICS = tuple(n for n, s in METRIC_CATALOG.items() if s.needs_vertex)
RUBRIC_METRICS = tuple(n for n, s in METRIC_CATALOG.items() if s.requires_rubrics)
#: Deterministic metrics that can actually gate: the informational ones are
#: deterministic too, but they never fail a case, so they are not a gate.
DETERMINISTIC_METRICS = tuple(
    n for n, s in METRIC_CATALOG.items() if not s.needs_judge and not s.informational
)
INFORMATIONAL_METRICS = tuple(n for n, s in METRIC_CATALOG.items() if s.informational)


def get(name: str) -> MetricSpec | None:
    return METRIC_CATALOG.get(name)


def is_known(name: str) -> bool:
    return name in METRIC_CATALOG


def suggest(name: str) -> str | None:
    """Closest catalog entry for a misspelled metric name."""
    import difflib

    matches = difflib.get_close_matches(name, METRIC_NAMES, n=1, cutoff=0.6)
    return matches[0] if matches else None


def cost_tier(metric_names: Iterable[str]) -> str:
    """The most expensive tier among these metrics."""
    tier = "free"
    for name in metric_names:
        spec = METRIC_CATALOG.get(name)
        if spec is None:
            continue
        if spec.needs_vertex:
            return "vertex"
        if spec.needs_judge:
            tier = "judge"
    return tier


def response_reference_metrics(metric_names: Iterable[str]) -> list[str]:
    """Metrics that compare against the expected `final_response` text.

    `tool_trajectory_avg_score` is reference-based too, but its reference is the
    expected tool calls, so it does not need a written answer.
    """
    return [
        n for n in metric_names
        if (s := METRIC_CATALOG.get(n)) and s.reference_kind == "response"
    ]


def incompatible_with_user_sim(metric_names: Iterable[str]) -> list[str]:
    """Metrics this plugin refuses to run alongside user simulation.

    Not a schema rule: ADK will accept the combination and then produce
    ``NOT_EVALUATED`` (or raise) for every case, because there is no fixed
    reference to compare a simulated conversation against. Refusing up front is
    the difference between a clear error and a green run that scored nothing.
    """
    return [n for n in metric_names if (s := METRIC_CATALOG.get(n)) and not s.user_sim_compatible]


def informational_metrics(metric_names: Iterable[str] | None = None) -> list[str]:
    """The always-on, never-failing efficiency metrics.

    Called with no argument it lists all of them; called with the metric names
    from a config it lists the ones that config wrongly tries to configure.
    """
    if metric_names is None:
        return list(INFORMATIONAL_METRICS)
    return [n for n in metric_names if (s := METRIC_CATALOG.get(n)) and s.informational]


def pr_tier_violations(metric_names: Iterable[str]) -> list[str]:
    """Metrics too slow or too costly to gate every pull request."""
    return [n for n in metric_names if (s := METRIC_CATALOG.get(n)) and s.ci_tier != "pr"]


def range_label(spec: MetricSpec) -> str:
    """How wide the metric's values are, in words a table can hold."""
    low, high = spec.value_range
    if high == INFINITY:
        return f"{low:g}+ (unbounded, lower is better)"
    return f"{low:g}-{high:g}"


def table_rows() -> list[dict[str, str]]:
    """Catalog as display rows, for `metric_info.py` and the references."""
    rows = []
    for spec in METRIC_CATALOG.values():
        rows.append({
            "metric": spec.name,
            "cost": spec.cost,
            "reference": "yes" if spec.reference_based else "no",
            "rubrics": "required" if spec.requires_rubrics else "no",
            "user_sim": "yes" if spec.user_sim_compatible else "no",
            "range": range_label(spec),
            "default_threshold": "n/a" if spec.informational else f"{spec.default_threshold:g}",
            "ci_tier": "informational" if spec.informational else spec.ci_tier,
        })
    return rows
