"""Rubric authoring rules, lint and builders.

A rubric is one sentence an LLM judge answers yes or no about. That is the whole
design constraint, and most bad rubrics break it in one of four ways:

* **graded** — "rate the response from 1 to 5" gives the judge a scale, and
  scores collect in the middle instead of separating pass from fail.
* **vague** — "the response is good/appropriate/professional" leaves the judge
  to invent a standard, so the verdict moves between runs.
* **compound** — "is polite and cites the policy and offers an alternative"
  fails as one bit, so you cannot tell which half broke.
* **unobservable** — claims about intent or internal reasoning that nothing in
  the transcript can settle.

A restraint rubric is the mirror image: state the action that must *not* happen
and the condition, naming the tool exactly as the agent defines it.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from .report import Finding

_SRC = "skills/eval-criteria/references/rubric-authoring.md"

SCALE_PATTERNS = (
    re.compile(r"\b(?:scale|rate|rating|score)\b.{0,24}\b(?:1|one)\b.{0,12}\b(?:5|10|five|ten)\b", re.I),
    re.compile(r"\b(?:1|one)\s*(?:-|to|through)\s*(?:5|10|five|ten)\b", re.I),
    re.compile(r"\bhow (?:well|good|much)\b", re.I),
    re.compile(r"\b(?:poor|average|excellent)\b.{0,40}\b(?:poor|average|excellent)\b", re.I),
)

VAGUE_TERMS = (
    "good", "bad", "nice", "appropriate", "appropriately", "properly", "correctly",
    "reasonable", "reasonably", "adequate", "adequately", "sufficient", "acceptable",
    "high quality", "high-quality", "helpful", "useful", "professional", "friendly",
    "clear", "concise", "well written", "well-written", "as expected",
)

UNOBSERVABLE_TERMS = (
    "understands", "understood", "believes", "knows", "intends", "tries to",
    "wants to", "thinks", "is aware", "realizes", "considers",
)

CONJUNCTIONS = re.compile(r"\b(?:and also|as well as|;)\b|\band\b(?=.{0,160}\band\b)", re.I)

# Verbs a rubric uses to state an observable expectation, third person. A
# connector ("and", "; ", "and then", "as well as") followed by one of these,
# with another one before it, joins two expectations: "names the rule and
# states the limit". A noun pair ("name and date of birth", "number and
# identifier") or an adjective pair ("concise and free of filler") does not.
CLAUSE_VERBS = frozenset("""
    acknowledges answers apologises apologizes applies asks avoids calls checks
    cites completes confirms declines delivers describes directs displays does
    ends escalates explains gives greets hands identifies includes informs
    invokes lists mentions names notes offers points proposes provides quotes
    recommends redirects refuses reports requests responds returns runs says
    sends shows states submits suggests summarises summarizes tells transfers
    updates uses validates verifies warns
""".split())

_CONNECTOR = re.compile(r";\s+|\s+and\s+then\s+|\s+as\s+well\s+as\s+|\s+and\s+(?:also\s+)?", re.I)
_WORD = re.compile(r"[A-Za-z']+")
_CLAUSE_LEAD = frozenset({"it", "they", "the", "agent", "response", "also", "then", "never",
                          "always"})


def _joins_two_clauses(text: str) -> bool:
    """One connector between two verb phrases: a two-expectation rubric."""
    for match in _CONNECTOR.finditer(text):
        before = {w.lower() for w in _WORD.findall(text[:match.start()])}
        after = _WORD.findall(text[match.end():])
        if not after or not before & CLAUSE_VERBS:
            continue
        # Skip a restated subject or an adverb: "; it also offers", "and then names".
        words = [w.lower() for w in after[:4]]
        while len(words) > 1 and words[0] in _CLAUSE_LEAD:
            words.pop(0)
        if words[0] in CLAUSE_VERBS:
            return True
    return False


NEGATIVE_MARKERS = re.compile(r"\b(?:does not|never|must not|refrains from|avoids|without)\b", re.I)

RUBRIC_TYPES = {
    "final_response": "FINAL_RESPONSE_QUALITY",
    "tool_use": "TOOL_USE_QUALITY",
    "trajectory": "TRAJECTORY_QUALITY",
}


def lint_rubric_text(
    text: str,
    *,
    rubric_id: str,
    known_tools: set[str] | None = None,
    path: str | None = None,
    locator: str | None = None,
) -> list[Finding]:
    """Findings for one rubric's `text_property`."""
    found: list[Finding] = []

    def add(fid: str, severity: str, message: str, fix: str) -> None:
        found.append(Finding(
            id=fid, severity=severity, area="rubric",
            message=f"rubric {rubric_id!r}: {message}",
            path=path, locator=locator, fix=fix, source=_SRC,
        ))

    stripped = text.strip()
    if not stripped:
        add("RUB001", "error", "text_property is empty", "state one yes/no property")
        return found

    if len(stripped) < 20:
        add("RUB002", "warn", f"text is very short ({len(stripped)} chars)",
            "a judge needs the condition as well as the action")

    for pattern in SCALE_PATTERNS:
        if pattern.search(stripped):
            add("RUB003", "error", "asks the judge for a graded rating",
                "rewrite as a single claim that is true or false, e.g. "
                "\"the response names the policy rule it applied\"")
            break

    lowered = stripped.lower()
    hits = [term for term in VAGUE_TERMS if re.search(rf"\b{re.escape(term)}\b", lowered)]
    if hits:
        add("RUB004", "warn", f"uses subjective wording: {', '.join(sorted(set(hits))[:4])}",
            "replace the judgement word with the observable fact you actually want, e.g. "
            "\"quotes the returned claim id\" instead of \"is helpful\"")

    unobservable = [term for term in UNOBSERVABLE_TERMS if term in lowered]
    if unobservable:
        add("RUB005", "warn",
            f"asks about the agent's internal state ({', '.join(sorted(set(unobservable))[:3])})",
            "score what the transcript shows, not what the agent understood")

    if CONJUNCTIONS.search(stripped) or _joins_two_clauses(stripped):
        add("RUB006", "warn",
            "bundles several properties into one verdict: one expectation per rubric; "
            "compound expectations grade inconsistently",
            "split it: one rubric per property, so a failure names the property")

    if known_tools:
        mentioned = {tool for tool in known_tools if tool in stripped}
        looks_like_tool = re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", stripped)
        unknown = {
            candidate for candidate in looks_like_tool
            if candidate not in known_tools and candidate not in ("text_property", "rubric_id")
        }
        if unknown and not mentioned:
            add("RUB007", "warn",
                f"names {', '.join(sorted(unknown)[:3])}, which is not a tool this agent defines",
                "use the exact tool name from the agent's code so the judge can match it")

    return found


def is_restraint_rubric(text: str) -> bool:
    """Does this rubric assert that something must NOT happen?"""
    return bool(NEGATIVE_MARKERS.search(text or ""))


def build_positive_rubric(rubric_id: str, text: str, *, rubric_type: str | None = None) -> dict:
    rubric: dict = {"rubric_id": rubric_id, "rubric_content": {"text_property": text.strip()}}
    if rubric_type:
        rubric["type"] = rubric_type
    return rubric


def build_restraint_rubric(
    rubric_id: str, tool: str, condition: str, *, rubric_type: str | None = "TOOL_USE_QUALITY"
) -> dict:
    """A rubric that proves the agent held back.

    Phrased as an observable negative, which is what makes it gradeable: the
    judge only has to look for a call that should not be there.
    """
    condition = condition.strip().rstrip(".")
    text = f"The agent does NOT call `{tool}` when {condition}."
    return build_positive_rubric(rubric_id, text, rubric_type=rubric_type)


def dedupe_ids(rubrics: Sequence[dict]) -> list[dict]:
    """Make `rubric_id` values unique, preserving order."""
    seen: dict[str, int] = {}
    result: list[dict] = []
    for rubric in rubrics:
        rubric = dict(rubric)
        base = str(rubric.get("rubric_id") or "rubric")
        if base in seen:
            seen[base] += 1
            rubric["rubric_id"] = f"{base}_{seen[base]}"
        else:
            seen[base] = 1
        result.append(rubric)
    return result


def suggest_rubric_id(text: str, *, prefix: str = "") -> str:
    """A readable id derived from the rubric's own wording."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {
        "the", "a", "an", "agent", "response", "does", "not", "is", "are", "and", "or",
        "when", "that", "for", "with", "it", "its", "to", "of", "in", "on", "has", "have",
    }
    keep = [word for word in words if word not in stop][:4]
    slug = "_".join(keep) or "rubric"
    return f"{prefix}{slug}" if prefix else slug


def lint_all(
    rubrics: Iterable[dict], *, known_tools: set[str] | None = None, path: str | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    for index, rubric in enumerate(rubrics):
        content = rubric.get("rubric_content") or {}
        text = content.get("text_property", "") if isinstance(content, dict) else ""
        findings.extend(lint_rubric_text(
            str(text),
            rubric_id=str(rubric.get("rubric_id") or index),
            known_tools=known_tools,
            path=path,
            locator=f"rubrics[{index}]",
        ))
    return findings
