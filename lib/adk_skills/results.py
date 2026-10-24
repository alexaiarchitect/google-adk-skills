"""Read ADK eval result files.

`adk eval` and the programmatic services write one file per run to
``<agent_dir>/.adk/eval_history/<app>_<eval_set>_<unix_ts>.evalset_result.json``.
It is a serialized `EvalSetResult`, dumped by field name, so the keys on disk are
snake_case (this parser accepts camelCase too, since the same models are served
over the dev-server API with aliases).

Four statuses, and the last two both need care:

===============  =  ==================================================
PASSED           1  every evaluated metric met its threshold
FAILED           2  at least one metric scored below its threshold
NOT_EVALUATED    3  the metric never produced a score
INFORMATIONAL    4  ADK 2.10: the metric reported a value and no verdict
===============  =  ==================================================

`NOT_EVALUATED` is not a regression: it means the judge could not be reached, no
reference was available, or a rubric list was empty. `adk eval`'s own summary
counts it in the failed column, which is why this parser keeps it separate - the
fix for a NOT_EVALUATED is a configuration fix, not an agent fix.

`INFORMATIONAL` (ADK 2.10) is not a regression either, and not a skipped metric:
it is the status of the four always-on efficiency metrics
(``tool_call_count_v1``, ``inference_call_count_v1``, ``token_usage_v1``,
``invocation_duration_v1``), which report a value and never pass or fail a case.
Their entries carry no `threshold` and no `criterion`; `token_usage_v1` adds a
`details.token_usage_details` breakdown. They must be kept out of pass/fail
counts and out of the NOT_EVALUATED bucket, so this parser tags them and the
analyzers report them in their own table.

Two shapes from ADK 2.13+ (absent through 2.12.0) are read when present and
never required:

* `invocation_duration_v1` gains `details.duration_details`
  (`DurationDetails`: `total_seconds`, `model_seconds`, `tool_seconds`,
  `framework_seconds`, each optional; the three parts add up to the total).
* Vertex-served rubric metrics surface the service's verdicts as ordinary
  `rubric_scores`, plus one entry with `rubric_id` "explanation" that carries
  the service's rationale. That entry is an explanation, not a rubric, so it is
  kept out of the verdicts and reported as the metric's rationale.

One shape from ADK main after 2.13.0 (unreleased, 7f3517e43) is tolerated:
with `multi_run_config.num_runs` above 1, one result file holds one
`EvalCaseResult` per run of each case, each carrying a 0-based
`repetition_index`. Released versions write neither. When an `eval_id`
repeats within one file, each entry is labelled "run k/N" (`repeat`, `repeats`)
and `RunResult.case_groups()` returns the runs of each case together, so a
comparison can take the per-case mean instead of letting the last run win.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

RESULT_SUFFIX = ".evalset_result.json"
EVAL_HISTORY_DIR = Path(".adk") / "eval_history"

STATUS_NAMES = {1: "PASSED", 2: "FAILED", 3: "NOT_EVALUATED", 4: "INFORMATIONAL"}

#: `EvalStatus.INFORMATIONAL`, as the integer a result file carries.
INFORMATIONAL_STATUS = "INFORMATIONAL"

#: The per-type token counts `token_usage_v1` reports under
#: `details.token_usage_details`, nested: total = input (prompt, of which cached,
#: plus tool use) + output (candidates plus reasoning).
TOKEN_USAGE_FIELDS = (
    "total_tokens", "input_tokens", "prompt_tokens", "cached_tokens",
    "tool_use_tokens", "output_tokens", "candidates_tokens", "reasoning_tokens",
)

#: `DurationDetails` (ADK 2.13+): where an invocation's seconds went.
DURATION_FIELDS = ("total_seconds", "model_seconds", "tool_seconds", "framework_seconds")

#: The rubric id Vertex rubric verdicts use for the service's rationale.
EXPLANATION_RUBRIC_ID = "explanation"


def cache_hit_ratio(usage: dict[str, Any] | None) -> float | None:
    """`cached_tokens / prompt_tokens` from a `token_usage_details` breakdown.

    `cached_tokens` is nested inside `prompt_tokens`, so this is the share of
    the prompt the context cache served. None when either count is unreported
    (ADK 2.9 has no efficiency metrics at all; a model may not report cached
    tokens) or the prompt is empty: unknown is not zero.
    """
    if not usage:
        return None
    cached = usage.get("cached_tokens")
    prompt = usage.get("prompt_tokens")
    if not isinstance(cached, (int, float)) or not isinstance(prompt, (int, float)) or prompt <= 0:
        return None
    return round(float(cached) / float(prompt), 4)


#: `<app_name>_<eval_set_id>_<timestamp>`. Both ids may contain underscores, so
#: only the trailing timestamp can be split off reliably; the eval set id comes
#: from the file contents.
_RESULT_NAME = re.compile(r"^(?P<stem>.+)_(?P<ts>\d+(?:\.\d+)?)\.evalset_result\.json$")


def status_name(value: Any) -> str:
    """Normalize a status that may arrive as 1, "PASSED" or "EvalStatus.PASSED"."""
    if isinstance(value, bool):
        return "UNKNOWN"
    if isinstance(value, int):
        return STATUS_NAMES.get(value, f"UNKNOWN({value})")
    text = str(value or "").strip()
    if not text:
        return "UNKNOWN"
    if text.isdigit():
        return STATUS_NAMES.get(int(text), f"UNKNOWN({text})")
    return text.rsplit(".", 1)[-1].upper()


def _get(node: dict[str, Any], *names: str, default: Any = None) -> Any:
    """Field lookup that tolerates snake_case or camelCase keys."""
    for name in names:
        if name in node:
            return node[name]
        camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), name)
        if camel in node:
            return node[camel]
    return default


@dataclass
class MetricResult:
    name: str
    score: float | None
    threshold: float | None
    status: str
    rubric_scores: list[dict[str, Any]] = field(default_factory=list)
    #: `token_usage_v1` only: the eight nested per-type counts.
    token_usage: dict[str, float] = field(default_factory=dict)
    #: `invocation_duration_v1` on ADK 2.13+: model / tool /
    #: framework seconds. Empty when the run did not report the split.
    duration: dict[str, float] = field(default_factory=dict)
    #: A Vertex rubric metric's overall rationale (the "explanation" entry).
    explanation: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    @property
    def informational(self) -> bool:
        """Reported for tracking, never a verdict (ADK 2.10 efficiency metrics).

        Recognized from the status the file carries, not from the metric name, so
        an efficiency metric ADK adds later is handled without a catalog update.
        """
        return self.status == INFORMATIONAL_STATUS

    @property
    def margin(self) -> float | None:
        if self.score is None or self.threshold is None:
            return None
        return round(self.score - self.threshold, 4)

    def failed_rubrics(self) -> list[str]:
        """Rubric ids the judge answered no to (score below 0.5)."""
        out: list[str] = []
        for entry in self.rubric_scores:
            score = entry.get("score")
            if score is not None and score < 0.5:
                out.append(str(entry.get("rubric_id", "?")))
        return out

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["margin"] = self.margin
        data["failed_rubrics"] = self.failed_rubrics()
        data["informational"] = self.informational
        return data


@dataclass
class InvocationResult:
    index: int
    user_text: str = ""
    actual_tools: list[dict[str, Any]] = field(default_factory=list)
    expected_tools: list[dict[str, Any]] = field(default_factory=list)
    actual_text: str = ""
    expected_text: str = ""
    metrics: list[MetricResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "user_text": self.user_text,
            "actual_tools": [t.get("name") for t in self.actual_tools],
            "expected_tools": [t.get("name") for t in self.expected_tools],
            "metrics": [m.to_dict() for m in self.metrics if not m.informational],
            "efficiency": [m.to_dict() for m in self.metrics if m.informational],
        }


@dataclass
class CaseResult:
    eval_id: str
    status: str
    eval_set_id: str = ""
    session_id: str = ""
    metrics: list[MetricResult] = field(default_factory=list)
    invocations: list[InvocationResult] = field(default_factory=list)
    #: ADK main after 2.13.0 (multi-run): the 0-based run index, else None.
    repetition_index: int | None = None
    #: Set when the same `eval_id` appears more than once in one file:
    #: this entry is run `repeat` of `repeats` (1-based).
    repeat: int | None = None
    repeats: int | None = None

    @property
    def run_label(self) -> str | None:
        """"run k/N" for a repeated case, None for a case run once."""
        if self.repeat is None or self.repeats is None:
            return None
        return f"run {self.repeat}/{self.repeats}"

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    @property
    def not_evaluated(self) -> bool:
        return self.status == "NOT_EVALUATED"

    def metric(self, name: str) -> MetricResult | None:
        for result in self.metrics:
            if result.name == name:
                return result
        return None

    def failing_metrics(self) -> list[MetricResult]:
        return [m for m in self.metrics if m.status == "FAILED"]

    def unevaluated_metrics(self) -> list[MetricResult]:
        return [m for m in self.metrics if m.status == "NOT_EVALUATED"]

    def gate_metrics(self) -> list[MetricResult]:
        """The metrics that can actually pass or fail this case."""
        return [m for m in self.metrics if not m.informational]

    def efficiency_metrics(self) -> list[MetricResult]:
        """The informational efficiency metrics, in the order ADK reported them."""
        return [m for m in self.metrics if m.informational]

    def token_usage(self) -> dict[str, float]:
        """`token_usage_v1`'s per-type breakdown for this case, or `{}` when the
        run did not report one (ADK 2.9, or a case that never ran)."""
        metric = self.metric("token_usage_v1")
        return dict(metric.token_usage) if metric else {}

    def duration_split(self) -> dict[str, float]:
        """Model / tool / framework seconds for this case, or `{}` on every
        version through 2.12.0, which reports only the total."""
        metric = self.metric("invocation_duration_v1")
        return dict(metric.duration) if metric else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_id": self.eval_id,
            "status": self.status,
            "eval_set_id": self.eval_set_id,
            "session_id": self.session_id,
            **({"repetition_index": self.repetition_index}
               if self.repetition_index is not None else {}),
            **({"run_label": self.run_label} if self.run_label else {}),
            "metrics": [m.to_dict() for m in self.gate_metrics()],
            "efficiency": [m.to_dict() for m in self.efficiency_metrics()],
            "invocations": [i.to_dict() for i in self.invocations],
        }


@dataclass
class RunResult:
    path: str
    eval_set_id: str = ""
    app_name: str | None = None
    timestamp: float | None = None
    cases: list[CaseResult] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case.status == "PASSED")

    @property
    def failed(self) -> int:
        return sum(1 for case in self.cases if case.status == "FAILED")

    @property
    def not_evaluated(self) -> int:
        return sum(1 for case in self.cases if case.status == "NOT_EVALUATED")

    @property
    def is_clean(self) -> bool:
        """Every case passed, nothing skipped."""
        return bool(self.cases) and self.failed == 0 and self.not_evaluated == 0

    def efficiency(self) -> dict[str, dict[str, Any]]:
        """Mean of each informational metric across the cases that reported it.

        ADK already averages per invocation within a case; this averages those
        case values, so the figure is per invocation, not per run.
        """
        totals: dict[str, list[float]] = {}
        usage: dict[str, dict[str, float]] = {}
        for case in self.cases:
            for metric in case.efficiency_metrics():
                if metric.score is not None:
                    totals.setdefault(metric.name, []).append(float(metric.score))
                if metric.token_usage:
                    usage[metric.name] = metric.token_usage
        out: dict[str, dict[str, Any]] = {}
        for name, scores in totals.items():
            out[name] = {
                "mean": round(sum(scores) / len(scores), 4),
                "cases": len(scores),
                "min": min(scores),
                "max": max(scores),
            }
            if name in usage:
                out[name]["token_usage_details"] = usage[name]
        split = self.duration_means()
        if split and "invocation_duration_v1" in out:
            out["invocation_duration_v1"]["duration_details"] = split
        return out

    def duration_means(self) -> dict[str, float]:
        """Mean model / tool / framework seconds across the cases that reported
        the split; `{}` when none did (unknown, not zero)."""
        values: dict[str, list[float]] = {}
        for case in self.cases:
            for key, value in case.duration_split().items():
                values.setdefault(key, []).append(value)
        return {key: round(sum(v) / len(v), 4) for key, v in values.items() if v}

    def token_means(self) -> dict[str, Any]:
        """Mean prompt / cached / output / total tokens per case, and the run's
        cache-hit ratio (summed cached over summed prompt).

        Each mean covers only the cases that reported that count, and is None
        when none did: a 2.9 run, or a model that reports no cached tokens,
        leaves the figure unknown rather than zero.
        """
        keys = ("prompt_tokens", "cached_tokens", "output_tokens", "total_tokens")
        values: dict[str, list[float]] = {key: [] for key in keys}
        cached_sum = prompt_sum = 0.0
        both = 0
        for case in self.cases:
            usage = case.token_usage()
            for key in keys:
                if key in usage:
                    values[key].append(usage[key])
            if "cached_tokens" in usage and usage.get("prompt_tokens"):
                cached_sum += usage["cached_tokens"]
                prompt_sum += usage["prompt_tokens"]
                both += 1
        out: dict[str, Any] = {
            key: (round(sum(found) / len(found), 4) if found else None)
            for key, found in values.items()
        }
        out["cache_hit_ratio"] = round(cached_sum / prompt_sum, 4) if both and prompt_sum else None
        return out

    def case_groups(self) -> dict[str, list[CaseResult]]:
        """Every case's entries by `eval_id`, in file order: one entry for a
        case run once, N for a multi-run file (ADK main after 2.13.0)."""
        groups: dict[str, list[CaseResult]] = {}
        for case in self.cases:
            groups.setdefault(case.eval_id, []).append(case)
        return groups

    @property
    def repeated(self) -> bool:
        return any(len(group) > 1 for group in self.case_groups().values())

    def case(self, eval_id: str) -> CaseResult | None:
        for case in self.cases:
            if case.eval_id == eval_id:
                return case
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "eval_set_id": self.eval_set_id,
            "app_name": self.app_name,
            "timestamp": self.timestamp,
            "cases": len(self.cases),
            "passed": self.passed,
            "failed": self.failed,
            "not_evaluated": self.not_evaluated,
            "efficiency": self.efficiency(),
            "cache_hit_ratio": self.token_means()["cache_hit_ratio"],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "case_results": [c.to_dict() for c in self.cases]}


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def find_result_files(target: Path | str) -> list[str]:
    """Result files under an agent directory, a project, or a history directory."""
    root = Path(target)
    if root.is_file() and root.name.endswith(RESULT_SUFFIX):
        return [str(root)]
    if not root.is_dir():
        return []
    if root.name == "eval_history":
        return sorted(str(p) for p in root.glob(f"*{RESULT_SUFFIX}"))
    found = [
        str(p) for p in root.rglob(f"*{RESULT_SUFFIX}")
        if ".venv" not in p.parts and "node_modules" not in p.parts
    ]
    return sorted(found)


def parse_result_name(path: Path | str, *, eval_set_id: str | None = None) -> dict[str, Any]:
    """Pull what the file name can tell us: the stem and the timestamp.

    `<app_name>_<eval_set_id>_<timestamp>` cannot be split on underscores alone,
    since both ids may contain them. Pass `eval_set_id` (read from the file) to
    recover the app name.
    """
    target = Path(path)
    match = _RESULT_NAME.match(target.name)
    if not match:
        return {}
    stem = match.group("stem")
    info: dict[str, Any] = {"stem": stem, "timestamp": float(match.group("ts"))}
    if eval_set_id and stem.endswith(f"_{eval_set_id}"):
        info["app_name"] = stem[: -(len(eval_set_id) + 1)]
        info["eval_set_id"] = eval_set_id
    elif eval_set_id:
        info["eval_set_id"] = eval_set_id
    return info


def result_identity(path: Path | str) -> dict[str, Any]:
    """App name, eval set id and timestamp for a result file, reading the file
    for the authoritative eval set id."""
    eval_set_id = None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            eval_set_id = _get(raw, "eval_set_id")
    except (OSError, ValueError, UnicodeDecodeError):
        pass
    info = parse_result_name(path, eval_set_id=eval_set_id)
    info.setdefault("eval_set_id", eval_set_id or info.get("stem", ""))
    return info


def _content_text(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts") or []
    return "".join(
        part.get("text", "") for part in parts if isinstance(part, dict) and part.get("text")
    )


def _tool_calls(invocation: Any) -> list[dict[str, Any]]:
    """Expected/actual tool calls, from either intermediate_data shape."""
    if not isinstance(invocation, dict):
        return []
    data = _get(invocation, "intermediate_data")
    calls: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for tool in _get(data, "tool_uses", default=[]) or []:
            if isinstance(tool, dict):
                calls.append({"name": tool.get("name"), "args": tool.get("args") or {}})
        # The InvocationEvents shape keeps calls inside events instead.
        for event in _get(data, "invocation_events", default=[]) or []:
            if not isinstance(event, dict):
                continue
            content = event.get("content") or {}
            for part in (content.get("parts") or []) if isinstance(content, dict) else []:
                call = part.get("function_call") or part.get("functionCall") if isinstance(part, dict) else None
                if isinstance(call, dict):
                    calls.append({"name": call.get("name"), "args": call.get("args") or {}})
    return calls


def _metric_results(entries: Iterable[Any]) -> list[MetricResult]:
    results: list[MetricResult] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        criterion = _get(entry, "criterion") or {}
        threshold = _get(entry, "threshold")
        if threshold is None and isinstance(criterion, dict):
            threshold = criterion.get("threshold")
        details = _get(entry, "details") or {}
        token_usage: dict[str, float] = {}
        rubric_scores = []
        duration: dict[str, float] = {}
        explanation: str | None = None
        if isinstance(details, dict):
            split = _get(details, "duration_details") or {}
            if isinstance(split, dict):
                for key in DURATION_FIELDS:
                    value = _get(split, key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        duration[key] = float(value)
            usage = _get(details, "token_usage_details") or {}
            if isinstance(usage, dict):
                for key in TOKEN_USAGE_FIELDS:
                    value = _get(usage, key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        token_usage[key] = float(value)
            for score in _get(details, "rubric_scores", default=[]) or []:
                if isinstance(score, dict) and _get(score, "rubric_id") == EXPLANATION_RUBRIC_ID:
                    explanation = score.get("rationale") or explanation
                    continue
                if isinstance(score, dict):
                    rubric_scores.append({
                        "rubric_id": _get(score, "rubric_id"),
                        "score": score.get("score"),
                        "rationale": score.get("rationale"),
                    })
        results.append(MetricResult(
            name=str(_get(entry, "metric_name", default="?")),
            score=_get(entry, "score"),
            threshold=threshold,
            status=status_name(_get(entry, "eval_status")),
            rubric_scores=rubric_scores,
            token_usage=token_usage,
            duration=duration,
            explanation=explanation,
        ))
    return results


def parse_result(path: Path | str) -> RunResult:
    """Read one `*.evalset_result.json`."""
    target = Path(path)
    meta = parse_result_name(target)
    run = RunResult(path=str(target), timestamp=meta.get("timestamp"))
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        run.error = "file not found"
        return run
    except json.JSONDecodeError as exc:
        run.error = f"invalid JSON: {exc}"
        return run
    except (OSError, UnicodeDecodeError) as exc:
        run.error = str(exc)
        return run

    # Older files were written as a JSON-encoded string containing JSON.
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            run.error = "unrecognized result format"
            return run
    if not isinstance(raw, dict):
        run.error = "result file must contain an object"
        return run

    run.eval_set_id = str(_get(raw, "eval_set_id", default="") or "")
    named = parse_result_name(target, eval_set_id=run.eval_set_id or None)
    run.app_name = named.get("app_name")
    if _get(raw, "creation_timestamp"):
        run.timestamp = _get(raw, "creation_timestamp")

    for entry in _get(raw, "eval_case_results", default=[]) or []:
        if not isinstance(entry, dict):
            continue
        case = CaseResult(
            eval_id=str(_get(entry, "eval_id", default="?")),
            status=status_name(_get(entry, "final_eval_status")),
            eval_set_id=str(_get(entry, "eval_set_id", default=run.eval_set_id) or ""),
            session_id=str(_get(entry, "session_id", default="") or ""),
            metrics=_metric_results(_get(entry, "overall_eval_metric_results", default=[])),
        )
        for index, per in enumerate(_get(entry, "eval_metric_result_per_invocation", default=[]) or []):
            if not isinstance(per, dict):
                continue
            actual = _get(per, "actual_invocation") or {}
            expected = _get(per, "expected_invocation") or {}
            case.invocations.append(InvocationResult(
                index=index,
                user_text=_content_text(_get(expected, "user_content") or _get(actual, "user_content")),
                actual_tools=_tool_calls(actual),
                expected_tools=_tool_calls(expected),
                actual_text=_content_text(_get(actual, "final_response")),
                expected_text=_content_text(_get(expected, "final_response")),
                metrics=_metric_results(_get(per, "eval_metric_results", default=[])),
            ))
        repetition = _get(entry, "repetition_index")
        if isinstance(repetition, int) and not isinstance(repetition, bool):
            case.repetition_index = repetition
        run.cases.append(case)
    _label_repeats(run)
    return run


def _label_repeats(run: RunResult) -> None:
    """Number the entries of every `eval_id` that repeats within the file,
    by `repetition_index` when present, else in file order."""
    for group in run.case_groups().values():
        if len(group) < 2:
            continue
        ordered = sorted(
            enumerate(group),
            key=lambda pair: (pair[1].repetition_index is None,
                              pair[1].repetition_index or 0, pair[0]),
        )
        for position, (_, case) in enumerate(ordered, start=1):
            case.repeat = position
            case.repeats = len(group)


def latest_per_evalset(paths: Iterable[Path | str]) -> dict[str, str]:
    """Newest result file for each eval set id."""
    newest: dict[str, tuple[float, str]] = {}
    for path in paths:
        meta = result_identity(path)
        eval_set = meta.get("eval_set_id") or Path(path).stem
        stamp = meta.get("timestamp")
        if stamp is None:
            try:
                stamp = Path(path).stat().st_mtime
            except OSError:
                stamp = 0.0
        current = newest.get(eval_set)
        if current is None or stamp > current[0]:
            newest[eval_set] = (float(stamp), str(path))
    return {key: value[1] for key, value in sorted(newest.items())}


def group_runs(paths: Iterable[Path | str], *, eval_set_id: str | None = None) -> list[RunResult]:
    """Parse several result files, oldest first, optionally for one eval set."""
    runs: list[RunResult] = []
    for path in paths:
        if eval_set_id and result_identity(path).get("eval_set_id") != eval_set_id:
            continue
        runs.append(parse_result(path))
    runs.sort(key=lambda r: (r.timestamp or 0.0, r.path))
    return runs
