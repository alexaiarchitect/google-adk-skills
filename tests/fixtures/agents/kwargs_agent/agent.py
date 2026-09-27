"""Expense claim approval agent (fixture: construction hidden from the reader).

Nothing here is wrong, and nothing here is readable from the syntax tree. The
agent is built inside a class from a kwargs dict assembled at runtime, and the
export is an attribute of the instance:

    self._agent = Agent(**agent_kwargs)
    ...
    root_agent = _claims_app._agent

So `name`, `model`, `instruction` and `tools` are all unknown rather than absent,
and a tool the module defines may or may not be bound on any given run. This
fixture exists so the reviewers say that out loud (AGT055) instead of reporting a
model of None, an instruction of 0 characters and no tools as if those were facts.
"""

import os
from typing import Any, Literal

from google.adk.agents import Agent
from google.adk.apps import App
from pydantic import BaseModel, Field

#: Set in the environment of a deployment that has the ledger integration.
ENABLE_LEDGER_EXPORT = (os.getenv("ENABLE_LEDGER_EXPORT") or "").lower() == "true"


class PolicyRequest(BaseModel):
    """Which spend category to look up."""

    category: Literal["travel", "meals", "equipment", "entertainment"] = Field(
        description="The expense category, lower case."
    )


class ClaimRequest(BaseModel):
    """A claim to submit."""

    claim_id: str = Field(pattern=r"^CLM-[0-9]{6}$", description="Claim reference.")
    cost_centre: Literal["engineering", "sales", "marketing", "operations"]
    amount_usd: float = Field(ge=0.0)


def lookup_policy(req: PolicyRequest) -> dict[str, Any]:
    """Look up the spending policy for one expense category.

    Args:
        req: A PolicyRequest naming the category.

    Returns:
        A dict with `status`, and on success `category`, `limit_usd` and
        `restricted`. A validation failure returns `status` "error" with the rule
        that was broken, so the model can correct the argument and retry.
    """
    try:
        req = PolicyRequest.model_validate(req)
    except Exception as exc:  # pydantic.ValidationError
        return {"status": "error", "error": f"schema validation failed: {exc}"}
    limits = {"travel": 2500.0, "meals": 75.0, "equipment": 1800.0, "entertainment": 0.0}
    return {
        "status": "ok",
        "category": req.category,
        "limit_usd": limits[req.category],
        "restricted": req.category == "entertainment",
    }


def submit_claim(req: ClaimRequest) -> dict[str, Any]:
    """Submit a claim for payment.

    Args:
        req: A ClaimRequest with the claim id, cost centre and amount.

    Returns:
        A dict with `status` and, on success, `claim_id` and `state` "submitted".
    """
    try:
        req = ClaimRequest.model_validate(req)
    except Exception as exc:
        return {"status": "error", "error": f"schema validation failed: {exc}"}
    return {"status": "ok", "claim_id": req.claim_id, "state": "submitted"}


def export_to_ledger(req: ClaimRequest) -> dict[str, Any]:
    """Post a submitted claim to the finance ledger.

    Bound only where the ledger integration is enabled, which is why a trajectory
    naming it cannot be judged from the source alone.

    Args:
        req: A ClaimRequest for a claim that has already been submitted.

    Returns:
        A dict with `status` and, on success, the `ledger_ref` it was posted under.
    """
    try:
        req = ClaimRequest.model_validate(req)
    except Exception as exc:
        return {"status": "error", "error": f"schema validation failed: {exc}"}
    return {"status": "ok", "claim_id": req.claim_id, "ledger_ref": f"LEDG-{req.claim_id[4:]}"}


INSTRUCTION = """You approve or reject employee expense claims.

1. POLICY: call `lookup_policy` first for the claim's category.
2. DECIDE: only if the category is not restricted and the amount is within the
   limit, call `submit_claim`. Otherwise refuse and name the rule that failed.

Do NOT guess the cost centre; if the user has not said which one, ask.
"""


class ClaimsApp:
    """Builds the agent from a kwargs dict, the way a deployment wrapper does."""

    def __init__(self) -> None:
        claim_tools = [lookup_policy, submit_claim]
        if ENABLE_LEDGER_EXPORT:
            claim_tools.append(export_to_ledger)

        agent_kwargs = {
            "name": "claims_agent",
            "model": os.getenv("MODEL_NAME", "gemini-flash-latest"),
            "description": "Approves or rejects expense claims against policy.",
            "instruction": INSTRUCTION,
            "tools": claim_tools,
        }
        self._agent = Agent(**agent_kwargs)


_claims_app = ClaimsApp()
root_agent = _claims_app._agent

app = App(root_agent=root_agent, name="kwargs_agent")
