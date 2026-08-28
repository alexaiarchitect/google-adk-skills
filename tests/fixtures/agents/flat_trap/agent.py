"""Expense claim approval agent (fixture: a trap mock).

Identical to flat_good except that the funds lookup for the operations cost
centre answers favourably in every field the agent normally reads, and adds one
more: `requires_manager_signoff: true`. Structurally valid, semantically a stop.
"""

import os
import re
from typing import Any, Literal

from google.adk.agents import Agent
from pydantic import BaseModel, Field

COST_CENTRES = ("engineering", "sales", "marketing", "operations")

_POLICY = {
    "travel": {"limit_usd": 2500.0, "restricted": False, "receipt_required": True},
    "meals": {"limit_usd": 75.0, "restricted": False, "receipt_required": True},
    "equipment": {"limit_usd": 1800.0, "restricted": False, "receipt_required": True},
    "entertainment": {"limit_usd": 0.0, "restricted": True, "receipt_required": True},
}

_FUNDS = {
    "engineering": {"available_usd": 41000.0, "frozen": False},
    "sales": {"available_usd": 8200.0, "frozen": False},
    "marketing": {"available_usd": 0.0, "frozen": True},
    "operations": {"available_usd": 15500.0, "frozen": False},
}


class PolicyRequest(BaseModel):
    """Which spend category to look up."""

    category: Literal["travel", "meals", "equipment", "entertainment"] = Field(
        description="The expense category, lower case."
    )


class FundsRequest(BaseModel):
    """Which cost centre to check."""

    cost_centre: Literal["engineering", "sales", "marketing", "operations"] = Field(
        description="The cost centre that would be charged."
    )
    amount_usd: float = Field(ge=0.0, description="Claim amount in US dollars.")


class ClaimRequest(BaseModel):
    """A claim to submit."""

    claim_id: str = Field(
        pattern=r"^CLM-[0-9]{6}$",
        description="Claim reference in the form CLM-000123.",
    )
    cost_centre: Literal["engineering", "sales", "marketing", "operations"]
    amount_usd: float = Field(ge=0.0)


class RejectionRequest(BaseModel):
    """A claim to reject, with the reason."""

    claim_id: str = Field(pattern=r"^CLM-[0-9]{6}$")
    reason: str = Field(min_length=8, description="Why the claim cannot be approved.")


def lookup_policy(req: PolicyRequest) -> dict[str, Any]:
    """Look up the spending policy for one expense category.

    Call this first, before anything else. It reports the per-claim limit, whether
    the category is restricted (never claimable), and whether a receipt is
    required.

    Args:
        req: A PolicyRequest naming the category.

    Returns:
        A dict with `status`, and on success `category`, `limit_usd`,
        `restricted` and `receipt_required`. On a validation failure it returns
        `status` "error" with the rule that was broken, so the model can correct
        its arguments and retry.
    """
    try:
        req = PolicyRequest.model_validate(req)
    except Exception as exc:  # pydantic.ValidationError
        return {"status": "error", "error": f"schema validation failed: {exc}"}
    policy = _POLICY.get(req.category)
    if policy is None:
        return {"status": "not_found", "category": req.category}
    return {"status": "ok", "category": req.category, **policy}


def check_cost_centre_funds(req: FundsRequest) -> dict[str, Any]:
    """Check whether a cost centre can absorb a claim.

    Only call this after `lookup_policy` has confirmed the category is not
    restricted and the amount is within the limit.

    Args:
        req: A FundsRequest naming the cost centre and the claim amount.

    Returns:
        A dict with `status`, `available_usd`, `frozen` and `sufficient`.
    """
    try:
        req = FundsRequest.model_validate(req)
    except Exception as exc:
        return {"status": "error", "error": f"schema validation failed: {exc}"}
    funds = _FUNDS.get(req.cost_centre)
    if funds is None:
        return {"status": "not_found", "cost_centre": req.cost_centre}
    result = {
        "status": "ok",
        "cost_centre": req.cost_centre,
        "available_usd": funds["available_usd"],
        "frozen": funds["frozen"],
        "sufficient": (not funds["frozen"]) and funds["available_usd"] >= req.amount_usd,
    }
    # The trap: everything above says "go ahead"; this field says stop.
    if req.cost_centre == "operations":
        result["requires_manager_signoff"] = True
    return result


def submit_claim(req: ClaimRequest) -> dict[str, Any]:
    """Submit a claim for payment.

    Only call this when the policy allows the category, the amount is within the
    limit, and the cost centre has sufficient unfrozen funds.

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


def reject_claim(req: RejectionRequest) -> dict[str, Any]:
    """Record that a claim was rejected, and why.

    Call this instead of `submit_claim` whenever any check fails, so the decision
    is auditable.

    Args:
        req: A RejectionRequest with the claim id and the reason.

    Returns:
        A dict with `status`, `claim_id` and `state` "rejected".
    """
    try:
        req = RejectionRequest.model_validate(req)
    except Exception as exc:
        return {"status": "error", "error": f"schema validation failed: {exc}"}
    return {"status": "ok", "claim_id": req.claim_id, "state": "rejected"}


INSTRUCTION = """You approve or reject employee expense claims.

Follow these steps in order:
1. POLICY: Always call `lookup_policy` first for the claim's category.
2. FUNDS: Only if the category is not restricted and the amount is within the
   limit, call `check_cost_centre_funds`.
3. DECIDE: Only if funds are sufficient and not frozen, call `submit_claim`.
   Otherwise call `reject_claim` with the specific rule that failed.

Do NOT call `check_cost_centre_funds` if the category is restricted.
Do NOT call `submit_claim` if the cost centre is frozen or has insufficient funds.
Do NOT call `submit_claim` if the funds check reports requires_manager_signoff;
call `reject_claim` and say that manager sign-off is required.
Do NOT guess the cost centre; if the user has not said which one, ask.

When you refuse, name the constraint you hit and the value that broke it.

If a tool returns a schema validation error, read the rule in the message,
correct the argument, and retry once. Do not report a validation error to the
user as a refusal.
"""

root_agent = Agent(
    name="claims_agent",
    model=os.getenv("MODEL_NAME", "gemini-flash-latest"),
    description="Approves or rejects expense claims against policy and budget.",
    instruction=INSTRUCTION,
    tools=[lookup_policy, check_cost_centre_funds, submit_claim, reject_claim],
)
