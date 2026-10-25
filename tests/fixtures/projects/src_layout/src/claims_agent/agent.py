"""Expense claim agent (fixture: an agents-cli src layout).

`src/__init__.py` re-exports `root_agent`, so `src` itself is loadable, but the
agent package is `src/claims_agent` (named by `agents-cli-manifest.yaml`) and
the App is named "claims". Its eval cases are agents-cli datasets under
`tests/eval/datasets/`, not ADK evalsets.
"""

import os
from typing import Any

from google.adk.agents import Agent
from google.adk.apps import App

_LIMITS_USD = {"travel": 2500.0, "meals": 75.0, "equipment": 1800.0}


def lookup_policy(category: str) -> dict[str, Any]:
    """Look up the spending limit for one expense category.

    Call this first for every claim.

    Args:
        category: The expense category in lower case, e.g. "travel".

    Returns:
        A dict with `status` and, on success, `category` and `limit_usd`.
    """
    if category not in _LIMITS_USD:
        return {"status": "not_found", "category": category}
    return {"status": "ok", "category": category, "limit_usd": _LIMITS_USD[category]}


INSTRUCTION = """You check expense claims against the spending policy.

Always call `lookup_policy` first for the claim's category. If the amount is
above the limit, say which limit it exceeds. Do NOT approve a claim yourself.
"""

root_agent = Agent(
    name="claims_agent",
    model=os.getenv("MODEL_NAME", "gemini-flash-latest"),
    description="Checks expense claims against the spending policy.",
    instruction=INSTRUCTION,
    tools=[lookup_policy],
)

app = App(name="claims", root_agent=root_agent)
