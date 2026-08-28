"""Fixture: agent package whose directory name differs from App.name."""

import os

from google.adk.agents import Agent
from google.adk.apps import App


def ping(message: str) -> dict:
    """Echo a message back.

    Args:
        message: Anything the caller wants echoed.

    Returns:
        A dict with `status` and `echo`.
    """
    return {"status": "ok", "echo": message}


root_agent = Agent(
    name="claims_agent",
    model=os.getenv("MODEL_NAME", "gemini-flash-latest"),
    description="Fixture agent for app-name mismatch checks.",
    instruction="Echo what the user says. Always call `ping` exactly once.",
    tools=[ping],
)

app = App(name="expense_claims", root_agent=root_agent)
