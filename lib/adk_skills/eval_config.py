"""Validate an ADK eval config (`test_config.json` / `eval_config.json`).

Shape, in snake_case (camelCase aliases also parse)::

    {
      "criteria": {
        "tool_trajectory_avg_score": {"threshold": 1.0, "match_type": "IN_ORDER"},
        "response_match_score": 0.0,
        "hallucinations_v1": {
          "threshold": 0.8,
          "judge_model_options": {"judge_model": "gemini-flash-latest", "num_samples": 3},
          "evaluate_intermediate_nl_responses": true
        }
      },
      "custom_metrics": {"my_metric": {"code_config": {"name": "pkg.module.fn"}}},
      "user_simulator_config": {"type": "llm_backed", "model": "gemini-flash-latest"},
      "live_model_config": {"timeout_seconds": 300}
    }

A criterion value is either a bare threshold float or an object. Two traps this
validator exists to catch:

* ADK's criterion models allow extra keys, so a misspelled field such as
  ``match_types`` is accepted and silently ignored - the eval still runs, just
  not the way you meant.
* The config must be JSON. ``EvalConfig.model_validate_json`` is what reads it,
  so a YAML file cannot be passed to ``--config_file_path``.
* The four ADK 2.10 efficiency metrics (``tool_call_count_v1``,
  ``inference_call_count_v1``, ``token_usage_v1``, ``invocation_duration_v1``)
  must not appear in ``criteria`` at all. ``EvalConfig.model_validate`` accepts
  them on both 2.9 and 2.10 - a threshold coerces silently - and the run then
  raises when the evaluator is looked up. They are reported automatically on
  every 2.10 run, so there is nothing to configure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import DEPRECATED_MODELS
from .env import BackendInfo, classify_model_id, version_at_least
from .evalset import key_style, normalize_keys
from .metrics import (
    INFORMATIONAL_METRICS,
    JUDGE_DEFAULTS,
    MATCH_TYPES,
    METRIC_CATALOG,
    cost_tier,
    incompatible_with_user_sim,
    informational_metrics,
    is_known,
    suggest,
)
from .report import GapReport

CONFIG_KEYS = frozenset({"criteria", "custom_metrics", "user_simulator_config", "live_model_config"})

BASE_CRITERION_FIELDS = frozenset({"threshold", "include_intermediate_responses_in_final"})
CRITERION_FIELDS: dict[str, frozenset[str]] = {
    "ToolTrajectoryCriterion": BASE_CRITERION_FIELDS | {"match_type", "ignore_args"},
    "LlmAsAJudgeCriterion": BASE_CRITERION_FIELDS | {"judge_model_options"},
    "RubricsBasedCriterion": BASE_CRITERION_FIELDS | {"judge_model_options", "rubrics"},
    "HallucinationsCriterion": BASE_CRITERION_FIELDS
    | {"judge_model_options", "evaluate_intermediate_nl_responses"},
    "LlmBackedUserSimulatorCriterion": BASE_CRITERION_FIELDS
    | {"judge_model_options", "stop_signal"},
    # `BaseCriterion` itself has no judge options. Criteria are validated as
    # bare `BaseCriterion` first and only the evaluator re-validates the
    # subclass, which is why a stray `judge_model_options` here raises nothing
    # and does nothing.
    "BaseCriterion": BASE_CRITERION_FIELDS,
    # Metrics scored by the Vertex evaluation service take no judge options.
    "VertexCriterion": BASE_CRITERION_FIELDS,
    # The efficiency metrics validate into no criterion type at all: the
    # evaluator requires `threshold is None and criterion is None` and raises
    # otherwise, so every field here is a field too many.
    "(none)": frozenset(),
}

JUDGE_OPTION_FIELDS = frozenset({"judge_model", "judge_model_config", "num_samples", "parallelism_limit"})

_SRC_CONFIG = "adk-python: evaluation/eval_config.py"
_SRC_METRICS = "adk-python: evaluation/eval_metrics.py"
_SRC_CRITERIA_DOC = "adk-docs: docs/evaluate/criteria.md"
_DOTTED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")


@dataclass
class LoadedConfig:
    path: str
    doc: dict[str, Any] | None
    raw: Any = None
    styles: set[str] = field(default_factory=set)
    error: str | None = None
    fmt: str = "eval_config"     # eval_config | foreign | invalid

    @property
    def ok(self) -> bool:
        return self.doc is not None and self.fmt == "eval_config"

    def metric_names(self) -> list[str]:
        if not self.doc:
            return []
        criteria = self.doc.get("criteria")
        return list(criteria) if isinstance(criteria, dict) else []

    def trajectory_match_type(self) -> str | None:
        """How `tool_trajectory_avg_score` compares trajectories, or None if
        the metric is not configured. A bare number means ADK's default, EXACT."""
        criteria = (self.doc or {}).get("criteria")
        if not isinstance(criteria, dict) or "tool_trajectory_avg_score" not in criteria:
            return None
        value = criteria["tool_trajectory_avg_score"]
        if isinstance(value, dict):
            return str(value.get("match_type") or "EXACT").upper()
        return "EXACT"

    def has_reference_metrics(self) -> bool:
        """Is any *active* criterion comparing against the expected response text?

        A reference-based metric with a threshold of 0 always passes, which is
        the usual way to run a trajectory-first suite, so it does not make an
        empty `final_response` a problem.
        """
        criteria = (self.doc or {}).get("criteria")
        if not isinstance(criteria, dict):
            return False
        for name, value in criteria.items():
            spec = METRIC_CATALOG.get(name)
            if not spec or spec.reference_kind != "response":
                continue
            threshold = value if isinstance(value, (int, float)) and not isinstance(value, bool) else (
                value.get("threshold") if isinstance(value, dict) else None
            )
            if threshold is None or threshold > 0:
                return True
        return False

    def cost(self) -> str:
        return cost_tier(self.metric_names())

    def uses_user_simulation(self) -> bool:
        return bool(self.doc and self.doc.get("user_simulator_config"))


def load_config(path: Path | str) -> LoadedConfig:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LoadedConfig(str(target), None, error="file not found", fmt="invalid")
    except (OSError, UnicodeDecodeError) as exc:
        return LoadedConfig(str(target), None, error=str(exc), fmt="invalid")

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        # A YAML config is a real thing in some project templates, but it is not
        # something `adk eval` can read.
        if target.suffix in (".yaml", ".yml"):
            return LoadedConfig(
                str(target), None, error="not JSON", fmt="foreign",
                raw=text,
            )
        return LoadedConfig(str(target), None, error=f"invalid JSON: {exc}", fmt="invalid")

    if not isinstance(raw, dict):
        return LoadedConfig(str(target), None, raw=raw, error="top level must be an object", fmt="invalid")

    styles = key_style(raw)
    doc = normalize_keys(raw)
    fmt = "eval_config"
    if "criteria" not in doc and ("metrics_to_run" in doc or "metrics" in doc):
        fmt = "foreign"
    return LoadedConfig(str(target), doc, raw=raw, styles=styles, fmt=fmt)


def validate_config(
    loaded: LoadedConfig,
    *,
    report: GapReport | None = None,
    backend: BackendInfo | None = None,
    agent_dir: Path | str | None = None,
    known_tools: set[str] | None = None,
    adk_version: str | None = None,
) -> GapReport:
    """Check a config against the metric catalog, the criterion schemas and the
    backend that has to serve it.

    `adk_version` is the version of the runner that will execute this config,
    when one was probed. It only changes the informational note about the
    always-on efficiency metrics: every error here holds on both 2.9 and 2.10.
    """
    report = report or GapReport(subject=loaded.path, skill="eval-criteria")
    path = loaded.path

    if loaded.fmt == "invalid":
        report.add("CFG001", "error", "config", loaded.error or "unreadable config",
                   path=path, source=_SRC_CONFIG)
        return report

    if loaded.fmt == "foreign":
        # A warning, not an error: an `eval_config.yaml` beside an agent is
        # usually some other tool's file and is nobody's mistake. It only
        # matters if someone expects `adk eval` to read it, so say so and move
        # on rather than failing the run that found it.
        report.add(
            "CFG002", "warn", "config",
            "this is not an ADK eval config: `adk eval` reads the file with "
            "EvalConfig.model_validate_json, so it must be JSON with a `criteria` object",
            path=path,
            fix="if this was meant to configure evals, write an eval_config.json with a "
                "`criteria` map; otherwise leave it for whatever tool consumes it",
            source=_SRC_CONFIG,
        )
        return report

    doc = loaded.doc or {}

    if "camel" in loaded.styles and "snake" in loaded.styles:
        report.add("CFG003", "warn", "config",
                   "config mixes camelCase and snake_case keys; both parse, but pick one",
                   path=path, fix="normalize to snake_case, as in the ADK docs",
                   source="adk-python: evaluation/common.py")

    unknown_top = sorted(set(doc) - CONFIG_KEYS)
    if unknown_top:
        report.add("CFG004", "warn", "config",
                   f"unrecognized top-level key(s): {', '.join(unknown_top)}",
                   path=path, fix="EvalConfig accepts extra keys and ignores them",
                   source=_SRC_CONFIG)

    criteria = doc.get("criteria")
    if criteria is None:
        report.add("CFG005", "error", "config",
                   "no `criteria`: without it the eval falls back to "
                   "tool_trajectory_avg_score 1.0 and response_match_score 0.8",
                   path=path, fix="declare the metrics you mean to gate on", source=_SRC_CONFIG)
        return report
    if not isinstance(criteria, dict):
        report.add("CFG006", "error", "config", "`criteria` must be an object keyed by metric name",
                   path=path, source=_SRC_CONFIG)
        return report
    if not criteria:
        report.add("CFG007", "error", "config", "`criteria` is empty", path=path,
                   fix="add at least one metric", source=_SRC_CONFIG)
        return report

    custom_metrics = doc.get("custom_metrics") or {}
    for name, value in criteria.items():
        _validate_criterion(
            name, value, report, path,
            custom_metrics=custom_metrics,
            backend=backend,
            known_tools=known_tools,
        )

    _note_efficiency_metrics(criteria, report, path, adk_version=adk_version)

    _validate_custom_metrics(custom_metrics, criteria, report, path, agent_dir=agent_dir)

    if loaded.uses_user_simulation():
        clashing = incompatible_with_user_sim(criteria)
        if clashing:
            report.add(
                "CFG030", "error", "config",
                "user simulation is configured together with reference-based metric(s): "
                f"{', '.join(clashing)}",
                path=path,
                fix="a simulated conversation has no fixed reference; use rubric-based, "
                    "hallucinations_v1, safety_v1 or the multi_turn_* metrics instead",
                source=_SRC_CRITERIA_DOC,
            )
        _validate_user_simulator(doc["user_simulator_config"], report, path)

    return report


def _note_efficiency_metrics(
    criteria: dict[str, Any],
    report: GapReport,
    path: str,
    *,
    adk_version: str | None,
) -> None:
    """One note per config: 2.10 reports the four efficiency metrics anyway.

    Only worth saying when the run will actually be on 2.10. An older runner
    reports nothing extra, so the note would be noise; an unknown runner gets it,
    because ``ADK_TARGET`` is what the plugin installs.
    """
    if not criteria:
        return
    if adk_version is not None and not version_at_least(adk_version, "2.10"):
        return
    already = set(informational_metrics(criteria))
    report.add(
        "CFG071", "info", "config",
        "ADK 2.10 also reports four informational efficiency metrics alongside these "
        "criteria (lower is better, never a pass or a fail): "
        + ", ".join(INFORMATIONAL_METRICS),
        path=path, locator="criteria",
        fix="nothing to configure; read them with /adk:eval-analyze"
            + (" and take the named one out of `criteria`" if already else ""),
        source="adk-python: evaluation/eval_config.py (get_eval_metrics_from_config)",
    )


def _validate_criterion(
    name: str,
    value: Any,
    report: GapReport,
    path: str,
    *,
    custom_metrics: dict[str, Any],
    backend: BackendInfo | None,
    known_tools: set[str] | None,
) -> None:
    locator = f"criteria.{name}"
    spec = METRIC_CATALOG.get(name)
    is_custom = name in custom_metrics

    if spec is None and not is_custom:
        hint = suggest(name)
        report.add(
            "CFG010", "error", "config",
            f"unknown metric {name!r}" + (f"; did you mean {hint!r}?" if hint else ""),
            path=path, locator=locator,
            fix="use a catalog metric, or register it under `custom_metrics`",
            source=_SRC_METRICS,
        )
        return

    if spec is not None and spec.informational:
        # Measured on both wheels: the config layer accepts the name and the run
        # then raises. There is no way to name it harmlessly - `null` is refused
        # by pydantic, and any threshold is refused by the evaluator.
        report.add(
            "CFG070", "error", "config",
            f"{name} is an informational efficiency metric and cannot be configured: on "
            "ADK 2.10 the run raises ValueError (\"does not support a threshold: it "
            "reports a value and never passes or fails\") and on 2.9 the metric is not "
            "registered at all (NotFoundError)",
            path=path, locator=locator,
            fix="remove it from `criteria` entirely; ADK 2.10 reports all four efficiency "
                "metrics automatically on every eval, and /adk:eval-analyze shows them in "
                "their own table",
            source="adk-python: evaluation/_efficiency_evaluators.py",
        )
        return

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        threshold: float | None = float(value)
        criterion: dict[str, Any] = {}
    elif isinstance(value, dict):
        criterion = value
        raw_threshold = value.get("threshold")
        if raw_threshold is None:
            report.add("CFG011", "error", "config", "criterion object is missing `threshold`",
                       path=path, locator=locator,
                       fix="thresholds are required on a criterion object", source=_SRC_METRICS)
            threshold = None
        elif not isinstance(raw_threshold, (int, float)) or isinstance(raw_threshold, bool):
            report.add("CFG012", "error", "config", "`threshold` must be a number",
                       path=path, locator=locator, source=_SRC_METRICS)
            threshold = None
        else:
            threshold = float(raw_threshold)
    else:
        report.add("CFG013", "error", "config",
                   "criterion must be a threshold number or an object",
                   path=path, locator=locator, source=_SRC_CONFIG)
        return

    if spec is not None and threshold is not None:
        low, high = spec.value_range
        if not low <= threshold <= high:
            report.add(
                "CFG014", "error", "config",
                f"threshold {threshold:g} is outside this metric's range {low:g}-{high:g}",
                path=path, locator=locator,
                fix=f"pick a value in {low:g}-{high:g} (a threshold below the minimum can never fail)",
                source="adk-python: evaluation/metric_info_providers.py",
            )
        elif threshold == low and low == 0.0:
            report.add("CFG015", "info", "config",
                       "threshold 0 disables this metric as a gate; it will always pass",
                       path=path, locator=locator,
                       fix="intentional for trajectory-first suites; remove the metric if you "
                           "did not mean to score it")

    if spec is not None and criterion:
        key = "VertexCriterion" if spec.needs_vertex else spec.criterion_class
        allowed = CRITERION_FIELDS.get(key, BASE_CRITERION_FIELDS)
        unknown = sorted(set(criterion) - allowed)
        if unknown:
            report.add(
                "CFG016", "warn", "config",
                f"field(s) this criterion ignores: {', '.join(unknown)}",
                path=path, locator=locator,
                fix=f"{spec.criterion_class} accepts {', '.join(sorted(allowed))}; extra keys "
                    "are accepted and silently dropped, so a typo changes nothing",
                source=_SRC_METRICS,
            )

    if name == "tool_trajectory_avg_score":
        match_type = criterion.get("match_type")
        if match_type is not None:
            normalized = str(match_type).upper().replace("-", "_").replace(" ", "_")
            if normalized not in MATCH_TYPES:
                report.add("CFG017", "error", "config",
                           f"`match_type` {match_type!r} is not one of {', '.join(MATCH_TYPES)}",
                           path=path, locator=locator, source=_SRC_METRICS)
        if criterion.get("ignore_args"):
            report.add(
                "CFG018", "warn", "config",
                "`ignore_args` is on, so argument mistakes cannot fail this metric",
                path=path, locator=locator,
                fix="a right tool called with the wrong id still passes; keep args checked in "
                    "the suite that gates releases",
                source=_SRC_METRICS,
            )

    if spec is not None and spec.requires_rubrics:
        rubrics = criterion.get("rubrics")
        if not rubrics:
            report.add(
                "CFG020", "warn", "config",
                "no rubrics on the criterion: the effective rubric list must be non-empty, "
                "so every eval case has to supply type-matching rubrics or evaluation raises",
                path=path, locator=locator,
                fix=f"add rubrics here, or per case with type {spec.rubric_type!r}",
                source=_SRC_CRITERIA_DOC,
            )
        else:
            _validate_rubric_list(rubrics, report, path, locator, known_tools=known_tools)

    if spec is not None and spec.needs_judge and not spec.needs_vertex:
        # Vertex-served metrics are scored by the evaluation service, which
        # chooses its own model, so judge options do not apply to them.
        _validate_judge_options(criterion.get("judge_model_options"), report, path, locator)

    if spec is not None and spec.needs_vertex and backend is not None and not backend.vertex_ready:
        report.add(
            "CFG021", "error", "config",
            f"{name} calls the Vertex AI Gen AI Evaluation Service, which needs either "
            "GOOGLE_API_KEY, or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION with ADC",
            path=path, locator=locator,
            fix="set one of the two in the agent's .env, or drop this metric. The facade "
                "takes GOOGLE_API_KEY first and only then falls back to project + location, "
                "so an API key alone is enough to run it",
            source="adk-python: evaluation/vertex_ai_eval_facade.py",
        )
    elif spec is not None and spec.needs_judge and backend is not None and not backend.judge_ready:
        report.add(
            "CFG022", "error", "config",
            f"{name} needs a judge model, but no backend is configured: it needs either "
            "GOOGLE_API_KEY, or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION with ADC",
            path=path, locator=locator,
            fix="configure the agent's .env before running this suite",
            source=_SRC_CRITERIA_DOC,
        )

    if name == "hallucinations_v1" and not criterion.get("evaluate_intermediate_nl_responses"):
        report.add(
            "CFG023", "warn", "config",
            "only the final response is checked for groundedness",
            path=path, locator=locator,
            fix="set `evaluate_intermediate_nl_responses` to true to audit every "
                "natural-language message the agent emits, not just the last one",
            source=_SRC_CRITERIA_DOC,
        )


def _validate_judge_options(options: Any, report: GapReport, path: str, locator: str) -> None:
    where = f"{locator}.judge_model_options"
    if options is None:
        report.add(
            "CFG024", "warn", "config",
            f"no `judge_model_options`: the judge defaults to "
            f"{JUDGE_DEFAULTS['judge_model']} with num_samples={JUDGE_DEFAULTS['num_samples']}",
            path=path, locator=locator,
            fix="name the judge model explicitly; the library default is a model this "
                "plugin's policy treats as deprecated, and 5 samples per invocation is "
                "five times the cost of 1",
            source=_SRC_METRICS,
        )
        return
    if not isinstance(options, dict):
        report.add("CFG025", "error", "config", "`judge_model_options` must be an object",
                   path=path, locator=where, source=_SRC_METRICS)
        return

    unknown = sorted(set(options) - JUDGE_OPTION_FIELDS)
    if unknown:
        report.add("CFG026", "warn", "config",
                   f"unrecognized judge option(s): {', '.join(unknown)}",
                   path=path, locator=where, source=_SRC_METRICS)

    judge_model = options.get("judge_model")
    if not judge_model:
        report.add("CFG027", "warn", "config",
                   f"`judge_model` unset, so it defaults to {JUDGE_DEFAULTS['judge_model']}",
                   path=path, locator=where, fix="set it explicitly", source=_SRC_METRICS)
    else:
        grade = classify_model_id(str(judge_model))
        if grade == "deprecated":
            report.add("CFG028", "warn", "config",
                       f"judge model {judge_model!r} is deprecated",
                       path=path, locator=where,
                       fix="use an alias id such as gemini-flash-latest; the id still "
                           "resolves today, so this is a clock running down rather than "
                           "a break",
                       source="adk-recipes/AGENTS.md")
        elif grade == "preview":
            report.add("CFG029", "warn", "config",
                       f"judge model {judge_model!r} is a preview id and will be withdrawn",
                       path=path, locator=where,
                       fix="prefer an alias id such as gemini-flash-latest")

    samples = options.get("num_samples")
    if samples is not None:
        if not isinstance(samples, int) or isinstance(samples, bool) or samples < 1:
            report.add("CFG031", "error", "config", "`num_samples` must be an integer >= 1",
                       path=path, locator=where, source=_SRC_METRICS)
        elif samples == 1:
            report.add("CFG032", "info", "config",
                       "num_samples=1: one judge call per invocation, so a single unlucky "
                       "sample decides the verdict",
                       path=path, locator=where,
                       fix="3 gives a majority vote for three times the cost")
        elif samples > 5:
            report.add("CFG033", "warn", "config",
                       f"num_samples={samples} multiplies judge cost by {samples}",
                       path=path, locator=where, fix="3 is usually enough for a stable vote")


def _validate_rubric_list(
    rubrics: Any, report: GapReport, path: str, locator: str, *, known_tools: set[str] | None
) -> None:
    if not isinstance(rubrics, list):
        report.add("CFG040", "error", "config", "`rubrics` must be an array",
                   path=path, locator=locator, source=_SRC_CRITERIA_DOC)
        return
    seen: set[str] = set()
    for index, rubric in enumerate(rubrics):
        where = f"{locator}.rubrics[{index}]"
        if not isinstance(rubric, dict):
            report.add("CFG041", "error", "config", "rubric must be an object",
                       path=path, locator=where, source=_SRC_CRITERIA_DOC)
            continue
        rubric_id = rubric.get("rubric_id")
        if not rubric_id:
            report.add("CFG042", "error", "config", "rubric is missing `rubric_id`",
                       path=path, locator=where, source=_SRC_CRITERIA_DOC)
        elif rubric_id in seen:
            report.add("CFG043", "error", "config", f"duplicate `rubric_id` {rubric_id!r}",
                       path=path, locator=where,
                       fix="per-rubric scores are reported by id; duplicates are ambiguous",
                       source=_SRC_CRITERIA_DOC)
        else:
            seen.add(str(rubric_id))

        content = rubric.get("rubric_content")
        if not isinstance(content, dict) or not content.get("text_property"):
            report.add("CFG044", "error", "config",
                       "rubric needs `rubric_content.text_property`",
                       path=path, locator=where, source=_SRC_CRITERIA_DOC)
            continue

        from .rubrics import lint_rubric_text  # local import: avoids a cycle

        for finding in lint_rubric_text(
            str(content["text_property"]), rubric_id=str(rubric_id or index),
            known_tools=known_tools, path=path, locator=where,
        ):
            report.findings.append(finding)


def _validate_custom_metrics(
    custom_metrics: Any,
    criteria: dict[str, Any],
    report: GapReport,
    path: str,
    *,
    agent_dir: Path | str | None,
) -> None:
    if not custom_metrics:
        return
    if not isinstance(custom_metrics, dict):
        report.add("CFG050", "error", "config",
                   "`custom_metrics` must be an object keyed by metric name",
                   path=path, source=_SRC_CONFIG)
        return

    for name, config in custom_metrics.items():
        locator = f"custom_metrics.{name}"
        if name not in criteria:
            report.add(
                "CFG051", "warn", "config",
                f"custom metric {name!r} is defined but not listed in `criteria`, so it never runs",
                path=path, locator=locator,
                fix=f'add "{name}": <threshold> to criteria', source=_SRC_CONFIG,
            )
        if not isinstance(config, dict):
            report.add("CFG052", "error", "config", "custom metric config must be an object",
                       path=path, locator=locator, source=_SRC_CONFIG)
            continue
        unknown = sorted(set(config) - CUSTOM_METRIC_KEYS)
        if unknown:
            hint = ("; the metric's value range goes under `metric_info.metric_value_info."
                    "interval` (min_value / max_value)") if "metric" in unknown else ""
            report.add(
                "CFG057", "warn", "config",
                f"unrecognized key(s) on custom metric {name!r}: {', '.join(unknown)}; "
                "CustomMetricConfig ignores them" + hint,
                path=path, locator=locator,
                fix="the keys are `code_config`, `metric_info` and `description`",
                source="adk-python: evaluation/eval_config.py (CustomMetricConfig)",
            )
        info = config.get("metric_info")
        if info is not None:
            problem = _metric_info_problem(info)
            if problem:
                report.add(
                    "CFG058", "error", "config",
                    f"`metric_info` on custom metric {name!r} {problem}, so EvalConfig "
                    "fails to load",
                    path=path, locator=f"{locator}.metric_info",
                    fix='{"metric_name": "<name>", "metric_value_info": {"interval": '
                        '{"min_value": 0.0, "max_value": 1.0}}}; ADK overwrites '
                        "`metric_name` with the key, but the field is still required",
                    source="adk-python: evaluation/eval_metrics.py (MetricInfo)",
                )
        code_config = config.get("code_config")
        if not isinstance(code_config, dict) or not code_config.get("name"):
            report.add("CFG053", "error", "config",
                       "custom metric needs `code_config.name`: the dotted import path of the "
                       "evaluation function",
                       path=path, locator=locator, source=_SRC_CONFIG)
            continue
        dotted = str(code_config["name"])
        if not _DOTTED.match(dotted):
            report.add("CFG054", "error", "config",
                       f"`code_config.name` {dotted!r} is not a dotted import path "
                       "(module.function)",
                       path=path, locator=locator, source=_SRC_CONFIG)
        elif agent_dir and not _dotted_path_exists(dotted, agent_dir):
            report.add(
                "CFG055", "warn", "config",
                f"could not find {dotted!r} under the agent project",
                path=path, locator=locator,
                fix="the path must be importable from the working directory where the eval "
                    "runs; check the module name and that the package is on sys.path",
                source=_SRC_CONFIG,
            )
        report.add(
            "CFG056", "info", "config",
            f"custom metric {name!r} must read its threshold from "
            "`eval_metric.criterion.threshold`",
            path=path, locator=locator,
            fix="ADK blanks `eval_metric.threshold` before calling a custom function",
            source="adk-python: evaluation/custom_metric_evaluator.py",
        )


#: `CustomMetricConfig` fields (2.9.2 and 2.11.0 alike; extra keys are ignored,
#: not rejected, so a misspelt one silently does nothing).
CUSTOM_METRIC_KEYS = frozenset({"code_config", "metric_info", "description"})


def _metric_info_problem(info: Any) -> str | None:
    """What `MetricInfo.model_validate` would reject, or None.

    Required: `metric_name` and `metric_value_info`; an `interval` inside the
    latter needs numeric `min_value` and `max_value`.
    """
    if not isinstance(info, dict):
        return "must be an object"
    if not info.get("metric_name"):
        return "has no `metric_name`"
    value_info = info.get("metric_value_info")
    if not isinstance(value_info, dict):
        return "has no `metric_value_info`"
    interval = value_info.get("interval")
    if interval is None:
        return None
    if not isinstance(interval, dict):
        return "has a `metric_value_info.interval` that is not an object"
    missing = [key for key in ("min_value", "max_value")
               if not isinstance(interval.get(key), (int, float)) or isinstance(interval.get(key), bool)]
    if missing:
        return f"has an interval without a numeric {' and '.join(missing)}"
    if interval["min_value"] > interval["max_value"]:
        return "has an interval whose min_value is above its max_value"
    return None


def _dotted_path_exists(dotted: str, agent_dir: Path | str) -> bool:
    """Best-effort check that a custom metric's module is present on disk."""
    module_path = dotted.rsplit(".", 1)[0]
    parts = module_path.split(".")
    roots = {Path(agent_dir).resolve(), Path(agent_dir).resolve().parent}
    for root in roots:
        candidate = root.joinpath(*parts)
        if candidate.with_suffix(".py").is_file() or (candidate / "__init__.py").is_file():
            return True
    return False


def _validate_user_simulator(config: Any, report: GapReport, path: str) -> None:
    locator = "user_simulator_config"
    if not isinstance(config, dict):
        report.add("CFG060", "error", "config", "`user_simulator_config` must be an object",
                   path=path, locator=locator,
                   source="adk-python: evaluation/simulation/llm_backed_user_simulator.py")
        return
    sim_type = config.get("type")
    if sim_type is None:
        report.add("CFG061", "info", "config",
                   "`type` unset; ADK back-fills \"llm_backed\"",
                   path=path, locator=locator, source=_SRC_CONFIG)
    elif sim_type not in ("llm_backed", "llm_audio"):
        report.add("CFG062", "error", "config",
                   f"`type` {sim_type!r} must be \"llm_backed\" or \"llm_audio\"",
                   path=path, locator=locator, source=_SRC_CONFIG)

    model = config.get("model")
    if not model:
        report.add("CFG063", "warn", "config",
                   f"no simulator `model`; it defaults to {JUDGE_DEFAULTS['judge_model']}",
                   path=path, locator=locator, fix="name the model explicitly")
    elif classify_model_id(str(model)) == "deprecated":
        report.add("CFG064", "warn", "config", f"simulator model {model!r} is deprecated",
                   path=path, locator=locator,
                   fix="use an alias id such as gemini-flash-latest",
                   source="adk-recipes/AGENTS.md")

    instructions = config.get("custom_instructions")
    if isinstance(instructions, str) and instructions:
        for token in ("stop_signal", "conversation_plan", "conversation_history"):
            if f"{{{{ {token} }}}}" not in instructions and f"{{{{{token}}}}}" not in instructions:
                report.add(
                    "CFG065", "error", "config",
                    f"`custom_instructions` drops the required {{{{ {token} }}}} placeholder",
                    path=path, locator=locator,
                    fix="a custom simulator prompt must keep stop_signal, conversation_plan "
                        "and conversation_history",
                    source="adk-docs: docs/evaluate/user-sim.md",
                )

    if config.get("max_allowed_invocations") in (None, -1):
        report.add("CFG066", "warn", "config",
                   "no `max_allowed_invocations` cap: a simulated conversation can run long "
                   "and every turn costs model calls",
                   path=path, locator=locator, fix="set a small cap while iterating")


def deprecated_judge_models(loaded: LoadedConfig) -> list[str]:
    """Judge or simulator models in this config that policy rejects."""
    found: list[str] = []
    doc = loaded.doc or {}
    for criterion in (doc.get("criteria") or {}).values():
        if isinstance(criterion, dict):
            options = criterion.get("judge_model_options") or {}
            model = options.get("judge_model") if isinstance(options, dict) else None
            if model in DEPRECATED_MODELS:
                found.append(str(model))
    simulator = doc.get("user_simulator_config") or {}
    if isinstance(simulator, dict) and simulator.get("model") in DEPRECATED_MODELS:
        found.append(str(simulator["model"]))
    return sorted(set(found))
