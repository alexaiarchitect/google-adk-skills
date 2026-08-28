"""Compare trajectories, find the divergence point, and name failure shapes.

A trajectory is the ordered list of tool calls an agent made, with arguments.
Comparing it against the expected one answers a different question from "was the
final answer right": an agent can produce a correct-looking answer while
skipping a check, and only the trajectory shows that.

Three match modes, matching `ToolTrajectoryCriterion`:

* ``EXACT`` - same calls, same arguments, same order, nothing extra.
* ``IN_ORDER`` - every expected call appears in the expected relative order;
  additional calls in between are allowed (use it when a schema retry or a
  self-correction loop is legitimate).
* ``ANY_ORDER`` - every expected call appears, order irrelevant.

When a comparison fails, the useful output is not "mismatch" but the **first
point of divergence**: the earliest call where actual and expected part company.
Everything after that point is downstream noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Sequence

MATCH_EXACT = "EXACT"
MATCH_IN_ORDER = "IN_ORDER"
MATCH_ANY_ORDER = "ANY_ORDER"


def normalize_match_type(value: str | None) -> str:
    if not value:
        return MATCH_EXACT
    return str(value).upper().replace("-", "_").replace(" ", "_")


@dataclass
class Call:
    name: str
    args: dict[str, Any] = field(default_factory=dict)

    def key(self, *, ignore_args: bool = False) -> tuple:
        if ignore_args:
            return (self.name,)
        return (self.name, _freeze(self.args))

    def short(self, *, width: int = 72) -> str:
        """A one-line form that keeps whole key/value pairs rather than cutting
        mid-value, so a truncated call is still readable."""
        if not self.args:
            return f"{self.name}()"
        # A single model-shaped argument reads better unwrapped: `req={...}`
        # carries no information the tool name does not already give.
        args = self.args
        if len(args) == 1:
            only = next(iter(args.values()))
            if isinstance(only, dict) and only:
                args = only

        parts: list[str] = []
        used = 0
        for key, value in args.items():
            piece = f"{key}={value!r}"
            if used + len(piece) > width and parts:
                parts.append(f"+{len(args) - len(parts)} more")
                break
            parts.append(piece)
            used += len(piece) + 2
        return f"{self.name}({', '.join(parts)})"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "args": self.args}


def _freeze(value: Any) -> Any:
    """Hashable, order-insensitive view of a JSON value.

    Argument comparison in ADK is dict equality, so `2499` and `2499.0` are the
    same number in Python and do compare equal, while `"2499"` does not.
    """
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def to_calls(raw: Iterable[Any]) -> list[Call]:
    calls: list[Call] = []
    for item in raw or []:
        if isinstance(item, Call):
            calls.append(item)
        elif isinstance(item, dict):
            calls.append(Call(name=str(item.get("name") or "?"), args=item.get("args") or {}))
        elif isinstance(item, str):
            calls.append(Call(name=item))
    return calls


def tool_seq(calls: Iterable[Any]) -> list[str]:
    return [call.name for call in to_calls(calls)]


@dataclass
class ArgMismatch:
    index: int
    tool: str
    expected: dict[str, Any]
    actual: dict[str, Any]

    @property
    def differing_keys(self) -> list[str]:
        keys = set(self.expected) | set(self.actual)
        return sorted(k for k in keys if _freeze(self.expected.get(k)) != _freeze(self.actual.get(k)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "differing_keys": self.differing_keys,
            "expected": {k: self.expected.get(k) for k in self.differing_keys},
            "actual": {k: self.actual.get(k) for k in self.differing_keys},
        }


@dataclass
class Diff:
    match: bool
    match_type: str
    expected: list[Call]
    actual: list[Call]
    divergence_index: int | None = None
    divergence_kind: str | None = None      # missing | extra | wrong_tool | arg_mismatch | order
    missing: list[Call] = field(default_factory=list)
    extra: list[Call] = field(default_factory=list)
    arg_mismatches: list[ArgMismatch] = field(default_factory=list)

    def explain(self) -> str:
        if self.match:
            return f"trajectory matches ({self.match_type})"
        if self.divergence_index is None:
            return f"trajectory differs ({self.match_type})"
        index = self.divergence_index
        expected = self.expected[index].short() if index < len(self.expected) else "(nothing)"
        actual = self.actual[index].short() if index < len(self.actual) else "(nothing)"
        return (
            f"first divergence at call {index + 1}: expected {expected}, got {actual} "
            f"[{self.divergence_kind}]"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "match": self.match,
            "match_type": self.match_type,
            "expected": [c.short() for c in self.expected],
            "actual": [c.short() for c in self.actual],
            "divergence_index": self.divergence_index,
            "divergence_kind": self.divergence_kind,
            "missing": [c.short() for c in self.missing],
            "extra": [c.short() for c in self.extra],
            "arg_mismatches": [m.to_dict() for m in self.arg_mismatches],
            "explanation": self.explain(),
        }


def diff(
    expected: Iterable[Any],
    actual: Iterable[Any],
    *,
    match_type: str = MATCH_EXACT,
    ignore_args: bool = False,
) -> Diff:
    """Compare two trajectories under one of ADK's match modes."""
    mode = normalize_match_type(match_type)
    exp = to_calls(expected)
    act = to_calls(actual)
    result = Diff(match=False, match_type=mode, expected=exp, actual=act)

    if mode == MATCH_ANY_ORDER:
        remaining = list(act)
        for call in exp:
            for index, candidate in enumerate(remaining):
                if candidate.key(ignore_args=ignore_args) == call.key(ignore_args=ignore_args):
                    remaining.pop(index)
                    break
            else:
                result.missing.append(call)
        result.extra = remaining
        result.match = not result.missing
        if result.missing:
            first = result.missing[0]
            result.divergence_kind = "missing"
            for index, call in enumerate(exp):
                if call.key(ignore_args=ignore_args) == first.key(ignore_args=ignore_args):
                    result.divergence_index = index
                    break
        return result

    if mode == MATCH_IN_ORDER:
        cursor = 0
        for call in exp:
            found = False
            while cursor < len(act):
                if act[cursor].key(ignore_args=ignore_args) == call.key(ignore_args=ignore_args):
                    cursor += 1
                    found = True
                    break
                cursor += 1
            if not found:
                result.missing.append(call)
        result.match = not result.missing
        if result.missing:
            first = result.missing[0]
            result.divergence_kind = "missing"
            for index, call in enumerate(exp):
                if call.key(ignore_args=ignore_args) == first.key(ignore_args=ignore_args):
                    result.divergence_index = index
                    break
            # A call with the right name but wrong arguments is the more precise story.
            if not ignore_args:
                for index, call in enumerate(act):
                    if call.name == first.name and _freeze(call.args) != _freeze(first.args):
                        result.divergence_kind = "arg_mismatch"
                        result.arg_mismatches.append(
                            ArgMismatch(index=index, tool=call.name, expected=first.args, actual=call.args)
                        )
                        break
        return result

    # EXACT
    for index in range(max(len(exp), len(act))):
        expected_call = exp[index] if index < len(exp) else None
        actual_call = act[index] if index < len(act) else None
        if expected_call is None:
            result.extra.append(actual_call)  # type: ignore[arg-type]
            if result.divergence_index is None:
                result.divergence_index, result.divergence_kind = index, "extra"
            continue
        if actual_call is None:
            result.missing.append(expected_call)
            if result.divergence_index is None:
                result.divergence_index, result.divergence_kind = index, "missing"
            continue
        if expected_call.name != actual_call.name:
            if result.divergence_index is None:
                result.divergence_index, result.divergence_kind = index, "wrong_tool"
            result.missing.append(expected_call)
            result.extra.append(actual_call)
        elif not ignore_args and _freeze(expected_call.args) != _freeze(actual_call.args):
            mismatch = ArgMismatch(index=index, tool=expected_call.name,
                                   expected=expected_call.args, actual=actual_call.args)
            result.arg_mismatches.append(mismatch)
            if result.divergence_index is None:
                result.divergence_index, result.divergence_kind = index, "arg_mismatch"

    result.match = (
        result.divergence_index is None
        and not result.missing
        and not result.extra
        and not result.arg_mismatches
    )
    return result


# ---------------------------------------------------------------------------
# failure signatures
# ---------------------------------------------------------------------------

@dataclass
class Signature:
    kind: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    layer: str = "prompt"          # tool | prompt | model | eval
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_signatures(
    calls: Sequence[Any],
    *,
    model_texts: Sequence[str] = (),
    known_tools: Iterable[str] | None = None,
    max_expected_calls: int | None = None,
) -> list[Signature]:
    """Name the shape of a misbehaving run from its trajectory.

    Five shapes, each with a different root cause and therefore a different fix:

    ``loop``
        The same call with the same arguments, repeatedly. Nothing changes
        between attempts, so no new information is arriving - usually a missing
        termination condition, or a tool error message the model cannot act on.
    ``oscillation``
        Two or more calls alternating without ever moving forward. The
        instruction's ordering rules are circular.
    ``stall``
        Model text with no call where a call was required. The instruction gives
        no safe path for the case at hand.
    ``unknown_tool``
        A call to something that is not a registered tool: the model invented it.
    ``runaway``
        Far more calls than the case expects, without an exact repeat.
    """
    sequence = to_calls(calls)
    signatures: list[Signature] = []

    # loop: three or more consecutive identical calls
    run_length, run_start = 1, 0
    for index in range(1, len(sequence) + 1):
        same = (
            index < len(sequence)
            and sequence[index].key() == sequence[index - 1].key()
        )
        if same:
            run_length += 1
            continue
        if run_length >= 3:
            signatures.append(Signature(
                kind="loop",
                detail=f"{sequence[run_start].name} called {run_length} times in a row with identical arguments",
                evidence=[sequence[run_start].short()],
                layer="tool",
                fix="the repeat means nothing new came back: make the tool's error response say "
                    "what to change, and give the instruction an explicit retry limit",
            ))
        run_length, run_start = 1, index

    # oscillation: A,B,A,B with no progress
    names = [call.name for call in sequence]
    for index in range(len(names) - 3):
        a, b, c, d = names[index:index + 4]
        if a == c and b == d and a != b:
            signatures.append(Signature(
                kind="oscillation",
                detail=f"alternates between {a} and {b} without moving on",
                evidence=names[index:index + 4],
                layer="prompt",
                fix="the ordering rules are circular; state the progression explicitly "
                    f"(once {b} has answered, move on rather than revisiting {a})",
            ))
            break

    if known_tools is not None:
        allowed = set(known_tools)
        invented = sorted({call.name for call in sequence if call.name not in allowed})
        if invented:
            signatures.append(Signature(
                kind="unknown_tool",
                detail=f"called tool(s) that are not registered: {', '.join(invented)}",
                evidence=invented,
                layer="model",
                fix="the model invented a capability; name the available tools in the "
                    "instruction and add a groundedness check",
            ))

    if model_texts and not sequence:
        signatures.append(Signature(
            kind="stall",
            detail=f"{len(model_texts)} model message(s) and no tool call",
            evidence=[text[:80] for text in model_texts[:2]],
            layer="prompt",
            fix="the instruction leaves this input with no safe path; define the default "
                "action for an ambiguous or unmatched request",
        ))

    if max_expected_calls is not None and len(sequence) > max_expected_calls * 2:
        if not any(s.kind == "loop" for s in signatures):
            signatures.append(Signature(
                kind="runaway",
                detail=f"{len(sequence)} calls where the case expects {max_expected_calls}",
                evidence=names[:8],
                layer="prompt",
                fix="add an explicit stopping condition; long trajectories that pass on the "
                    "final answer still cost money and latency",
            ))
    return signatures


def cascade_suspect(diff_result: Diff) -> Signature | None:
    """An early argument mistake that later calls build on.

    The hardest failure to read: every call looks right, the sequence looks
    right, and the outcome is wrong because step one normalized an input
    incorrectly and nothing revalidated it.
    """
    if diff_result.match or not diff_result.arg_mismatches:
        return None
    first = min(diff_result.arg_mismatches, key=lambda m: m.index)
    later = len(diff_result.actual) - first.index - 1
    if later < 2:
        return None
    return Signature(
        kind="cascade_suspect",
        detail=(
            f"arguments first diverge at call {first.index + 1} ({first.tool}, "
            f"keys: {', '.join(first.differing_keys)}), and {later} later call(s) build on it"
        ),
        evidence=[f"{first.tool}: expected {first.expected}, got {first.actual}"],
        layer="tool",
        fix="fix the first divergence, not the last symptom: tighten the schema or the "
            "docstring for that argument, and assert on it in the eval case",
    )


# ---------------------------------------------------------------------------
# the trajectory x response quadrant
# ---------------------------------------------------------------------------

QUADRANTS = {
    "Q1": {
        "label": "trajectory and response both good",
        "reading": "the agent did the right things and said them well",
        "action": "nothing to fix; keep the case as a regression guard",
    },
    "Q2": {
        "label": "good trajectory, weak response",
        "reading": "the work was right, the wording was not. A presentation problem, not a logic one",
        "action": "fix the instruction's output format, or relax a brittle text metric "
                  "(response_match_score compares wording, not meaning)",
    },
    "Q3": {
        "label": "weak trajectory, good response",
        "reading": "the answer reads well but the agent skipped or reordered work to get there. "
                   "Every text-only metric passes this, which is what makes it the dangerous one",
        "action": "treat as a real failure: strengthen the instruction's ordering and refusal "
                  "rules, and check whether the answer was grounded in tool output at all",
    },
    "Q4": {
        "label": "both weak",
        "reading": "a plain logic failure, and the easiest to act on",
        "action": "start at the first divergence in the trajectory",
    },
}


def quadrant(
    trajectory_score: float | None,
    response_score: float | None,
    *,
    trajectory_threshold: float = 1.0,
    response_threshold: float = 0.8,
) -> dict[str, Any]:
    """Place a case in the trajectory x response quadrant."""
    if trajectory_score is None or response_score is None:
        return {
            "quadrant": None,
            "label": "not enough signal",
            "reading": "needs both a trajectory metric and a response metric to place",
            "action": "add the missing metric if you want this diagnosis",
        }
    good_trajectory = trajectory_score >= trajectory_threshold
    good_response = response_score >= response_threshold
    key = "Q1" if good_trajectory and good_response else "Q2" if good_trajectory else "Q3" if good_response else "Q4"
    return {"quadrant": key, **QUADRANTS[key]}
