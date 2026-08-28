"""Normalize ADK run traces into a flat event list.

Two sources, one shape out:

* ``adk run --jsonl <agent> "<prompt>"`` prints one JSON event per line, dumped
  with aliases, so its keys are camelCase (``functionCall``, ``invocationId``)
  with a ``session_id`` and ``node_path`` added per line. Without ``--jsonl``
  the command prints text only, so tool calls and tool errors are invisible -
  which is why a trace worth analyzing is always captured with the flag.
* A saved session JSON (``adk run --save_session``, or a session fetched from
  the dev server) holds the same events under an ``events`` key.

A tool that failed does not raise: ADK turns a rejected or malformed call into a
function response carrying an ``error`` key and lets the model continue. So "the
tool silently did nothing" is visible only by reading the responses, which
:func:`tool_errors` does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from .results import _get


@dataclass
class TraceEvent:
    index: int
    author: str = ""
    invocation_id: str = ""
    text: str = ""
    function_calls: list[dict[str, Any]] = field(default_factory=list)
    function_responses: list[dict[str, Any]] = field(default_factory=list)
    node_path: str | None = None
    branch: str | None = None
    is_final: bool = False
    partial: bool = False
    finish_reason: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_user(self) -> bool:
        return self.author == "user"

    @property
    def has_calls(self) -> bool:
        return bool(self.function_calls)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


def _parts(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, dict):
        return []
    parts = content.get("parts")
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


def _event_from_dict(raw: dict[str, Any], index: int) -> TraceEvent:
    event = TraceEvent(index=index, raw=raw)
    event.author = str(_get(raw, "author", default="") or "")
    event.invocation_id = str(_get(raw, "invocation_id", default="") or "")
    event.partial = bool(_get(raw, "partial", default=False))
    event.node_path = raw.get("node_path") or (
        (_get(raw, "node_info") or {}).get("path") if isinstance(_get(raw, "node_info"), dict) else None
    )
    event.branch = _get(raw, "branch")
    event.finish_reason = _get(raw, "finish_reason")
    event.error = _get(raw, "error_message") or _get(raw, "error_code")

    content = _get(raw, "content") or {}
    texts: list[str] = []
    for part in _parts(content):
        if part.get("text"):
            texts.append(str(part["text"]))
        call = part.get("function_call") or part.get("functionCall")
        if isinstance(call, dict):
            event.function_calls.append({
                "name": call.get("name"),
                "args": call.get("args") or {},
                "id": call.get("id"),
            })
        response = part.get("function_response") or part.get("functionResponse")
        if isinstance(response, dict):
            event.function_responses.append({
                "name": response.get("name"),
                "response": response.get("response"),
                "id": response.get("id"),
            })
    event.text = "".join(texts)

    actions = _get(raw, "actions") or {}
    if isinstance(actions, dict) and actions.get("escalate"):
        event.is_final = True
    if event.text and not event.function_calls and not event.partial:
        event.is_final = True
    return event


def load_jsonl(path: Path | str) -> list[TraceEvent]:
    """Parse `adk run --jsonl` output. Non-JSON lines (log noise) are skipped."""
    events: list[TraceEvent] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return events
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            events.append(_event_from_dict(raw, len(events)))
    return events


def load_session_json(path: Path | str) -> list[TraceEvent]:
    """Parse a saved session file, or a bare list of events."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if isinstance(raw, dict):
        raw_events = _get(raw, "events", default=[]) or []
    elif isinstance(raw, list):
        raw_events = raw
    else:
        return []
    return [
        _event_from_dict(item, index)
        for index, item in enumerate(raw_events)
        if isinstance(item, dict)
    ]


def load_trace(path: Path | str) -> list[TraceEvent]:
    """Load either format, deciding by content rather than extension."""
    target = Path(path)
    try:
        head = target.read_text(encoding="utf-8").lstrip()[:400]
    except (OSError, UnicodeDecodeError):
        return []
    if head.startswith("["):
        return load_session_json(target)
    if head.startswith("{"):
        # A single JSON object is a session; many lines are JSONL.
        try:
            json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return load_jsonl(target)
        return load_session_json(target)
    return load_jsonl(target)


# ---------------------------------------------------------------------------
# views over a trace
# ---------------------------------------------------------------------------

def tool_calls(events: Iterable[TraceEvent]) -> list[dict[str, Any]]:
    """Every tool call in order, as `{name, args}` (the actual trajectory)."""
    calls: list[dict[str, Any]] = []
    for event in events:
        for call in event.function_calls:
            calls.append({"name": call.get("name"), "args": call.get("args") or {}})
    return calls


def model_texts(events: Iterable[TraceEvent]) -> list[str]:
    """Natural-language messages from the agent, skipping streamed fragments."""
    return [
        event.text for event in events
        if event.text and not event.is_user and not event.partial
    ]


def final_text(events: Iterable[TraceEvent]) -> str:
    candidates = [event.text for event in events if event.text and not event.is_user and not event.partial]
    return candidates[-1] if candidates else ""


def user_texts(events: Iterable[TraceEvent]) -> list[str]:
    return [event.text for event in events if event.is_user and event.text]


def tool_errors(events: Iterable[TraceEvent]) -> list[dict[str, Any]]:
    """Tool responses that carry an error.

    ADK converts a missing argument, a rejected confirmation or a tool-side
    failure into a response with an ``error`` key rather than an exception, so
    the run continues and the failure is easy to miss.
    """
    errors: list[dict[str, Any]] = []
    for event in events:
        for response in event.function_responses:
            payload = response.get("response")
            if isinstance(payload, dict):
                keys = {str(k).lower() for k in payload}
                if keys & {"error", "error_message", "errors"} or str(payload.get("status", "")).lower() in ("error", "failed"):
                    errors.append({
                        "tool": response.get("name"),
                        "index": event.index,
                        "payload": payload,
                    })
    return errors


def invocations(events: Iterable[TraceEvent]) -> list[dict[str, Any]]:
    """Group events by invocation: one user turn and everything it produced."""
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        key = event.invocation_id or "unknown"
        if key not in grouped:
            grouped[key] = {
                "invocation_id": key,
                "user_text": "",
                "tool_calls": [],
                "tool_responses": [],
                "final_text": "",
                "events": 0,
            }
            order.append(key)
        bucket = grouped[key]
        bucket["events"] += 1
        if event.is_user and event.text:
            bucket["user_text"] = event.text
        elif event.text and not event.partial:
            bucket["final_text"] = event.text
        bucket["tool_calls"].extend(
            {"name": c.get("name"), "args": c.get("args") or {}} for c in event.function_calls
        )
        bucket["tool_responses"].extend(event.function_responses)
    return [grouped[key] for key in order]


def to_eval_invocations(events: Iterable[TraceEvent], *, include_responses: bool = False) -> list[dict[str, Any]]:
    """Turn a trace into eval-case invocations - the golden-trace capture step.

    Mirrors what the dev server's "add current session to eval set" button does:
    the user turn, the tool calls with their arguments, and the final text become
    the expected values of an eval case.
    """
    result: list[dict[str, Any]] = []
    for position, invocation in enumerate(invocations(events), start=1):
        if not invocation["user_text"] and not invocation["tool_calls"]:
            continue
        intermediate: dict[str, Any] = {"tool_uses": invocation["tool_calls"]}
        if include_responses:
            intermediate["tool_responses"] = [
                {"name": r.get("name"), "response": r.get("response")}
                for r in invocation["tool_responses"]
            ]
        result.append({
            "invocation_id": invocation["invocation_id"] or f"inv_{position}",
            "user_content": {"parts": [{"text": invocation["user_text"]}], "role": "user"},
            "final_response": {"parts": [{"text": invocation["final_text"]}], "role": "model"},
            "intermediate_data": intermediate,
        })
    return result
