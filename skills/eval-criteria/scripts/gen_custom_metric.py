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

This writes a module that gets both right, in a sync or async variant, plus the
config snippet that registers it.

    gen_custom_metric.py --name my_metric --out path/to/metric.py
                         [--kind sync|async] [--threshold F] [--with-metric-info]
                         [--dry-run] [--force] [--json]

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
    del conversation_scenario          # TODO: use it, or keep ignoring it
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
                eval_status=(
                    EvalStatus.PASSED if score >= threshold else EvalStatus.FAILED
                ),
            )
        )

    if not per_invocation:
        # Nothing to score is not a failure - it is an unscored case, and
        # conflating the two is how a broken suite looks green.
        return EvaluationResult(
            overall_score=None,
            overall_eval_status=EvalStatus.NOT_EVALUATED,
            per_invocation_results=[],
        )

    overall = sum(r.score or 0.0 for r in per_invocation) / len(per_invocation)
    return EvaluationResult(
        overall_score=overall,
        overall_eval_status=(
            EvalStatus.PASSED if overall >= threshold else EvalStatus.FAILED
        ),
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

METRIC_INFO_BLOCK = '''def {function}_metric_info() -> MetricInfo:
    """Describe the metric to anything that lists the available ones.

    Optional: ADK scores the metric without it. It is what makes the metric
    legible in the dev UI and in `metric_info.py` output, which matters once
    somebody other than the author has to decide whether to enable it.
    """
    return MetricInfo(
        metric_name="{metric}",
        description="{description}",
        metric_value_info=MetricValueInfo(
            interval=Interval(min_value=0.0, max_value=1.0)
        ),
    )'''

METRIC_INFO_IMPORTS = '''from google.adk.evaluation.eval_metrics import Interval
from google.adk.evaluation.eval_metrics import MetricInfo
from google.adk.evaluation.eval_metrics import MetricValueInfo
'''


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
    parser.add_argument("--kind", choices=("sync", "async"), default="sync")
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--description", default="",
                        help="one line; shown wherever metrics are listed")
    parser.add_argument("--with-metric-info", action="store_true",
                        help="also emit a MetricInfo provider")
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

    out = Path(args.out)
    if out.suffix != ".py":
        print(f"error: --out must be a .py module, not {out.name}", file=sys.stderr)
        return EXIT_USAGE

    function = args.name
    description = args.description or f"Custom metric {args.name}."
    layout = detect_layout(out.parent if out.parent.exists() else Path("."))
    path = dotted_path(out, Path(layout.project_root) if layout.project_root else None)

    body = (ASYNC_BODY if args.kind == "async" else SYNC_BODY).format(function=function)
    info = (
        METRIC_INFO_IMPORTS + "\n\n" + METRIC_INFO_BLOCK.format(
            function=function, metric=args.name, description=description,
        )
        if args.with_metric_info else
        "# Optional: re-run with --with-metric-info to add a MetricInfo provider,\n"
        "# which is what makes the metric legible in the dev UI."
    )

    content = render(TEMPLATE.read_text(encoding="utf-8"), {
        "METRIC_NAME": args.name,
        "METRIC_DESCRIPTION": description,
        "THRESHOLD": repr(args.threshold),
        "DOTTED_PATH": f"{path}.{function}",
        "FUNCTION_BODY": body,
        "METRIC_INFO": info,
    })

    plan = WritePlan()
    plan.add(out, content, force=args.force,
             reason=f"{args.kind} custom metric {args.name}")

    snippet = {
        "criteria": {
            args.name: {
                "threshold": args.threshold,
                "custom_function_path": f"{path}.{function}",
            }
        }
    }
    next_steps = [
        f"replace the TODO in `_score_one`: as written it only checks that a tool "
        f"was called, which proves nothing",
        f"add the snippet above to your eval config, then check it with "
        f"validate_config.py (CFG055 resolves the dotted path)",
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
        "threshold": args.threshold,
        "out": str(out),
        "dotted_path": f"{path}.{function}",
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
