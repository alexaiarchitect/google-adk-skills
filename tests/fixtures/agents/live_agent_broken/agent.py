"""Expense claim agent (fixture: a streaming tool on a text model).

The same claim tracker as `live_agent`, but the agent runs on a text model, so
every run goes through `run_async`: `adk run`, `adk eval` and text `adk web`.
ADK streams an async-generator tool only under `run_live`, so here the second
`yield` aborts the run on ADK 2.12+ (`ValueError: Output already set`) and on
2.9.2 the body never runs (AGT081).
"""

import asyncio
import os
from typing import Any, AsyncGenerator

from google.adk.agents import Agent


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

    Call this ONCE per claim.

    Args:
        claim_id: The claim reference, e.g. "CLM-000412".

    Yields:
        A progress line, then the final decision.
    """
    await asyncio.sleep(1.0)
    yield f"PROGRESS: {claim_id} receipt checked"
    yield f"DONE: {claim_id} approved for payment"


INSTRUCTION = """You answer questions about the state of expense claims.

1. Always call `lookup_claim` first with the claim reference the user gives.
2. If the claim is in audit, call `track_claim_progress` once and report each
   update.

Do NOT call `track_claim_progress` twice for the same claim. If the user has
not given a claim reference, ask for it.
"""

root_agent = Agent(
    name="claims_tracker",
    model=os.getenv("MODEL_NAME", "gemini-flash-latest"),
    description="Reports where an expense claim stands.",
    instruction=INSTRUCTION,
    tools=[lookup_claim, track_claim_progress],
)
