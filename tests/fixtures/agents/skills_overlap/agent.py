"""Expense claim agent (fixture: two skills that describe themselves alike).

`claim-policy` covers travel, meals and equipment; `claim-policy-hotel` covers
hotel nights. Their procedures differ, but their descriptions use almost the
same words (Jaccard 0.93, containment 1.00), and the model chooses a skill from
the name and description alone, so a hotel claim can load the general policy
and a meals claim the hotel one (AGT083). Loads on ADK 2.9.2 and 2.13.0.
"""

import os
import pathlib
from typing import Any

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.skills import load_skills_from_dir
from google.adk.tools.skill_toolset import SkillToolset

MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")


def submit_claim(claim_id: str, amount_usd: float) -> dict[str, Any]:
    """Submit a claim for payment.

    Only call this when the matching claim-policy skill says the claim is
    within policy.

    Args:
        claim_id: The claim reference, e.g. CLM-000123.
        amount_usd: The claim amount in US dollars.

    Returns:
        A dict with `status` "submitted" and the `claim_id`.
    """
    return {"status": "submitted", "claim_id": claim_id, "amount_usd": amount_usd}


policy_skills = SkillToolset(
    skills=load_skills_from_dir(pathlib.Path(__file__).parent / "skills"))

root_agent = Agent(
    name="overlap_claims_agent",
    model=MODEL,
    description="Approves or returns expense claims using the claim policy skills.",
    instruction=(
        "You process expense claims. Load the policy skill that fits the claim and "
        "follow it. Never call submit_claim unless the skill says the claim is within "
        "policy. If the claim id or the amount is missing, ask for it instead of guessing."
    ),
    tools=[policy_skills, submit_claim],
)

app = App(name="skills_overlap", root_agent=root_agent)
