"""An agent whose tools arrive by every binding shape ADK accepts.

Nothing here is exotic: each shape below was taken from a real ADK project.
The point of the fixture is that a static reviewer must read all of them, or it
reports tools that do not exist and misses tools that do.
"""

from google.adk.agents import Agent, LlmAgent
from google.adk.tools import FunctionTool, LongRunningFunctionTool, google_search
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from pydantic import BaseModel, Field

from . import constants


class PolicyDecision(BaseModel):
    """What the policy lookup concluded."""

    category: str = Field(description="The expense category that was checked.")
    limit_usd: float = Field(ge=0.0, description="Per-claim limit for the category.")
    restricted: bool = Field(default=False, description="True when claims are refused outright.")


def lookup_policy(category: str) -> PolicyDecision:
    """Look up the expense policy for one category.

    Args:
        category: The expense category, e.g. "meals" or "equipment".

    Returns:
        The limit and whether the category is restricted.
    """
    return PolicyDecision(category=category, limit_usd=100.0)


def check_condition_and_escalate(cost_centre: str, amount_usd: float) -> dict:
    """Decide whether a claim needs a human approver.

    Args:
        cost_centre: The cost centre the claim is charged to.
        amount_usd: The claim amount in US dollars.

    Returns:
        A dict with a status key and the approver, when one is needed.
    """
    return {"status": "ok", "approver": None}


def submit_claim(claim_id: str, cost_centre: str, amount_usd: float) -> dict:
    """Submit an approved claim for payment.

    Args:
        claim_id: The claim reference, e.g. CLM-000123.
        cost_centre: The cost centre the claim is charged to.
        amount_usd: The claim amount in US dollars.

    Returns:
        A dict with a status key.
    """
    return {"status": "submitted", "claim_id": claim_id}


def archive_claim(claim_id: str, **options) -> dict:
    """Archive a settled claim.

    Args:
        claim_id: The claim reference to archive.
        **options: Forwarded to the archive backend.

    Returns:
        A dict with a status key.
    """
    return {"status": "archived", "claim_id": claim_id}


def reconcile_ledger(claim_id: str) -> dict:
    """Reconcile a claim against the ledger.

    Args:
        claim_id: The claim reference to reconcile.

    Returns:
        A dict with a status key.
    """
    return {"status": "reconciled"}


class LedgerClient:
    """A client whose method shares a module function's name on purpose."""

    def reconcile_ledger(self, claim_id: str) -> dict:
        """Not a tool: reached through an instance, never registered by name."""
        return {"status": "ignored"}


# A wrapper assigned to a name. The trajectory carries `check_condition_and_escalate`,
# never `CHECK_CONDITION_TOOL`.
CHECK_CONDITION_TOOL = FunctionTool(func=check_condition_and_escalate)
SKILLS_TOOLSET = McpToolset(connection_params=None)

BASE_TOOLS = [lookup_policy, CHECK_CONDITION_TOOL]

research_agent = LlmAgent(
    name="policy_researcher",
    model="gemini-flash-latest",
    instruction="Find the policy paragraph that applies and quote it.",
    tools=[google_search],
)

root_agent = Agent(
    name=constants.AGENT_NAME,
    model="gemini-flash-latest",
    instruction=(
        "Route expense claims.\n"
        "Always call lookup_policy first.\n"
        "Never call submit_claim for a restricted category.\n"
    ),
    tools=[
        *BASE_TOOLS,
        FunctionTool(submit_claim),
        LongRunningFunctionTool(reconcile_ledger),
        SKILLS_TOOLSET,
        google_search,
        AgentTool(agent=research_agent),
        archive_claim,
    ],
)
