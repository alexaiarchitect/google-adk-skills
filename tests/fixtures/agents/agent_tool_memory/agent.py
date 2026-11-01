"""Expense claim agent that asks a policy sub-agent through an AgentTool.

Fixture for AGT082: `policy_lookup` binds `load_memory`, and `claims_agent`
calls it as `AgentTool(agent=policy_lookup)`. Through ADK 2.13.0 the AgentTool
runs `policy_lookup` with a fresh, empty memory service, so the memory lookup
never sees the exceptions the parent's memory service holds.
"""

import os

from google.adk.agents import Agent
from google.adk.tools import load_memory
from google.adk.tools.agent_tool import AgentTool

MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")


def submit_claim(claim_id: str, cost_centre: str, amount_usd: float) -> dict:
    """Submit an approved expense claim for payment.

    Args:
        claim_id: Claim reference in the form CLM-000123.
        cost_centre: The cost centre that is charged, lower case.
        amount_usd: Claim amount in US dollars.

    Returns:
        {"status": "submitted", "claim_id": str} or {"status": "error", "error": str}.
    """
    if not claim_id.startswith("CLM-"):
        return {"status": "error", "error": "claim_id must look like CLM-000123"}
    if amount_usd < 0:
        return {"status": "error", "error": "amount_usd must not be negative"}
    return {"status": "submitted", "claim_id": claim_id, "cost_centre": cost_centre}


policy_lookup = Agent(
    name="policy_lookup",
    model=MODEL,
    description="Finds the policy exceptions previously granted to a claimant.",
    instruction=(
        "Search memory for exceptions previously granted to the claimant named in the "
        "request. If memory holds none, say that no exception is on record."
    ),
    tools=[load_memory],
)

root_agent = Agent(
    name="claims_agent",
    model=MODEL,
    description="Approves or rejects expense claims.",
    instruction=(
        "You process expense claims. Before you submit a claim over 500 USD, ask "
        "policy_lookup whether the claimant has an exception on record. If there is no "
        "exception, do not submit the claim; explain that it needs a manager's approval."
    ),
    tools=[AgentTool(agent=policy_lookup), submit_claim],
)
