"""Expense claim voice agent (fixture: a streaming tool done right).

A live model, so `run_live` streams each `yield` of `track_claim_progress` to the
caller as a partial function response while the audit runs. The docstring
tells the model to call it once per claim, a module-level latch makes a second
call harmless, and `stop_streaming` lets the model cancel the stream. The eval
config next to it sets `live_model_config`, so `adk eval` drives it through the
Live API too.
"""

import asyncio
import os
from typing import Any, AsyncGenerator

from google.adk.agents import Agent

_AUDIT_STEPS = ("receipt checked", "policy limit checked", "cost centre funds checked")
_IN_FLIGHT: set[str] = set()


def lookup_claim(claim_id: str) -> dict[str, Any]:
    """Look up one expense claim by its reference.

    Call this first, before tracking the claim.

    Args:
        claim_id: The claim reference, e.g. "CLM-000412".

    Returns:
        A dict with `status` and, on success, `claim_id`, `amount_usd` and
        `state`.
    """
    if not claim_id.startswith("CLM-"):
        return {"status": "not_found", "claim_id": claim_id}
    return {"status": "ok", "claim_id": claim_id, "amount_usd": 412.0, "state": "in_audit"}


async def track_claim_progress(claim_id: str) -> AsyncGenerator[str, None]:
    """Stream the audit progress of one claim while it runs.

    Call this ONCE per claim. Every update arrives on its own, so never call it
    again for the same claim while it is running. Each update starts with
    `PROGRESS:` and the last one with `DONE:`.

    Args:
        claim_id: The claim reference, e.g. "CLM-000412".

    Yields:
        One line per audit step, then the final decision.
    """
    if claim_id in _IN_FLIGHT:
        yield f"PROGRESS: {claim_id} is already being tracked"
        return
    _IN_FLIGHT.add(claim_id)
    try:
        for step in _AUDIT_STEPS:
            await asyncio.sleep(1.0)
            yield f"PROGRESS: {claim_id} {step}"
        yield f"DONE: {claim_id} approved for payment"
    finally:
        _IN_FLIGHT.discard(claim_id)


def stop_streaming(function_name: str) -> dict[str, Any]:
    """Stop a streaming tool that is still running.

    Call this when the caller no longer wants updates, e.g.
    stop_streaming(function_name="track_claim_progress").

    Args:
        function_name: The name of the streaming tool to stop.

    Returns:
        A dict with `status` and the `function_name` that was stopped.
    """
    return {"status": "ok", "function_name": function_name}


INSTRUCTION = """You answer callers about the state of their expense claims.

1. Always call `lookup_claim` first with the claim reference the caller gives.
2. If the claim is in audit, call `track_claim_progress` once and read each
   update to the caller as it arrives.
3. If the caller says they do not need more updates, call `stop_streaming`
   with function_name "track_claim_progress".

Do NOT call `track_claim_progress` again for a claim that is already being
tracked. If the caller has not given a claim reference, ask for it.
"""

root_agent = Agent(
    name="claims_voice_agent",
    model=os.getenv("MODEL_NAME", "gemini-3.8-live"),
    description="Tells callers where their expense claim stands, live.",
    instruction=INSTRUCTION,
    tools=[lookup_claim, track_claim_progress, stop_streaming],
)
