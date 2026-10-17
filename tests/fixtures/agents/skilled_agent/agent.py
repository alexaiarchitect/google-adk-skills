"""Expense claim agent (fixture: skills done right).

The claim procedure lives in two skills instead of the instruction: an inline
`receipt-check` skill and a file-based `claim-policy` skill whose limits sit in
`references/`, loaded only when the model asks for them. The instruction says
when to load each one; the skills say how. Clean under AGT070-AGT079, loads on
ADK 2.9.2 and 2.12.0.
"""

import os
import pathlib
from typing import Any

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.skills import load_skill_from_dir, models
from google.adk.tools.skill_toolset import SkillToolset

MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")

_FUNDS_USD = {"engineering": 41000.0, "sales": 8200.0, "operations": 15500.0}


def check_cost_centre_funds(cost_centre: str, amount_usd: float) -> dict[str, Any]:
    """Check whether a cost centre can absorb a claim.

    Call this only after the claim-policy skill has confirmed the claim is
    within policy.

    Args:
        cost_centre: The cost centre to charge, lower case.
        amount_usd: The claim amount in US dollars.

    Returns:
        A dict with `status` and, on success, `available_usd` and `sufficient`.
    """
    available = _FUNDS_USD.get(cost_centre.lower())
    if available is None:
        return {"status": "not_found", "cost_centre": cost_centre}
    return {"status": "ok", "available_usd": available, "sufficient": available >= amount_usd}


def submit_claim(claim_id: str, cost_centre: str, amount_usd: float) -> dict[str, Any]:
    """Submit a claim for payment.

    Only call this when the claim is within policy, has a receipt, and the cost
    centre has sufficient funds.

    Args:
        claim_id: The claim reference, e.g. CLM-000123.
        cost_centre: The cost centre to charge.
        amount_usd: The claim amount in US dollars.

    Returns:
        A dict with `status` "submitted" and the `claim_id`.
    """
    return {"status": "submitted", "claim_id": claim_id, "cost_centre": cost_centre,
            "amount_usd": amount_usd}


receipt_check = models.Skill(
    frontmatter=models.Frontmatter(
        name="receipt-check",
        description=(
            "Checks that an expense claim has a readable receipt whose date, merchant "
            "and amount match the claim. Use before submitting any claim over 25 dollars."
        ),
    ),
    instructions=(
        "1. Ask for the receipt if the claim does not mention one.\n"
        "2. Compare the receipt amount with the claimed amount; a difference over one "
        "dollar means the claim is returned to the claimant.\n"
        "3. Report which of date, merchant and amount matched."
    ),
)

claim_policy = load_skill_from_dir(pathlib.Path(__file__).parent / "skills" / "claim-policy")

claim_skills = SkillToolset(skills=[receipt_check, claim_policy])

root_agent = Agent(
    name="skilled_claims_agent",
    model=MODEL,
    description="Approves or returns expense claims using the claim-policy and receipt-check skills.",
    instruction=(
        "You process expense claims. For every claim, load the claim-policy skill first "
        "and follow it. If the claim is over 25 dollars, load the receipt-check skill. "
        "Never call submit_claim unless the policy allows the claim and "
        "check_cost_centre_funds reports sufficient funds. If the claim id, the amount "
        "or the cost centre is missing, ask for it instead of guessing."
    ),
    tools=[claim_skills, check_cost_centre_funds, submit_claim],
)

app = App(name="skilled_agent", root_agent=root_agent)
