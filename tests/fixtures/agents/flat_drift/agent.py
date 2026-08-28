"""Fixture: the same expense-claim agent with deliberate ADK 2.x problems.

Each problem here exists to trip exactly one review rule. Do not "fix" this file.
"""

import time

import requests
from google.adk.agents import Agent, SequentialAgent
from google.genai import types


def lookup_policy(category):
    """Look it up."""
    time.sleep(1)
    requests.get("https://example.invalid/policy")
    if not category:
        raise ValueError("category is required")
    return "travel: 2500"


def submit_claim(claim_id: str) -> dict:
    """Submit a claim.

    Args:
        claim_id: The claim reference.

    Returns:
        A dict describing the outcome.
    """
    return {"claim": claim_id, "state": "submitted"}


approver = Agent(
    name="user",
    model="gemini-2.0-flash",
    temperature=0.4,
    instruction="Approve claims.",
    global_instruction="Be concise.",
    tools=[lookup_policy, submit_claim],
    generate_content_config=types.GenerateContentConfig(
        system_instruction="You approve claims.",
    ),
)

root_agent = SequentialAgent(name="pipeline", sub_agents=[approver, approver])


def remember(session, value):
    """Fixture: a state write that never persists."""
    session.state["last_claim"] = value
