"""Coverage analysis: does the eval suite exercise what the agent promises?

An agent's instruction is its specification. Every branch in it - each "if this,
refuse", each required ordering - is a behaviour that either has a test or does
not. This module reads the instruction into a list of rules, matches eval cases
against them, and reports the gaps.

Two shape rules come with it:

* **Branch coverage** - an instruction with *N* conditional branches needs at
  least *N + 1* cases: one per branch, plus the unobstructed happy path.
* **Suite shape** - roughly 70% narrow single-turn cases, 20% multi-turn, 10%
  adversarial. Fast deterministic cases carry the suite; a few well-chosen
  adversarial ones are worth more than another dozen happy paths. Weight the
  adversarial layer up for anything that spends money or touches a person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Sequence

TARGET_SHAPE = {"narrow": 0.70, "multi_turn": 0.20, "adversarial": 0.10}

#: Below this many cases the shape of a suite is not information. Three cases
#: are not "10% adversarial" short; they are three cases.
MIN_CASES_FOR_SHAPE = 5

#: Above this many rules the instruction is prose, not a procedure, and listing
#: every sentence as an untested rule buries the few that matter.
MAX_RULES_REPORTED = 40

#: The message coverage reports instead of inventing zero rules.
DYNAMIC_INSTRUCTION_NOTE = "instruction is dynamic; coverage not computable"

MANDATORY = re.compile(r"\b(?:must|shall|always|require[ds]?|ensure|first|then|before|after)\b", re.I)
# Only explicit negatives. "reject" and "refuse" are ordinary verbs in this
# domain ("call reject_claim"), so they do not by themselves mark a prohibition.
PROHIBITION = re.compile(
    r"\b(?:never|must not|may not|cannot|can not|do not|don'?t|"
    r"shall not|should not|refuse to|forbidden|prohibited|"
    r"under no circumstances|without ever)\b",
    re.I,
)
CONDITIONAL = re.compile(r"\b(?:if|when|unless|whenever|in case|should the|otherwise|in the event)\b", re.I)

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[a-z][.)])\s+", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is", "are",
    "be", "you", "your", "it", "its", "this", "that", "as", "at", "by", "from", "if",
    "when", "then", "must", "not", "never", "always", "do", "does", "should", "will",
    "can", "may", "user", "agent", "call", "use", "using", "any", "all", "each",
}


@dataclass
class SopRule:
    index: int
    text: str
    kind: str                      # mandatory | prohibition | conditional | informational
    tools: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def is_branch(self) -> bool:
        """Does this rule create a path the agent can take or refuse to take?"""
        return self.kind in ("prohibition", "conditional")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["is_branch"] = self.is_branch
        return data


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9_]{2,}", text.lower())
    return [w for w in dict.fromkeys(words) if w not in STOPWORDS]


def _mentions(haystack: str, needle: str) -> bool:
    """Whole-word containment.

    Substring matching claims `submit` covers `submit_claim` and that a rule
    about `get_image` is exercised by a case calling `get_image_metadata`, which
    turns the matrix into noise.
    """
    if not needle:
        return False
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])", haystack) is not None


def extract_sop_rules(instruction: str | None, known_tools: Iterable[str] = ()) -> list[SopRule]:
    """Split an instruction into individually testable rules."""
    if not instruction:
        return []
    tools = list(known_tools)
    rules: list[SopRule] = []

    # Rejoin wrapped lines first: an instruction is usually hard-wrapped, and a
    # continuation line is not a rule of its own.
    blocks: list[str] = []
    for raw_line in instruction.splitlines():
        line = raw_line.strip()
        if not line:
            blocks.append("")
            continue
        starts_item = bool(_BULLET.match(line)) or line.isupper()
        if blocks and blocks[-1] and not starts_item and not blocks[-1].endswith((".", "!", "?", ":")):
            blocks[-1] = f"{blocks[-1]} {line}"
        else:
            blocks.append(line)

    candidates: list[str] = []
    for line in (b for b in blocks if b):
        if _BULLET.match(line):
            line = _BULLET.sub("", line).strip()
        elif line.endswith(":") and len(line) < 60:
            continue                     # a section heading, not a rule
        candidates.extend(part.strip() for part in _SENTENCE_SPLIT.split(line) if part.strip())

    for text in candidates:
        if len(text) < 8:
            continue
        if PROHIBITION.search(text):
            kind = "prohibition"
        elif CONDITIONAL.search(text):
            kind = "conditional"
        elif MANDATORY.search(text):
            kind = "mandatory"
        else:
            kind = "informational"
        rules.append(SopRule(
            index=len(rules) + 1,
            text=text,
            kind=kind,
            tools=[tool for tool in tools if _mentions(text, tool)],
            keywords=_keywords(text),
        ))
    return rules


def summarize_rules(rules: Sequence[SopRule]) -> dict[str, Any]:
    """Rules as a report shows them, capped so the signal survives.

    An instruction that splits into hundreds of rules is a document, not a
    standard operating procedure, and the honest thing to report is that rather
    than four hundred "uncovered" sentences.
    """
    by_kind: dict[str, int] = {}
    for rule in rules:
        by_kind[rule.kind] = by_kind.get(rule.kind, 0) + 1
    summary: dict[str, Any] = {
        "total": len(rules),
        "by_kind": by_kind,
        "branches": branch_count(rules),
        "truncated": len(rules) > MAX_RULES_REPORTED,
        "rules": [r.to_dict() for r in rules[:MAX_RULES_REPORTED]],
    }
    if summary["truncated"]:
        summary["note"] = (
            f"{len(rules)} rules extracted; the instruction reads as prose rather than an "
            f"SOP, so only the first {MAX_RULES_REPORTED} are listed. Coverage per sentence "
            "is not a useful target here - restructure the instruction into numbered steps "
            "and prohibitions first"
        )
    return summary


def branch_count(rules: Sequence[SopRule]) -> int:
    return sum(1 for rule in rules if rule.is_branch)


@dataclass
class CaseSummary:
    """Just enough about an eval case to match it against rules."""

    eval_id: str
    category: str
    tools: list[str]
    turns: int
    text: str
    evalset: str | None = None
    #: Keys the case seeds in `session_input.state`: a resumed session, the
    #: way a suite simulates time passing (a workflow already at a step).
    seeded_state: list[str] = field(default_factory=list)

    @property
    def is_narrow(self) -> bool:
        return self.turns <= 1 and self.category not in ("adversarial",)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_cases(evalsets: dict[str, dict[str, Any]]) -> list[CaseSummary]:
    """Build case summaries from `{path: normalized evalset document}`."""
    from .evalset import case_category, iter_cases, iter_invocations, iter_tool_uses, text_of

    summaries: list[CaseSummary] = []
    for path, doc in evalsets.items():
        for case in iter_cases(doc):
            eval_id = str(case.get("eval_id", ""))
            texts = []
            for invocation in iter_invocations(case):
                texts.append(text_of(invocation.get("user_content")))
                texts.append(text_of(invocation.get("final_response")))
            scenario = case.get("conversation_scenario") or {}
            if isinstance(scenario, dict):
                texts.extend([
                    str(scenario.get("starting_prompt", "")),
                    str(scenario.get("conversation_plan", "")),
                ])
            session_input = case.get("session_input")
            state = session_input.get("state") if isinstance(session_input, dict) else None
            summaries.append(CaseSummary(
                eval_id=eval_id,
                category=case_category(eval_id),
                tools=[tool.get("name", "") for _, tool in iter_tool_uses(case)],
                turns=len(list(iter_invocations(case))) or 1,
                text=" ".join(t for t in texts if t),
                evalset=path,
                seeded_state=sorted(str(k) for k in state) if isinstance(state, dict) else [],
            ))
    return summaries


@dataclass
class Matrix:
    rules: list[SopRule]
    cases: list[CaseSummary]
    coverage: dict[int, list[str]] = field(default_factory=dict)

    def uncovered(self) -> list[SopRule]:
        return [rule for rule in self.rules if not self.coverage.get(rule.index)]

    def uncovered_branches(self) -> list[SopRule]:
        return [rule for rule in self.uncovered() if rule.is_branch]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": [rule.to_dict() for rule in self.rules],
            "coverage": {str(k): v for k, v in self.coverage.items()},
            "uncovered_rule_indexes": [rule.index for rule in self.uncovered()],
            "uncovered_branch_indexes": [rule.index for rule in self.uncovered_branches()],
        }

    def to_md(self) -> str:
        lines = ["| # | kind | rule | covered by |", "|---|---|---|---|"]
        for rule in self.rules:
            cases = self.coverage.get(rule.index) or []
            covered = ", ".join(f"`{c}`" for c in cases) if cases else "**nothing**"
            text = rule.text if len(rule.text) <= 78 else rule.text[:75] + "..."
            lines.append(f"| {rule.index} | {rule.kind} | {text.replace('|', '/')} | {covered} |")
        return "\n".join(lines)


def matrix(rules: Sequence[SopRule], cases: Sequence[CaseSummary]) -> Matrix:
    """Map each rule to the cases that plausibly exercise it.

    A case covers a rule when it calls a tool the rule names, or when its text
    shares distinctive wording with the rule. This is a heuristic, so it is
    reported as evidence for a human decision, never as a pass or fail.
    """
    result = Matrix(rules=list(rules), cases=list(cases))
    for rule in rules:
        matched: list[str] = []
        rule_keywords = set(rule.keywords)
        for case in cases:
            if rule.tools and set(rule.tools) & set(case.tools):
                matched.append(case.eval_id)
                continue
            haystack = f"{case.eval_id} {case.text}".lower()
            overlap = {word for word in rule_keywords if _mentions(haystack, word)}
            # A long rule sharing two words with a case is a coincidence; a
            # short one sharing two is most of the rule. Scale with length.
            needed = max(2, len(rule_keywords) // 3)
            if len(overlap) >= needed:
                matched.append(case.eval_id)
        result.coverage[rule.index] = matched
    return result


def shape(cases: Sequence[CaseSummary]) -> dict[str, Any]:
    """Actual suite shape against the 70/20/10 target."""
    total = len(cases)
    if not total:
        return {"total": 0, "counts": {}, "ratios": {}, "target": TARGET_SHAPE, "verdict": "empty"}

    adversarial = sum(1 for c in cases if c.category == "adversarial")
    multi_turn = sum(1 for c in cases if c.turns > 1 and c.category != "adversarial")
    narrow = total - adversarial - multi_turn

    counts = {"narrow": narrow, "multi_turn": multi_turn, "adversarial": adversarial}
    ratios = {key: round(value / total, 3) for key, value in counts.items()}

    notes: list[str] = []
    if adversarial == 0:
        notes.append("no adversarial case: nothing in the suite is designed to make the agent fail")
    if multi_turn == 0:
        notes.append("no multi-turn case: context retention is untested")
    if ratios["narrow"] < 0.4:
        notes.append("few narrow single-turn cases; the suite will be slow and hard to localize")

    by_category: dict[str, int] = {}
    for case in cases:
        by_category[case.category] = by_category.get(case.category, 0) + 1

    seeded = seeded_state_cases(cases)
    result = {
        "total": total,
        "counts": counts,
        "ratios": ratios,
        "target": TARGET_SHAPE,
        "by_category": by_category,
        # Orthogonal to the 70/20/10 split: any case may start from seeded state.
        "seeded_state_cases": len(seeded),
    }
    if total < MIN_CASES_FOR_SHAPE:
        # A 70/20/10 target cannot be read off three cases. Telling someone
        # with two cases to rebalance their suite is advice about a ratio that
        # does not exist yet; what they need is more cases.
        result["notes"] = []
        result["suppressed_notes"] = notes
        result["verdict"] = "too_small"
        return result
    result["notes"] = notes
    result["verdict"] = "ok" if not notes else "gaps"
    return result


def seeded_state_cases(cases: Sequence[CaseSummary]) -> dict[str, list[str]]:
    """`{eval_id: seeded keys}` for the cases that start from seeded state."""
    return {case.eval_id: case.seeded_state for case in cases if case.seeded_state}


def n_plus_one_gaps(rules: Sequence[SopRule], cases: Sequence[CaseSummary]) -> dict[str, Any]:
    """Compare case count with the branch count the instruction implies.

    With no branches there is no N+1 rule to apply: "0 branches need at least 1
    case" is arithmetic dressed as a finding, and it fires on every agent whose
    instruction happens to contain no conditional.
    """
    branches = branch_count(rules)
    required = branches + 1 if branches else 0
    return {
        "branches": branches,
        "cases": len(cases),
        "minimum_cases": required,
        "shortfall": max(0, required - len(cases)) if branches else 0,
    }


def tool_coverage(
    tool_names: Iterable[str],
    cases: Sequence[CaseSummary],
    *,
    resolver: Any = None,
) -> dict[str, Any]:
    """Which of the agent's tools appear in an expected trajectory, and which
    tools the cases expect that the agent does not have.

    `resolver` is `tools_ast.tool_resolver`. With it, a name that belongs to a
    toolset or an `AgentTool` is recognised as real rather than reported as
    unknown - which is the same judgement EVS036 makes, made in one place.
    """
    tools = set(tool_names)
    used: dict[str, int] = {}
    for case in cases:
        for tool in case.tools:
            used[tool] = used.get(tool, 0) + 1

    seen = set(used)
    if resolver is not None:
        unknown = sorted(name for name in seen - tools if resolver(name).status == "unknown")
        opaque = sorted(
            name for name in seen - tools if resolver(name).status in ("opaque", "unresolved")
        )
    else:
        unknown = sorted(seen - tools)
        opaque = []
    return {
        "tools": sorted(tools),
        "exercised": sorted(seen & tools),
        "never_exercised": sorted(tools - seen),
        "unknown_in_cases": unknown,
        "opaque_in_cases": opaque,
        "call_counts": used,
    }


def skill_coverage(
    registered: Iterable[str] | None, evalsets: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Which registered skills some case activates with `load_skill`.

    A skill is activated by its description alone: the model reads the L1
    metadata and decides to call `load_skill(skill_name=...)`. A skill no case
    ever expects to be loaded is a description nobody has tested. None when the
    agent registers no skills or the set is unknowable (registry, GCS).
    """
    if registered is None:
        return None
    from .evalset import expected_skill_loads, iter_cases

    names = set(registered)
    loads: dict[str, int] = {}
    for doc in evalsets.values():
        for case in iter_cases(doc):
            for _, skill in expected_skill_loads(case):
                loads[skill] = loads.get(skill, 0) + 1
    return {
        "skills": sorted(names),
        "activated": sorted(names & set(loads)),
        "never_activated": sorted(names - set(loads)),
        "unknown_in_cases": sorted(set(loads) - names),
        "load_counts": loads,
    }
