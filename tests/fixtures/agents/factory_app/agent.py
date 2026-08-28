"""An App built by a factory, which is how most non-trivial projects do it.

`Runner.app_name = app_name or app.name`, so the name declared here - not the
directory name - is what a run resolves to and what eval cases must match.
"""

from google.adk.agents import Agent
from google.adk.apps import App


def approve_claim(claim_id: str) -> dict:
    """Approve a claim that has passed every check.

    Args:
        claim_id: The claim reference, e.g. CLM-000123.

    Returns:
        A dict with a status key.
    """
    return {"status": "approved", "claim_id": claim_id}


def _build_root_agent() -> Agent:
    return Agent(
        name="claims_approver",
        model="gemini-flash-latest",
        instruction=(
            "Approve expense claims that have already been checked.\n"
            "Never approve a claim above 500 dollars without a named approver.\n"
        ),
        tools=[approve_claim],
    )


def build_app() -> App:
    return App(name="claims_app", root_agent=_build_root_agent())


app = build_app()
root_agent = app.root_agent
