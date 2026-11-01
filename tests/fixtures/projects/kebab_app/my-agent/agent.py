"""Expense claim policy agent (fixture: an app directory name AgentLoader treats specially)."""

import os

from google.adk.agents import Agent


def lookup_policy(category: str) -> dict:
    """Return the spending limit for an expense category.

    Args:
        category: The expense category, lower case (travel, meals, equipment).

    Returns:
        {"status": "ok", "limit_usd": float} or {"status": "error", "error": str}.
    """
    limits = {"travel": 2500.0, "meals": 75.0, "equipment": 1800.0}
    if category not in limits:
        return {"status": "error", "error": f"unknown category {category!r}"}
    return {"status": "ok", "limit_usd": limits[category]}


root_agent = Agent(
    name="policy_agent",
    model=os.getenv("MODEL_NAME", "gemini-flash-latest"),
    description="Answers expense policy questions.",
    instruction=(
        "You answer questions about expense claim limits. Call lookup_policy with the "
        "category before you state a limit. If the category is unknown, say so and list "
        "travel, meals and equipment."
    ),
    tools=[lookup_policy],
)
