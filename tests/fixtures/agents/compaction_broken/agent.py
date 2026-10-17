"""Expense claim agent (fixture: context engineering done wrong).

* `EventsCompactionConfig(compaction_interval=0, overlap_size=-1)`: both out of
  bounds, so building the App raises (AGT076);
* `policy_desk` is a transfer target of an LlmAgent and keeps
  include_contents='default', so it reads the whole history (AGT078);
* `policy_handbook` returns a 2,356-character string literal into the chat
  history (AGT079).
"""

from typing import Any

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.apps.app import EventsCompactionConfig

HANDBOOK = "Travel, meals and equipment claims follow the handbook. " * 43


def policy_handbook(section: str) -> str:
    """Return the expense handbook text for one section.

    Args:
        section: The handbook section, e.g. "travel".

    Returns:
        The handbook text.
    """
    return "Travel, meals and equipment claims follow the handbook, section by section. " * 31


def lookup_limit(category: str) -> dict[str, Any]:
    """Look up the per-claim limit for a category.

    Args:
        category: The expense category, lower case.

    Returns:
        A dict with `status` and `limit_usd`.
    """
    return {"status": "ok", "limit_usd": {"travel": 2500.0, "meals": 75.0}.get(category, 0.0)}


policy_desk = Agent(
    name="policy_desk",
    model="gemini-flash-latest",
    instruction="Answer policy questions with policy_handbook. Never approve a claim.",
    tools=[policy_handbook],
)

root_agent = Agent(
    name="claims_router",
    model="gemini-flash-latest",
    instruction=(
        "Look up the limit with lookup_limit for every claim. If the user asks a policy "
        "question, transfer to policy_desk."
    ),
    tools=[lookup_limit],
    sub_agents=[policy_desk],
)

app = App(
    name="compaction_broken",
    root_agent=root_agent,
    events_compaction_config=EventsCompactionConfig(compaction_interval=0, overlap_size=-1),
)
