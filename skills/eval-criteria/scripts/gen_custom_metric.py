#!/usr/bin/env python3
"""Scaffold a custom metric that ADK will actually call correctly.

The built-in catalog covers trajectory, response match, rubric judgement,
groundedness and safety. What it cannot express is a *business invariant* -
"the claim id submitted must be the one the user gave", "a refusal must name the
rule it hit" - and those are exactly the checks worth automating, because they
are deterministic and a judge would score them inconsistently.

Writing one by hand fails in the same two ways every time:

* **The threshold reads as `None`.** `_CustomMetricEvaluator` copies the
  `EvalMetric` and blanks `eval_metric.threshold` before calling the function,
  so the number has to come from `eval_metric.criterion.threshold`. Code that
  reads the obvious attribute compares against `None` and either raises or
  passes everything.
* **The return shape is wrong.** ADK expects an `EvaluationResult` carrying one
  `PerInvocationResult` per invocation, each with its own `eval_status`. A bare
  float, or a result with no per-invocation detail, leaves every report unable
  to say *which* turn failed.

A third failure is in the config: the function is wired through
`custom_metrics.<name>.code_config.name`, and the value range through
`custom_metrics.<name>.metric_info.metric_value_info.interval`. A
`custom_function_path` key under `criteria`, or a `metric` key with
`min_value`/`max_value`, validates and is silently ignored, and the run then
fails with "not found in registry".

This writes a module that gets all three right, plus the config snippet that
registers it (also kept in the module as `CONFIG_SNIPPET`).

`--kind` picks the body:

* `sync` / `async` - a skeleton with a TODO rule, for a business invariant.
* `trajectory-precision` - the share of the tool calls the agent made that the
  expected trajectory also makes. Low precision means extra or wrong calls.
* `trajectory-recall` - the share of the expected tool calls the agent made.
  Low recall means skipped steps.

Both trajectory kinds match calls as a multiset by name (`--with-args`: by name
and arguments), ignoring order. They grade partial credit where the built-in
`tool_trajectory_avg_score` (EXACT, IN_ORDER, ANY_ORDER) is all or nothing per
invocation. An invocation with no expected trajectory (a user-simulation case)
is NOT_EVALUATED, not failed.

    gen_custom_metric.py --name my_metric --out path/to/metric.py
                         [--kind sync|async|trajectory-precision|trajectory-recall]
                         [--with-args] [--threshold F] [--with-metric-info]
                         [--description TEXT] [--dry-run] [--force] [--json]

Exit codes: 0 written (or planned), 1 refused (the file exists), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE  # noqa: E402
from adk_skills.templating import WritePlan, render  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "templates" / "custom_metric.py.tmpl"

METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

KINDS = ("sync", "async", "trajectory-precision", "trajectory-recall")

SYNC_BODY = '''def {function}(
    eval_metric: EvalMetric,
    actual_invocations: list[Invocation],
    expected_invocations: Optional[list[Invocation]] = None,
    conversation_scenario: Optional[ConversationScenario] = None,
) -> EvaluationResult:
    """Score one eval case.

    ADK calls this with the four positional arguments below, in this order, and
    accepts either a plain return or an awaitable.

    Args:
        eval_metric: The metric as configured. Read the threshold from
            `eval_metric.criterion.threshold`, never `eval_metric.threshold`.
        actual_invocations: What the agent actually did, one entry per turn.
        expected_invocations: The golden trace, when the case has one. `None`
            for a scenario (user-simulation) case, which has no fixed reference.
        conversation_scenario: The scenario, for simulated cases.

    Returns:
        An `EvaluationResult` whose `per_invocation_results` explain the overall
        score turn by turn.
    """
    del conversation_scenario          # not used by this metric
    threshold = _threshold(eval_metric)
    expected = list(expected_invocations or [])

    per_invocation: list[PerInvocationResult] = []
    for index, actual in enumerate(actual_invocations):
        reference = expected[index] if index < len(expected) else None
        score = _score_one(actual, reference)
        per_invocation.append(
            PerInvocationResult(
                actual_invocation=actual,
                expected_invocation=reference,
                score=score,
                eval_status=_status(score, threshold),
            )
        )

    scored = [r.score for r in per_invocation if r.score is not None]
    if not scored:
        # Nothing to score is not a failure - it is an unscored case.
        return EvaluationResult(
            overall_score=None,
            overall_eval_status=EvalStatus.NOT_EVALUATED,
            per_invocation_results=per_invocation,
        )

    overall = sum(scored) / len(scored)
    return EvaluationResult(
        overall_score=overall,
        overall_eval_status=_status(overall, threshold),
        per_invocation_results=per_invocation,
    )'''

ASYNC_BODY = SYNC_BODY.replace("def {function}(", "async def {function}(", 1).replace(
    '''    ADK calls this with the four positional arguments below, in this order, and
    accepts either a plain return or an awaitable.''',
    '''    ADK calls this with the four positional arguments below, in this order, and
    awaits the result when it is awaitable - so an async metric needs no
    registration of its own. Use this variant when the check has to await
    something (a datastore lookup, an internal API); keep it deterministic.''',
)

TODO_SCORE = '''def _score_one(
    actual: Invocation,
    expected: Optional[Invocation],
) -> Optional[float]:
    """Score a single invocation, between 0.0 and 1.0; None when it cannot be
    scored (it is then NOT_EVALUATED, not failed).

    TODO: the real rule. This is the whole reason for a custom metric - it
    should be something deterministic that the built-in catalog cannot express,
    such as a business invariant ("a submitted claim id must match the one the
    user gave") or a domain check a judge would score inconsistently.

    Keep it deterministic. A custom metric that calls a model is a judge metric
    with none of the calibration machinery, and it will drift.
    """
    del expected                       # TODO: compare against the golden trace
    tool_names = []
    intermediate = getattr(actual, "intermediate_data", None)
    for tool_use in getattr(intermediate, "tool_uses", None) or []:
        name = getattr(tool_use, "name", None)
        if name:
            tool_names.append(name)
    return 1.0 if tool_names else 0.0'''

TRAJECTORY_SCORE = '''#: Compare arguments as well as names. With False, `lookup_policy(category=
#: "meals")` matches an expected `lookup_policy(category="travel")`.
WITH_ARGS = {with_args}


def _calls(invocation: Invocation) -> list[tuple[str, str]]:
    """(name, canonical arguments) for each tool call, in call order.

    `get_all_tool_calls` reads both shapes ADK stores: `IntermediateData`
    (a hand-written or captured case) and `InvocationEvents` (a live run).
    """
    calls = []
    for call in get_all_tool_calls(getattr(invocation, "intermediate_data", None)):
        args = json.dumps(call.args or {{}}, sort_keys=True, default=str) if WITH_ARGS else ""
        calls.append((call.name or "", args))
    return calls


def precision_recall(
    actual_calls: list[tuple[str, str]],
    expected_calls: list[tuple[str, str]],
) -> tuple[float, float]:
    """(precision, recall) of the actual calls against the expected ones.

    Calls are matched as a multiset, ignoring order: each expected call can be
    matched once. Precision is matched / actual (1.0 when neither side has a
    call, 0.0 when the agent made none but some were expected); recall is
    matched / expected (1.0 when nothing was expected).
    """
    remaining = Counter(expected_calls)
    matched = 0
    for call in actual_calls:
        if remaining[call] > 0:
            remaining[call] -= 1
            matched += 1
    if actual_calls:
        precision = matched / len(actual_calls)
    else:
        precision = 1.0 if not expected_calls else 0.0
    recall = matched / len(expected_calls) if expected_calls else 1.0
    return precision, recall


def _score_one(
    actual: Invocation,
    expected: Optional[Invocation],
) -> Optional[float]:
    """Trajectory {measure} for one invocation; None without a reference."""
    if expected is None:
        return None
    precision, recall = precision_recall(_calls(actual), _calls(expected))
    return {measure}'''

TRAJECTORY_STDLIB_IMPORTS = "import json\nfrom collections import Counter\n"
TRAJECTORY_ADK_IMPORTS = "from google.adk.evaluation.eval_case import get_all_tool_calls\n"

TRAJECTORY_DESCRIPTIONS = {
    "precision": "Trajectory precision: the share of the tool calls the agent made that the "
                 "expected trajectory also makes, matched by {what}, ignoring order.",
    "recall": "Trajectory recall: the share of the expected tool calls the agent made, "
              "matched by {what}, ignoring order.",
}

METRIC_INFO_BLOCK = '''def {function}_metric_info() -> MetricInfo:
    """The same `MetricInfo` the config entry declares, as an object.

    ADK reads `metric_info` from `custom_metrics.{metric}` in the eval config,
    not from this module; this is for code that registers the metric itself
    (`MetricEvaluatorRegistry.register_evaluator`) or a test that wants to
    check the declared range.
    """
    return MetricInfo.model_validate(CONFIG_SNIPPET["custom_metrics"]["{metric}"]["metric_info"])'''

METRIC_INFO_IMPORTS = '''from google.adk.evaluation.eval_metrics import MetricInfo
'''


def config_snippet(name: str, dotted: str, threshold: float, description: str,
                   with_metric_info: bool) -> dict:
    """The eval config entry ADK actually reads for a custom metric."""
    entry: dict = {"code_config": {"name": dotted}, "description": description}
    if with_metric_info:
        entry["metric_info"] = {
            "metric_name": name,
            "description": description,
            "metric_value_info": {"interval": {"min_value": 0.0, "max_value": 1.0}},
        }
    return {"criteria": {name: threshold}, "custom_metrics": {name: entry}}


def dotted_path(out: Path, layout_root: Path | None) -> str:
    """The import path ADK will resolve, given where the module was written."""
    out = out.resolve()
    parts = [out.stem]
    current = out.parent
    # Walk up while the directory is a package, which is what makes the dots real.
    while (current / "__init__.py").is_file():
        parts.append(current.name)
        current = current.parent
    if layout_root and len(parts) == 1:
        return out.stem
    return ".".join(reversed(parts))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="metric name, snake_case")
    parser.add_argument("--out", required=True, help="module to write")
    parser.add_argument("--kind", choices=KINDS, default="sync",
                        help="sync/async: a skeleton with a TODO rule; trajectory-precision / "
                             "trajectory-recall: a complete tool-call precision or recall metric")
    parser.add_argument("--with-args", action="store_true",
                        help="trajectory kinds: match tool calls by name and arguments")
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--description", default="",
                        help="one line; shown wherever metrics are listed")
    parser.add_argument("--with-metric-info", action="store_true",
                        help="also declare `metric_info` (interval 0.0-1.0) in the config "
                             "entry, and emit a MetricInfo helper that reads it")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not METRIC_NAME_RE.match(args.name):
        print(f"error: metric name {args.name!r} should be snake_case "
              "(lower-case letters, digits and underscores, starting with a letter)",
              file=sys.stderr)
        return EXIT_USAGE
    if not 0.0 <= args.threshold <= 1.0:
        print(f"error: threshold {args.threshold} is outside 0.0-1.0; the generated "
              "metric scores on that interval", file=sys.stderr)
        return EXIT_USAGE

    if args.with_args and not args.kind.startswith("trajectory-"):
        print("error: --with-args only applies to --kind trajectory-precision or "
              "trajectory-recall", file=sys.stderr)
        return EXIT_USAGE

    out = Path(args.out)
    if out.suffix != ".py":
        print(f"error: --out must be a .py module, not {out.name}", file=sys.stderr)
        return EXIT_USAGE

    function = args.name
    trajectory = args.kind.startswith("trajectory-")
    measure = args.kind.split("-", 1)[1] if trajectory else None
    if args.description:
        description = args.description
    elif trajectory:
        description = TRAJECTORY_DESCRIPTIONS[measure].format(
            what="name and arguments" if args.with_args else "tool name")
    else:
        description = f"Custom metric {args.name}."
    layout = detect_layout(out.parent if out.parent.exists() else Path("."))
    path = dotted_path(out, Path(layout.project_root) if layout.project_root else None)
    dotted = f"{path}.{function}"

    body = (ASYNC_BODY if args.kind == "async" else SYNC_BODY).format(function=function)
    score = (TRAJECTORY_SCORE.format(with_args=args.with_args, measure=measure)
             if trajectory else TODO_SCORE)
    info = (
        METRIC_INFO_BLOCK.format(function=function, metric=args.name)
        if args.with_metric_info else
        "# Optional: re-run with --with-metric-info to declare the value range\n"
        "# (`metric_info.metric_value_info.interval`) in the config entry."
    )
    snippet = config_snippet(args.name, dotted, args.threshold, description,
                             args.with_metric_info)
    snippet_json = json.dumps(snippet, indent=4)

    content = render(TEMPLATE.read_text(encoding="utf-8"), {
        "METRIC_NAME": args.name,
        "METRIC_DESCRIPTION": description,
        "THRESHOLD": repr(args.threshold),
        "CONFIG_SNIPPET_TEXT": "\n".join("    " + line for line in snippet_json.splitlines()),
        "CONFIG_SNIPPET_PY": snippet_json,
        "STDLIB_IMPORTS": TRAJECTORY_STDLIB_IMPORTS if trajectory else "",
        "ADK_IMPORTS": TRAJECTORY_ADK_IMPORTS if trajectory else "",
        "METRIC_INFO_IMPORTS": METRIC_INFO_IMPORTS if args.with_metric_info else "",
        "SCORE_FUNCTION": score,
        "FUNCTION_BODY": body,
        "METRIC_INFO": info,
    })

    plan = WritePlan()
    plan.add(out, content, force=args.force,
             reason=f"{args.kind} custom metric {args.name}")

    next_steps = ([
        "pick the threshold from a baseline run: partial credit means 1.0 is rarely "
        "the right bar for a multi-step trajectory",
    ] if trajectory else [
        "replace the TODO in `_score_one`: as written it only checks that a tool "
        "was called, which proves nothing",
    ]) + [
        "merge the snippet above into your eval config (both `criteria` and "
        "`custom_metrics`), then check it with validate_config.py (CFG055 resolves "
        "the dotted path)",
        "keep it deterministic: a custom metric that calls a model is an "
        "uncalibrated judge",
    ]
    if path == out.stem:
        next_steps.insert(0, (
            f"{out.name} is not inside a package, so `{path}` only imports when the "
            "evaluation runs from its directory; move it into the agent package for "
            "a stable dotted path"
        ))

    written = [] if args.dry_run else plan.apply()
    refused = [item for item in plan.items if item.action == "skip"]

    payload = {
        "metric": args.name,
        "kind": args.kind,
        "with_args": args.with_args,
        "threshold": args.threshold,
        "out": str(out),
        "dotted_path": dotted,
        "config_snippet": snippet,
        "write_plan": plan.to_dict(),
        "written": written,
        "next_steps": next_steps,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"{args.name}  ({args.kind}, threshold {args.threshold})")
        print(f"dotted path: {payload['dotted_path']}")
        print()
        print("Add to the eval config:")
        print(json.dumps(snippet, indent=2))
        print()
        print(plan.to_md())
        if args.dry_run:
            print()
            print("--dry-run: nothing was written")
        for item in written:
            print(f"wrote {item}")
        print()
        print("Next:")
        for step in next_steps:
            print(f"  - {step}")

    if refused and not args.dry_run:
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
