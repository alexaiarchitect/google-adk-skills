"""Expense claim router with two state mistakes (fixture: AGT080, AGT015).

* The review instruction asks for `{approver}`, which no tool, callback,
  `output_key`, evalset or `initial_state.json` ever writes: ADK raises
  KeyError ("Context variable not found") when it builds that instruction.
* Two agents named `checker` sit under different parents (cousins). ADK 2.13
  warns, never raises; `find_agent("checker")` returns the first one found.

It loads on ADK 2.9.2 and 2.13.0: both mistakes only show at run time.
"""

import os
from typing import Any

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.tool_context import ToolContext

MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")


def start_claim(callback_context: CallbackContext) -> None:
    """Put a new session at the first step."""
    if "claim_step" not in callback_context.state:
        callback_context.state["claim_step"] = "new"
    return None


def record_receipt(claim_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Record that the receipt for a claim has arrived.

    Args:
        claim_id: Claim reference, CLM-000123.

    Returns:
        A dict with `status` and the new `claim_step`.
    """
    tool_context.state["claim_step"] = "receipt_validated"
    return {"status": "ok", "claim_id": claim_id, "claim_step": "receipt_validated"}


intake_checker = Agent(
    name="checker",
    model=MODEL,
    description="Checks that a new claim has a receipt.",
    instruction="Check that the claim at step {claim_step} has a receipt attached.",
    tools=[record_receipt],
)

review_checker = Agent(
    name="checker",
    model=MODEL,
    description="Checks a claim before it goes to the approver.",
    instruction="Check the claim at step {claim_step} before it reaches {approver}.",
)

intake = Agent(
    name="intake",
    model=MODEL,
    description="Takes in new claims.",
    instruction="Take in the claim. Hand it to the checker when it has a receipt.",
    sub_agents=[intake_checker],
)

review = Agent(
    name="review",
    model=MODEL,
    description="Reviews claims that have a receipt.",
    instruction="Review the claim, then hand it to the checker.",
    sub_agents=[review_checker],
)

root_agent = Agent(
    name="claims_router",
    model=MODEL,
    description="Routes an expense claim to intake or review.",
    instruction="Route new claims to intake and claims with a receipt to review.",
    sub_agents=[intake, review],
    before_agent_callback=start_claim,
)
