"""Expense claim agent (fixture: an output cap too small for a thinking model).

`max_output_tokens=300` was meant to keep replies short, but on a Gemini 2.5+
or 3.x model (here the `gemini-flash-latest` default) the cap also has to hold
the model's thinking, so a claim that needs several checks runs out mid-
reasoning (AGT084). Loads on ADK 2.9.2 and 2.13.0.
"""

import os
from typing import Any

from google.adk.agents import Agent
from google.adk.apps import App
from google.genai import types

MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")

_LIMITS_USD = {"travel": 1500.0, "meals": 60.0, "equipment": 400.0}


def check_claim_limit(category: str, amount_usd: float) -> dict[str, Any]:
    """Check a claim amount against its category's per-claim limit.

    Call this before submit_claim for every claim.

    Args:
        category: travel, meals or equipment, lower case.
        amount_usd: The claim amount in US dollars.

    Returns:
        A dict with `status` and, when the category exists, `limit_usd` and `within_limit`.
    """
    limit = _LIMITS_USD.get(category.lower())
    if limit is None:
        return {"status": "unknown_category", "category": category}
    return {"status": "ok", "limit_usd": limit, "within_limit": amount_usd <= limit}


def submit_claim(claim_id: str, amount_usd: float) -> dict[str, Any]:
    """Submit a claim for payment.

    Only call this when check_claim_limit reports the claim is within its limit.

    Args:
        claim_id: The claim reference, e.g. CLM-000123.
        amount_usd: The claim amount in US dollars.

    Returns:
        A dict with `status` "submitted" and the `claim_id`.
    """
    return {"status": "submitted", "claim_id": claim_id, "amount_usd": amount_usd}


root_agent = Agent(
    name="capped_claims_agent",
    model=MODEL,
    description="Checks an expense claim against its limit and submits it.",
    instruction=(
        "You process expense claims. Call check_claim_limit first. Never call "
        "submit_claim unless the claim is within its limit; otherwise return it and "
        "quote the limit. If the claim id, category or amount is missing, ask for it. "
        "Answer in two sentences at most."
    ),
    tools=[check_claim_limit, submit_claim],
    generate_content_config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=300),
)

app = App(name="low_token_cap", root_agent=root_agent)
