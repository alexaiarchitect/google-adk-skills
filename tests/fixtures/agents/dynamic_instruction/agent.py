"""An agent whose instruction is built per turn by an InstructionProvider.

The specification exists only at runtime, so there is nothing static to read
coverage out of - which is a different answer from "this instruction has no
rules in it".
"""

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext


def build_instruction(context: ReadonlyContext) -> str:
    """Compose the instruction from the caller's cost centre."""
    centre = context.state.get("cost_centre", "unknown")
    return f"Approve expense claims charged to {centre}. Never exceed its limit."


def approve_claim(claim_id: str) -> dict:
    """Approve a claim.

    Args:
        claim_id: The claim reference, e.g. CLM-000123.

    Returns:
        A dict with a status key.
    """
    return {"status": "approved", "claim_id": claim_id}


root_agent = Agent(
    name="dynamic_claims_agent",
    model="gemini-flash-latest",
    instruction=build_instruction,
    tools=[approve_claim],
)
