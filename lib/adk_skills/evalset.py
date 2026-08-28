"""Load, normalize, validate and migrate ADK evalset files.

Strictness is **not** uniform across ADK's eval models, and every difference
below was read off the live 2.9.2 models rather than assumed:

============================  ==================  ========================
model                         camelCase aliases   unknown keys
============================  ==================  ========================
``EvalSet`` (top level)       **no**              ignored
``EvalCase``                  yes                 allowed (kept)
``SessionInput``              yes                 allowed (kept)
``Invocation``                yes                 **rejected**
``IntermediateData``          yes                 **rejected**
``ConversationScenario``      yes                 **rejected**
``Rubric``                    yes                 **rejected**
============================  ==================  ========================

So ``evalSetId``/``evalCases`` at the top level do not parse at all - the
document loads with both required fields missing - while the same camelCase
convention one level down is fine. The canonical shape (snake_case, which is
what the CLI and docs use) is::

    {
      "eval_set_id": "expense_claims",          # ^[a-zA-Z0-9_]+$ when saved
      "name": "Expense claims",
      "description": "...",
      "eval_cases": [
        {
          "eval_id": "happy_claim_within_policy",
          "conversation": [                      # XOR conversation_scenario
            {
              "invocation_id": "inv_1",
              "user_content": {"parts": [{"text": "..."}], "role": "user"},
              "final_response": {"parts": [{"text": "..."}], "role": "model"},
              "intermediate_data": {
                "tool_uses": [{"name": "lookup_policy", "args": {...}}],
                "tool_responses": [],
                "intermediate_responses": []
              }
            }
          ],
          "session_input": {"app_name": "app", "user_id": "evaluator", "state": {}},
          "rubrics": [...]                       # filtered by `type`
        }
      ]
    }

A legacy shape also still loads: a top-level JSON *list* of
``{name, data: [{query, expected_tool_use: [{tool_name, tool_input}], reference}],
initial_session}``. ADK converts it on read and assigns a random eval set id.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .report import Finding, GapReport

#: `local_eval_sets_manager` enforces this when writing an eval set.
EVAL_SET_ID_RE = re.compile(r"^[a-zA-Z0-9_]+$")

#: Subtrees whose keys belong to the user's tools or state, never to ADK.
OPAQUE_KEYS = frozenset({
    "args", "state", "response", "final_session_state", "metadata", "labels",
    "environment_data", "custom_metadata",
})

#: Structural fields, snake_case. `EvalSet` is a plain BaseModel, so extras are
#: dropped silently: a typo here changes nothing and says nothing.
EVAL_SET_KEYS = frozenset({"eval_set_id", "name", "description", "eval_cases", "creation_timestamp"})
#: `EvalCase` and `SessionInput` are `extra="allow"`, so extras survive
#: round-tripping and are equally silent.
EVAL_CASE_KEYS = frozenset({
    "eval_id", "conversation", "conversation_scenario", "session_input",
    "creation_timestamp", "rubrics", "final_session_state",
})
#: The four models below are `extra="forbid"`: an unrecognized key raises at
#: load, so these are errors rather than notes.
INVOCATION_KEYS = frozenset({
    "invocation_id", "user_content", "final_response", "intermediate_data",
    "creation_timestamp", "rubrics", "app_details",
})
#: Exactly the fields `IntermediateData` declares in ADK 2.9.2. Kept tight on
#: purpose: adding a key ADK has not shipped yet would turn a load-time
#: ValidationError into silence, so this list is revisited per ADK release
#: rather than padded for forward compatibility.
INTERMEDIATE_DATA_KEYS = frozenset({
    "tool_uses", "tool_responses", "intermediate_responses",
})
CONVERSATION_SCENARIO_KEYS = frozenset({
    "starting_prompt", "conversation_plan", "user_persona",
})
RUBRIC_KEYS = frozenset({"rubric_id", "rubric_content", "type", "description"})

#: Rubric `type` values each rubric-based criterion accepts.
RUBRIC_TYPES = ("FINAL_RESPONSE_QUALITY", "TOOL_USE_QUALITY", "TRAJECTORY_QUALITY")

CATEGORY_PREFIXES = {
    "happy": ("happy", "baseline", "golden", "pipeline", "full"),
    "edge": ("edge", "boundary", "reject", "refuse", "forbidden", "missing", "empty"),
    "adversarial": ("adversarial", "trap", "attack", "injection", "restricted", "conflict"),
    "memory": ("memory", "recall", "context", "needle", "long_horizon", "multiturn", "multi_turn"),
    "regression": ("regression", "bug", "issue", "repro"),
}

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def camel_to_snake(key: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", key).lower()


def snake_to_camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part.title() for part in rest)


def _is_camel(key: str) -> bool:
    return any(char.isupper() for char in key) and "_" not in key


def normalize_keys(value: Any, *, _opaque: bool = False) -> Any:
    """Recursively convert camelCase keys to snake_case.

    Tool arguments and session state are left exactly as written: those keys
    belong to the agent's own tools, not to ADK's schema.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _opaque:
                result[key] = item
                continue
            new_key = camel_to_snake(key) if _is_camel(key) else key
            result[new_key] = normalize_keys(item, _opaque=new_key in OPAQUE_KEYS)
        return result
    if isinstance(value, list):
        return [normalize_keys(item, _opaque=_opaque) for item in value]
    return value


def key_style(value: Any) -> set[str]:
    """Which key conventions appear in a document: {'snake'}, {'camel'} or both."""
    styles: set[str] = set()

    def walk(node: Any, opaque: bool = False) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if not opaque:
                    if _is_camel(key):
                        styles.add("camel")
                    elif "_" in key:
                        styles.add("snake")
                walk(item, opaque or camel_to_snake(key) in OPAQUE_KEYS)
        elif isinstance(node, list):
            for item in node:
                walk(item, opaque)

    walk(value)
    return styles


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

@dataclass
class LoadedEvalSet:
    path: str
    #: Always the EvalSet-shaped document. A legacy file is migrated on read so
    #: that every counter - case totals, coverage, maturity - sees its cases
    #: instead of silently scoring the project as having none.
    doc: Any
    raw: Any
    fmt: str                    # evalset | legacy | invalid
    styles: set[str] = field(default_factory=set)
    error: str | None = None
    #: wrapped | flat | None - which legacy shape the raw list was in.
    legacy_shape: str | None = None
    #: camelCase keys found at the top level, which `EvalSet` cannot read.
    camel_top_level: list[str] = field(default_factory=list)

    @property
    def is_legacy(self) -> bool:
        return self.fmt == "legacy"

    @property
    def is_flat_legacy(self) -> bool:
        return self.legacy_shape == "flat"

    @property
    def ok(self) -> bool:
        return self.fmt in ("evalset", "legacy")


def legacy_shape(raw: Any) -> str | None:
    """Which legacy shape a top-level list is in.

    ``wrapped`` - ``[{name, data: [turn, ...], initial_session}]``, one entry
    per case, which is what `adk eval` still reads.

    ``flat`` - ``[{query, expected_tool_use, reference}, ...]``: a bare list of
    turns with no ``data`` key. `AgentEvaluator` wraps the whole list as a
    single case; `adk eval` cannot read it at all.
    """
    if not isinstance(raw, list) or not raw:
        return None
    entries = [e for e in raw if isinstance(e, dict)]
    if not entries:
        return None
    if any("data" in entry for entry in entries):
        return "wrapped"
    if any("query" in entry or "expected_tool_use" in entry for entry in entries):
        return "flat"
    return "wrapped"


def load_evalset(path: Path | str) -> LoadedEvalSet:
    """Read an evalset file, detect its format, and normalize its keys."""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return LoadedEvalSet(str(target), None, None, "invalid", error="file not found")
    except json.JSONDecodeError as exc:
        return LoadedEvalSet(str(target), None, None, "invalid", error=f"invalid JSON: {exc}")
    except (OSError, UnicodeDecodeError) as exc:
        return LoadedEvalSet(str(target), None, None, "invalid", error=str(exc))

    styles = key_style(raw)
    if isinstance(raw, list):
        shape = legacy_shape(raw)
        stem = target.name.replace(".test.json", "").replace(".evalset.json", "")
        migrated = migrate_legacy(raw, eval_set_id=sanitize_eval_set_id(stem))
        return LoadedEvalSet(str(target), migrated, raw, "legacy", styles,
                             legacy_shape=shape)
    if not isinstance(raw, dict):
        return LoadedEvalSet(str(target), None, raw, "invalid", styles,
                             error="top level must be an object (eval set) or a list (legacy)")
    camel_top = sorted(key for key in raw if _is_camel(key))
    return LoadedEvalSet(str(target), normalize_keys(raw), raw, "evalset", styles,
                         camel_top_level=camel_top)


# ---------------------------------------------------------------------------
# traversal helpers
# ---------------------------------------------------------------------------

def iter_cases(doc: dict[str, Any]) -> Iterator[dict[str, Any]]:
    cases = doc.get("eval_cases")
    if isinstance(cases, list):
        for case in cases:
            if isinstance(case, dict):
                yield case


def iter_invocations(case: dict[str, Any]) -> Iterator[dict[str, Any]]:
    conversation = case.get("conversation")
    if isinstance(conversation, list):
        for invocation in conversation:
            if isinstance(invocation, dict):
                yield invocation


def iter_tool_uses(case: dict[str, Any]) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield (invocation index, tool_use) for every expected call in a case."""
    for index, invocation in enumerate(iter_invocations(case)):
        data = invocation.get("intermediate_data")
        if not isinstance(data, dict):
            continue
        for tool_use in data.get("tool_uses") or []:
            if isinstance(tool_use, dict):
                yield index, tool_use


def tool_sequence(case: dict[str, Any]) -> list[str]:
    return [tool.get("name", "?") for _, tool in iter_tool_uses(case)]


def text_of(content: Any) -> str:
    """Pull the text out of a genai `Content` dict."""
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    chunks = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    return "".join(chunks)


def case_category(eval_id: str) -> str:
    """Classify a case from its id, using the naming convention this plugin
    recommends (``<category>_<behaviour>``)."""
    lowered = (eval_id or "").lower()
    for category, prefixes in CATEGORY_PREFIXES.items():
        for prefix in prefixes:
            if lowered.startswith(prefix) or f"_{prefix}" in lowered:
                return category
    return "uncategorized"


VAGUE_IDS = re.compile(r"^(test|case|eval|example|sample|tmp|foo|bar|untitled)[_\-]?\d*$")


def stats(doc: dict[str, Any]) -> dict[str, Any]:
    """Shape metrics a skill can show the user without reading every case."""
    cases = list(iter_cases(doc))
    by_category: dict[str, int] = {}
    multi_turn = 0
    scenario_cases = 0
    tools_used: dict[str, int] = {}
    with_reference = 0
    total_turns = 0

    for case in cases:
        category = case_category(str(case.get("eval_id", "")))
        by_category[category] = by_category.get(category, 0) + 1
        turns = len(list(iter_invocations(case)))
        total_turns += turns
        if turns > 1:
            multi_turn += 1
        if case.get("conversation_scenario"):
            scenario_cases += 1
        for _, tool in iter_tool_uses(case):
            name = tool.get("name", "?")
            tools_used[name] = tools_used.get(name, 0) + 1
        if any(text_of(inv.get("final_response")).strip() for inv in iter_invocations(case)):
            with_reference += 1

    return {
        "eval_set_id": doc.get("eval_set_id"),
        "cases": len(cases),
        "turns": total_turns,
        "multi_turn_cases": multi_turn,
        "scenario_cases": scenario_cases,
        "cases_with_reference_response": with_reference,
        "by_category": by_category,
        "tools_referenced": sorted(tools_used),
        "tool_call_counts": tools_used,
    }


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

_SRC_EVAL_SET = "adk-python: evaluation/eval_set.py"
_SRC_EVAL_CASE = "adk-python: evaluation/eval_case.py"
_SRC_MANAGER = "adk-python: evaluation/local_eval_sets_manager.py"
_SRC_GENERATOR = "adk-python: evaluation/evaluation_generator.py"
_SRC_RUBRICS = "adk-docs: docs/evaluate/criteria.md (rubric merge rules)"


def _tool_checker(
    known_tools: set[str] | None, tool_resolver: Any
) -> Any:
    """One predicate for "could the agent ever emit this name?".

    Prefers a resolver built from the agent's facts, which can tell a toolset
    apart from a typo. Falls back to a plain set of names for callers that only
    have that, and to "assume it is fine" when neither is supplied.
    """
    if tool_resolver is not None:
        def check(name: str) -> bool:
            return bool(tool_resolver(name).is_real)
        return check
    if known_tools is not None:
        allowed = set(known_tools)

        def check(name: str) -> bool:
            return name in allowed
        return check
    return None


def validate_evalset(
    loaded: LoadedEvalSet,
    *,
    report: GapReport | None = None,
    expected_app_name: str | None = None,
    known_tools: set[str] | None = None,
    reference_metrics: bool | None = None,
    tool_resolver: Any = None,
    trajectory_match_type: str | None = None,
) -> GapReport:
    """Check one evalset against what ADK 2.x will accept and what it needs.

    `expected_app_name` enables the app-name consistency check.
    `known_tools` / `tool_resolver` enable the unknown-tool check. A resolver
    (`tools_ast.tool_resolver`) is preferred: it knows that a toolset has no
    readable definition, so EVS036 means one thing everywhere.
    `reference_metrics` says whether the configured criteria compare against a
    reference response, which decides if an empty `final_response` matters.
    """
    report = report or GapReport(subject=loaded.path, skill="eval-review")
    path = loaded.path

    if loaded.fmt == "invalid":
        report.add("EVS001", "error", "evalset", loaded.error or "unreadable evalset",
                   path=path, fix="fix the JSON, or regenerate the file", source=_SRC_EVAL_SET)
        return report

    if loaded.is_legacy:
        report.add(
            "EVS050", "warn", "evalset",
            "legacy list format: ADK converts it on read and assigns a random eval set id",
            path=path,
            fix="migrate with eval-create's migrate_legacy.py to get a stable eval_set_id",
            source=_SRC_MANAGER,
        )
        if loaded.is_flat_legacy:
            report.add(
                "EVS051", "warn", "evalset",
                "flat-turn legacy: `adk eval` cannot read it (KeyError 'data'); only "
                "AgentEvaluator.evaluate() can - migrate first",
                path=path,
                fix="run eval-create's migrate_legacy.py; the whole list becomes one case, "
                    "which is exactly what AgentEvaluator does with it today",
                source="adk-python: evaluation/agent_evaluator.py "
                       "(_get_eval_set_from_old_format)",
            )
        return report

    doc = loaded.doc
    if loaded.camel_top_level:
        report.add(
            "EVS007", "error", "evalset",
            f"camelCase key(s) at the top level: {', '.join(loaded.camel_top_level)}",
            path=path,
            fix="rename to snake_case (eval_set_id, eval_cases). Unlike every model below "
                "it, `EvalSet` is a plain BaseModel with no alias generator, so these are "
                "not read and the required fields come back missing. camelCase inside "
                "cases and invocations is fine",
            source=f"{_SRC_EVAL_SET} (EvalSet has no alias_generator)",
        )
    if "camel" in loaded.styles and "snake" in loaded.styles:
        report.add("EVS041", "warn", "evalset",
                   "file mixes camelCase and snake_case keys; both parse, but pick one",
                   path=path, fix="normalize to snake_case (what the CLI and docs use)",
                   source="adk-python: evaluation/common.py (alias_generator=to_camel)")

    eval_set_id = doc.get("eval_set_id")
    if not eval_set_id:
        report.add("EVS002", "error", "evalset", "missing `eval_set_id`", path=path,
                   fix="add a stable snake_case id, e.g. the filename stem",
                   source=_SRC_EVAL_SET)
    elif not isinstance(eval_set_id, str) or not EVAL_SET_ID_RE.match(eval_set_id):
        report.add("EVS003", "error", "evalset",
                   f"`eval_set_id` {eval_set_id!r} must match ^[a-zA-Z0-9_]+$",
                   path=path, fix="use letters, digits and underscores only (no dashes or dots)",
                   source=f"{_SRC_MANAGER} (id validation on save)")

    unknown_top = sorted(set(doc) - EVAL_SET_KEYS)
    if unknown_top:
        report.add("EVS040", "warn", "evalset",
                   f"unrecognized top-level key(s): {', '.join(unknown_top)}",
                   path=path,
                   fix="remove or rename; EvalSet ignores unknown keys, so a typo is silent",
                   source=_SRC_EVAL_SET)

    cases = doc.get("eval_cases")
    if cases is None:
        report.add("EVS004", "error", "evalset", "missing `eval_cases`", path=path,
                   fix="add an `eval_cases` array (may be empty while you capture traces)",
                   source=_SRC_EVAL_SET)
        return report
    if not isinstance(cases, list):
        report.add("EVS005", "error", "evalset", "`eval_cases` must be an array",
                   path=path, source=_SRC_EVAL_SET)
        return report
    if not cases:
        report.add("EVS006", "warn", "evalset",
                   "`eval_cases` is empty: this evalset asserts nothing yet",
                   path=path, fix="capture a golden trace or add a case with eval-create",
                   source=_SRC_EVAL_SET)

    seen_ids: dict[str, int] = {}
    for index, case in enumerate(cases):
        locator = f"eval_cases[{index}]"
        if not isinstance(case, dict):
            report.add("EVS010", "error", "evalset", "eval case must be an object",
                       path=path, locator=locator, source=_SRC_EVAL_CASE)
            continue
        _validate_case(
            case, index, report, path,
            seen_ids=seen_ids,
            expected_app_name=expected_app_name,
            tool_known=_tool_checker(known_tools, tool_resolver),
            reference_metrics=reference_metrics,
        )
    _check_restraint_under_lenient_match(cases, trajectory_match_type, report, path)
    return report


LENIENT_MATCH_TYPES = ("IN_ORDER", "ANY_ORDER")


def _is_restraint_case(case: dict[str, Any]) -> bool:
    """A case written to prove a call does NOT happen: a refusal or trap."""
    eval_id = str(case.get("eval_id") or case.get("evalId") or "")
    head = eval_id.split("_", 1)[0]
    return head in ("edge", "adversarial") or "_no_" in eval_id or eval_id.endswith("_no")


def _check_restraint_under_lenient_match(
    cases: list[Any], match_type: str | None, report: GapReport, path: str | None
) -> None:
    """IN_ORDER and ANY_ORDER let extra calls through (`trajectory_evaluator.py`),
    so a case that exists to assert an absent `submit_claim` still passes when
    the agent submits and then rejects. Only EXACT, or a restraint rubric, can
    fail it for the reason it was written."""
    if match_type not in LENIENT_MATCH_TYPES:
        return
    restraint = [
        str(c.get("eval_id") or c.get("evalId"))
        for c in cases if isinstance(c, dict) and _is_restraint_case(c)
    ]
    if not restraint:
        return
    shown = ", ".join(restraint[:4]) + (" ..." if len(restraint) > 4 else "")
    report.add(
        "EVS062", "warn", "evalset",
        f"{len(restraint)} restraint case(s) scored with `{match_type}`, which allows "
        f"extra tool calls: the forbidden call cannot fail the case ({shown})",
        path=path,
        fix="set `match_type` to `EXACT` for the gating config, or add a "
            "`rubric_based_tool_use_quality_v1` restraint rubric that names the call "
            "that must not appear",
        source="adk-python: evaluation/trajectory_evaluator.py (IN_ORDER and ANY_ORDER "
               "allow additional tool calls)",
    )


def _validate_case(
    case: dict[str, Any],
    index: int,
    report: GapReport,
    path: str,
    *,
    seen_ids: dict[str, int],
    expected_app_name: str | None,
    tool_known: Any,
    reference_metrics: bool | None,
) -> None:
    locator = f"eval_cases[{index}]"
    eval_id = case.get("eval_id")

    if not eval_id or not isinstance(eval_id, str):
        report.add("EVS011", "error", "evalset", "case is missing `eval_id`",
                   path=path, locator=locator,
                   fix="name it <category>_<behaviour>, e.g. edge_frozen_budget_no_order",
                   source=_SRC_EVAL_CASE)
    else:
        locator = f"{locator} ({eval_id})"
        if eval_id in seen_ids:
            report.add("EVS012", "error", "evalset",
                       f"duplicate `eval_id` {eval_id!r} (also at index {seen_ids[eval_id]})",
                       path=path, locator=locator,
                       fix="ids address a case in reports and CI logs; make them unique",
                       source=_SRC_EVAL_CASE)
        seen_ids[eval_id] = index
        if VAGUE_IDS.match(eval_id.lower()):
            report.add("EVS060", "warn", "evalset",
                       f"`eval_id` {eval_id!r} says nothing about the behaviour under test",
                       path=path, locator=locator,
                       fix="rename to <category>_<behaviour>; the id is the first thing read when CI fails")
        elif case_category(eval_id) == "uncategorized":
            report.add("EVS061", "info", "evalset",
                       f"`eval_id` {eval_id!r} has no category prefix "
                       "(happy/edge/adversarial/memory/regression)",
                       path=path, locator=locator,
                       fix="prefix the id so coverage reporting can bucket it")

    unknown = sorted(set(case) - EVAL_CASE_KEYS)
    if unknown:
        report.add("EVS013", "warn", "evalset",
                   f"unrecognized key(s) on the case: {', '.join(unknown)}",
                   path=path, locator=locator,
                   fix="EvalCase allows extra keys, so typos never raise; remove or rename",
                   source=_SRC_EVAL_CASE)

    has_conversation = isinstance(case.get("conversation"), list)
    has_scenario = bool(case.get("conversation_scenario"))
    if has_conversation and has_scenario:
        report.add("EVS014", "error", "evalset",
                   "case sets both `conversation` and `conversation_scenario`; exactly one is allowed",
                   path=path, locator=locator,
                   fix="keep the static conversation, or move to a scenario and drop the turns",
                   source=f"{_SRC_EVAL_CASE} (model validator)")
    elif not has_conversation and not has_scenario:
        report.add("EVS015", "error", "evalset",
                   "case has neither `conversation` nor `conversation_scenario`",
                   path=path, locator=locator,
                   fix="add at least one invocation, or a conversation_scenario for user simulation",
                   source=f"{_SRC_EVAL_CASE} (model validator)")

    if has_scenario:
        _validate_scenario(case["conversation_scenario"], report, path, locator)

    if has_conversation:
        conversation = case["conversation"]
        if not conversation:
            report.add("EVS016", "error", "evalset", "`conversation` is empty",
                       path=path, locator=locator,
                       fix="an eval case needs at least one user turn", source=_SRC_EVAL_CASE)
        for turn, invocation in enumerate(conversation):
            _validate_invocation(
                invocation, report, path, f"{locator}.conversation[{turn}]",
                tool_known=tool_known, reference_metrics=reference_metrics,
            )

    session_input = case.get("session_input")
    if session_input is None:
        report.add("EVS030", "warn", "evalset",
                   "no `session_input`: the eval session runs as app "
                   "\"EvaluationGenerator\" / user \"test_user_id\"",
                   path=path, locator=locator,
                   fix="add session_input with the agent's app_name and a stable user_id",
                   source=f"{_SRC_GENERATOR} (_get_or_create_eval_session defaults)")
    elif not isinstance(session_input, dict):
        report.add("EVS031", "error", "evalset", "`session_input` must be an object",
                   path=path, locator=locator, source=_SRC_EVAL_CASE)
    else:
        if not session_input.get("app_name"):
            report.add("EVS032", "error", "evalset", "`session_input.app_name` is required",
                       path=path, locator=locator, source=_SRC_EVAL_CASE)
        elif expected_app_name and session_input["app_name"] != expected_app_name:
            report.add(
                "EVS033", "warn", "evalset",
                f"`session_input.app_name` is {session_input['app_name']!r} but the agent "
                f"resolves to {expected_app_name!r}",
                path=path, locator=locator,
                fix=(f"set it to {expected_app_name!r} so `app:`-scoped state, memory and "
                     "artifacts line up and ADK stops appending a mismatch hint to errors"),
                source="adk-python: runners.py (_enforce_app_name_alignment)",
            )
        if not session_input.get("user_id"):
            report.add("EVS034", "warn", "evalset",
                       "`session_input.user_id` is unset; user-scoped state needs a stable id",
                       path=path, locator=locator, source=_SRC_EVAL_CASE)

    for position, rubric in enumerate(case.get("rubrics") or []):
        _validate_rubric(rubric, report, path, f"{locator}.rubrics[{position}]", per_case=True)


def _validate_scenario(scenario: Any, report: GapReport, path: str, locator: str) -> None:
    if not isinstance(scenario, dict):
        report.add("EVS090", "error", "evalset", "`conversation_scenario` must be an object",
                   path=path, locator=locator,
                   source="adk-python: evaluation/conversation_scenarios.py")
        return
    if not scenario.get("starting_prompt"):
        report.add("EVS091", "error", "evalset",
                   "`conversation_scenario.starting_prompt` is required",
                   path=path, locator=locator,
                   source="adk-python: evaluation/conversation_scenarios.py")
    if not scenario.get("conversation_plan"):
        report.add("EVS092", "error", "evalset",
                   "`conversation_scenario.conversation_plan` is required: it is what the "
                   "simulated user follows",
                   path=path, locator=locator,
                   source="adk-python: evaluation/conversation_scenarios.py")
    unknown = sorted(set(scenario) - CONVERSATION_SCENARIO_KEYS)
    if unknown:
        report.add("EVS093", "error", "evalset",
                   f"unrecognized key(s) on the conversation_scenario: {', '.join(unknown)}",
                   path=path, locator=locator,
                   fix=f"`ConversationScenario` is extra=\"forbid\" and accepts only "
                       f"{', '.join(sorted(CONVERSATION_SCENARIO_KEYS))}",
                   source="adk-python: evaluation/conversation_scenarios.py "
                          "(extra=\"forbid\")")


def _validate_invocation(
    invocation: Any,
    report: GapReport,
    path: str,
    locator: str,
    *,
    tool_known: Any,
    reference_metrics: bool | None,
) -> None:
    if not isinstance(invocation, dict):
        report.add("EVS020", "error", "evalset", "invocation must be an object",
                   path=path, locator=locator, source=_SRC_EVAL_CASE)
        return

    unknown = sorted(set(invocation) - INVOCATION_KEYS)
    if unknown:
        report.add("EVS021", "error", "evalset",
                   f"unrecognized key(s) on the invocation: {', '.join(unknown)}",
                   path=path, locator=locator,
                   fix="`Invocation` is extra=\"forbid\", so this raises a ValidationError "
                       "when the file is read; remove or rename the key",
                   source=f"{_SRC_EVAL_CASE} (Invocation, extra=\"forbid\")")

    user_content = invocation.get("user_content")
    if not user_content:
        report.add("EVS022", "error", "evalset", "invocation is missing `user_content`",
                   path=path, locator=locator, source=_SRC_EVAL_CASE)
    else:
        if not text_of(user_content).strip() and not _has_non_text_part(user_content):
            report.add("EVS023", "error", "evalset",
                       "`user_content` carries no text and no other part",
                       path=path, locator=locator,
                       fix="add parts: [{\"text\": \"...\"}]", source=_SRC_EVAL_CASE)
        role = user_content.get("role") if isinstance(user_content, dict) else None
        if role not in (None, "user"):
            report.add("EVS024", "warn", "evalset",
                       f"`user_content.role` is {role!r}; the user turn should be \"user\"",
                       path=path, locator=locator, source=_SRC_EVAL_CASE)

    final_response = invocation.get("final_response")
    has_reference = bool(text_of(final_response).strip())
    if not has_reference:
        if reference_metrics:
            report.add(
                "EVS025", "error", "evalset",
                "no `final_response` text, but the criteria compare against a reference "
                "response (response_match_score / final_response_match_v2)",
                path=path, locator=locator,
                fix="add the expected answer, or drop the reference-based metric "
                    "(set response_match_score to 0.0 for trajectory-only cases)",
                source="adk-docs: docs/evaluate/criteria.md",
            )
        else:
            report.add("EVS026", "info", "evalset",
                       "no `final_response` text: this case asserts trajectory only",
                       path=path, locator=locator)

    data = invocation.get("intermediate_data")
    if data is not None and not isinstance(data, dict):
        report.add("EVS027", "error", "evalset", "`intermediate_data` must be an object",
                   path=path, locator=locator, source=_SRC_EVAL_CASE)
        return
    if isinstance(data, dict):
        unknown_data = sorted(set(data) - INTERMEDIATE_DATA_KEYS)
        if unknown_data:
            report.add(
                "EVS047", "error", "evalset",
                f"unrecognized key(s) in `intermediate_data`: {', '.join(unknown_data)}",
                path=path, locator=locator,
                fix=f"`IntermediateData` is extra=\"forbid\" and accepts only "
                    f"{', '.join(sorted(INTERMEDIATE_DATA_KEYS))}",
                source=f"{_SRC_EVAL_CASE} (IntermediateData, extra=\"forbid\")",
            )
        tool_uses = data.get("tool_uses")
        if tool_uses is not None and not isinstance(tool_uses, list):
            report.add("EVS028", "error", "evalset", "`intermediate_data.tool_uses` must be an array",
                       path=path, locator=locator, source=_SRC_EVAL_CASE)
            return
        for position, tool_use in enumerate(tool_uses or []):
            tool_locator = f"{locator}.tool_uses[{position}]"
            if not isinstance(tool_use, dict):
                report.add("EVS029", "error", "evalset", "tool use must be an object",
                           path=path, locator=tool_locator, source=_SRC_EVAL_CASE)
                continue
            name = tool_use.get("name")
            if not name:
                report.add("EVS035", "error", "evalset", "tool use is missing `name`",
                           path=path, locator=tool_locator, source=_SRC_EVAL_CASE)
            elif tool_known is not None and not tool_known(str(name)):
                report.add(
                    "EVS036", "error", "evalset",
                    f"expected call to {name!r}, which the agent does not define",
                    path=path, locator=tool_locator,
                    fix="fix the spelling, or add the tool; a name that never fires can never match",
                    source="adk-python: evaluation/trajectory_evaluator.py",
                )
            args = tool_use.get("args")
            if args is not None and not isinstance(args, dict):
                report.add("EVS037", "error", "evalset", "`args` must be an object",
                           path=path, locator=tool_locator, source=_SRC_EVAL_CASE)

        _validate_tool_responses(data, tool_uses, report, path, locator)
        _validate_intermediate_responses(data, report, path, locator)


def _validate_tool_responses(
    data: dict[str, Any], tool_uses: Any, report: GapReport, path: str, locator: str
) -> None:
    responses = data.get("tool_responses")
    if responses is None:
        return
    if not isinstance(responses, list):
        report.add("EVS048", "error", "evalset",
                   "`intermediate_data.tool_responses` must be an array",
                   path=path, locator=locator, source=_SRC_EVAL_CASE)
        return
    expected = len(tool_uses) if isinstance(tool_uses, list) else 0
    if expected and len(responses) != expected:
        report.add(
            "EVS110", "info", "evalset",
            f"{len(responses)} tool_responses for {expected} tool_uses",
            path=path, locator=locator,
            fix="responses pair positionally with calls; an unequal count usually means a "
                "call was added without its recorded result, and nothing reads the orphan",
            source=_SRC_EVAL_CASE,
        )


def _validate_intermediate_responses(
    data: dict[str, Any], report: GapReport, path: str, locator: str
) -> None:
    """Each entry is a `[author, parts]` pair, not an object.

    `IntermediateData.intermediate_responses` is typed
    `list[tuple[str, list[Part]]]`, so a dict here fails validation at load.
    """
    entries = data.get("intermediate_responses")
    if entries is None:
        return
    if not isinstance(entries, list):
        report.add("EVS111", "error", "evalset",
                   "`intermediate_data.intermediate_responses` must be an array",
                   path=path, locator=locator, source=_SRC_EVAL_CASE)
        return
    for position, entry in enumerate(entries):
        where = f"{locator}.intermediate_responses[{position}]"
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            report.add(
                "EVS111", "error", "evalset",
                "intermediate response must be a two-element [author, parts] pair",
                path=path, locator=where,
                fix='write ["sub_agent_name", [{"text": "..."}]]',
                source=_SRC_EVAL_CASE,
            )
            continue
        author, parts = entry
        if not isinstance(author, str) or not author:
            report.add("EVS111", "error", "evalset",
                       "intermediate response author must be a non-empty string",
                       path=path, locator=where, source=_SRC_EVAL_CASE)
        if not isinstance(parts, list):
            report.add("EVS111", "error", "evalset",
                       "intermediate response parts must be an array of content parts",
                       path=path, locator=where, source=_SRC_EVAL_CASE)


def _has_non_text_part(content: Any) -> bool:
    if not isinstance(content, dict):
        return False
    for part in content.get("parts") or []:
        if isinstance(part, dict) and set(part) - {"text"}:
            return True
    return False


def _validate_rubric(
    rubric: Any, report: GapReport, path: str, locator: str, *, per_case: bool
) -> None:
    if not isinstance(rubric, dict):
        report.add("EVS080", "error", "evalset", "rubric must be an object",
                   path=path, locator=locator, source=_SRC_RUBRICS)
        return
    if not rubric.get("rubric_id"):
        report.add("EVS081", "error", "evalset", "rubric is missing `rubric_id`",
                   path=path, locator=locator, source=_SRC_RUBRICS)
    content = rubric.get("rubric_content")
    if not isinstance(content, dict) or not content.get("text_property"):
        report.add("EVS082", "error", "evalset",
                   "rubric needs `rubric_content.text_property`",
                   path=path, locator=locator, source=_SRC_RUBRICS)
    unknown = sorted(set(rubric) - RUBRIC_KEYS)
    if unknown:
        report.add("EVS085", "error", "evalset",
                   f"unrecognized key(s) on the rubric: {', '.join(unknown)}",
                   path=path, locator=locator,
                   fix=f"`Rubric` is extra=\"forbid\" and accepts only "
                       f"{', '.join(sorted(RUBRIC_KEYS))}",
                   source=f"{_SRC_RUBRICS} (Rubric, extra=\"forbid\")")
    if per_case:
        rubric_type = rubric.get("type")
        if not rubric_type:
            report.add(
                "EVS083", "warn", "evalset",
                "per-case rubric has no `type`, so no criterion will pick it up",
                path=path, locator=locator,
                fix=f"set type to one of {', '.join(RUBRIC_TYPES)}",
                source=_SRC_RUBRICS,
            )
        elif rubric_type not in RUBRIC_TYPES:
            report.add("EVS084", "error", "evalset",
                       f"rubric `type` {rubric_type!r} is not one of {', '.join(RUBRIC_TYPES)}",
                       path=path, locator=locator, source=_SRC_RUBRICS)


# ---------------------------------------------------------------------------
# legacy migration
# ---------------------------------------------------------------------------

def _legacy_turn(name: str, turn: int, item: dict[str, Any]) -> dict[str, Any]:
    tool_uses = [
        {"name": old.get("tool_name"), "args": old.get("tool_input") or {}}
        for old in item.get("expected_tool_use") or []
        if isinstance(old, dict)
    ]
    intermediate = [
        [resp.get("author"), [{"text": resp.get("text", "")}]]
        for resp in item.get("expected_intermediate_agent_responses") or []
        if isinstance(resp, dict)
    ]
    return {
        "invocation_id": f"{name}_turn_{turn + 1}",
        "user_content": {"parts": [{"text": item.get("query", "")}], "role": "user"},
        "final_response": {
            "parts": [{"text": item.get("reference", "")}],
            "role": "model",
        },
        "intermediate_data": {
            "tool_uses": tool_uses,
            "intermediate_responses": intermediate,
        },
    }


def migrate_legacy(
    legacy: list[dict[str, Any]],
    *,
    eval_set_id: str,
    app_name: str | None = None,
    user_id: str = "evaluator",
) -> dict[str, Any]:
    """Convert either legacy list format to the EvalSet schema.

    Mirrors `convert_eval_set_to_pydantic_schema`, but keeps ids stable and
    readable instead of generating UUIDs: an evalset is a specification under
    version control, and a stable diff matters more than a unique id.

    Two shapes exist in the wild. The **wrapped** one is a list of cases, each
    with its own ``data`` array of turns. The **flat** one is a bare list of
    turns; `AgentEvaluator._get_eval_set_from_old_format` takes the whole list
    as one case's ``data``, so that is what happens here - splitting it per
    turn would invent cases that never shared a session and quietly change what
    the file asserts.
    """
    if not isinstance(legacy, list):
        legacy = []
    shape = legacy_shape(legacy)
    entries: list[dict[str, Any]]
    if shape == "flat":
        entries = [{
            "name": eval_set_id,
            "data": [item for item in legacy if isinstance(item, dict)],
            "initial_session": {},
        }]
    else:
        entries = [entry for entry in legacy if isinstance(entry, dict)]

    cases: list[dict[str, Any]] = []
    for position, entry in enumerate(entries):
        name = entry.get("name") or f"case_{position + 1}"
        initial = entry.get("initial_session") or entry.get("initial_state") or {}
        if not isinstance(initial, dict):
            initial = {}
        conversation = [
            _legacy_turn(name, turn, item)
            for turn, item in enumerate(entry.get("data") or [])
            if isinstance(item, dict)
        ]
        cases.append({
            "eval_id": name,
            "conversation": conversation,
            "session_input": {
                "app_name": initial.get("app_name") or app_name or eval_set_id,
                "user_id": initial.get("user_id") or user_id,
                "state": initial.get("state") or {},
            },
        })

    return {
        "eval_set_id": eval_set_id,
        "name": eval_set_id.replace("_", " ").strip().title(),
        "description": "Migrated from the legacy ADK eval file format.",
        "eval_cases": cases,
    }


def sanitize_eval_set_id(raw: str) -> str:
    """Turn a filename stem into a legal `eval_set_id`."""
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    if not cleaned:
        cleaned = "eval_set"
    if not cleaned[0].isalpha():
        cleaned = f"s_{cleaned}"
    return cleaned
