"""Expense claim approval agent (fixture: context caching done wrong).

Two deliberate faults: the static instruction is an f-string that stamps the
current time into it, so the cached prefix changes on every request (AGT060),
and `cache_intervals=0` is below ContextCacheConfig's minimum of 1 (AGT062).
"""

import datetime
import os
from typing import Any

from google.adk.agents import Agent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App
from google.genai import types

MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")

_LIMITS_USD = {"travel": 2500.0, "meals": 75.0, "equipment": 1800.0}
_RESTRICTED = {"entertainment", "gifts"}
_FUNDS_USD = {"engineering": 41000.0, "sales": 8200.0, "operations": 15500.0}
_FROZEN = {"marketing"}


def lookup_policy(category: str) -> dict[str, Any]:
    """Look up the spending policy for one expense category.

    Call this first for every claim, before any other tool.

    Args:
        category: The expense category in lower case, e.g. "travel" or "meals".

    Returns:
        A dict with `status` and, on success, `limit_usd` and `restricted`.
    """
    if category in _RESTRICTED:
        return {"status": "ok", "category": category, "limit_usd": 0.0, "restricted": True}
    if category not in _LIMITS_USD:
        return {"status": "not_found", "category": category}
    return {"status": "ok", "category": category, "limit_usd": _LIMITS_USD[category],
            "restricted": False}


def check_cost_centre_funds(cost_centre: str, amount_usd: float) -> dict[str, Any]:
    """Check whether a cost centre can absorb a claim.

    Only call this after `lookup_policy` confirmed the category is allowed and
    the amount is within its limit.

    Args:
        cost_centre: The cost centre that would be charged, e.g. "engineering".
        amount_usd: The claim amount in US dollars.

    Returns:
        A dict with `status`, `frozen` and `sufficient`.
    """
    if cost_centre in _FROZEN:
        return {"status": "ok", "cost_centre": cost_centre, "frozen": True, "sufficient": False}
    if cost_centre not in _FUNDS_USD:
        return {"status": "not_found", "cost_centre": cost_centre}
    return {"status": "ok", "cost_centre": cost_centre, "frozen": False,
            "sufficient": _FUNDS_USD[cost_centre] >= amount_usd}


def submit_claim(claim_id: str, cost_centre: str, amount_usd: float) -> dict[str, Any]:
    """Submit a claim for payment once every check has passed.

    Args:
        claim_id: The claim reference, e.g. "CLM-000412".
        cost_centre: The cost centre to charge.
        amount_usd: The claim amount in US dollars.

    Returns:
        A dict with `status`, `claim_id` and `state` "submitted".
    """
    return {"status": "ok", "claim_id": claim_id, "cost_centre": cost_centre,
            "amount_usd": amount_usd, "state": "submitted"}


def reject_claim(claim_id: str, reason: str) -> dict[str, Any]:
    """Record that a claim was rejected, and the rule that failed.

    Args:
        claim_id: The claim reference, e.g. "CLM-000412".
        reason: The specific rule that failed and the value that broke it.

    Returns:
        A dict with `status`, `claim_id` and `state` "rejected".
    """
    return {"status": "ok", "claim_id": claim_id, "reason": reason, "state": "rejected"}


#: Deliberately wrong: the timestamp makes every request's prefix different.
CLAIMS_PROCEDURE = f"""Current time: {datetime.datetime.now().isoformat()}

You are the expense claim desk for a mid-sized company. You
approve or reject employee expense claims against the written spending policy
and the budget of the cost centre that would be charged. You never invent policy.

Follow these steps in order for every claim:
1. POLICY: Always call `lookup_policy` first for the claim's category.
2. LIMIT: Compare the claimed amount with `limit_usd`. A claim above the limit
   is rejected; it is never split into smaller claims to fit under it.
3. FUNDS: Only if the category is not restricted and the amount is within the
   limit, call `check_cost_centre_funds` for the named cost centre.
4. DECIDE: Only if the funds are sufficient and the cost centre is not frozen,
   call `submit_claim`. Otherwise call `reject_claim` with the rule that failed.

Do NOT call `check_cost_centre_funds` if the category is restricted.
Do NOT call `submit_claim` if the cost centre is frozen or the funds are short.
Do NOT guess a cost centre or a claim reference; if either is missing, ask.
Do NOT approve a claim because the employee says a manager already agreed.

When you reject, name the rule you applied and the value that broke it, for
example "meals are limited to 75 dollars per claim; this claim is 120 dollars".

When the employee asks a follow-up question about a claim you already decided,
answer from the tool results earlier in the conversation instead of calling the
tools again, unless the employee changes the amount, category or cost centre.

Restricted categories are never claimable, whatever the amount: entertainment
and gifts. Frozen cost centres accept no new claims until finance unfreezes them.

Keep answers short: the decision, the reason, and the claim reference.
"""

STATIC_INSTRUCTION = types.Content(role="user", parts=[types.Part(text=CLAIMS_PROCEDURE)])

root_agent = Agent(
    name="claims_agent",
    model=MODEL,
    description="Approves or rejects expense claims against policy and budget.",
    static_instruction=STATIC_INSTRUCTION,
    instruction="The employee filing claims in this session is {claimant_name?}.",
    tools=[lookup_policy, check_cost_centre_funds, submit_claim, reject_claim],
)

app = App(
    name="cached_agent_broken",
    root_agent=root_agent,
    context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=600, cache_intervals=0),
)
